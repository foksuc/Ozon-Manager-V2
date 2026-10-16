"""Portable closed START/STOP runner: no shell, global package config or PID killing."""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.cache/lifecycle/server.json'
URL = 'http://127.0.0.1:8000'

def confined(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_relative_to(ROOT): raise ValueError('External path denied')
    for ancestor in (absolute, *absolute.parents):
        if ancestor.is_symlink() or (hasattr(ancestor, 'is_junction') and ancestor.is_junction()):
            raise ValueError('Reparse point denied')
    if not absolute.resolve().is_relative_to(ROOT.resolve()): raise ValueError('External target denied')
    return absolute

def preflight(action: str) -> None:
    if action not in {'start','stop'}: raise ValueError('Unknown action')
    if Path.cwd().resolve() != ROOT: raise ValueError('Wrong working directory')
    confined(ROOT)
    for name in ('frontend','backend','.cache','data','scripts'):
        base=confined(ROOT/name)
        if not base.exists(): continue
        # DirEntry retains Windows enumeration attributes. Path.lstat on every
        # file performed a second filesystem call for the same information.
        pending=[str(base)]
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    info=entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                        raise ValueError('Nested reparse point denied')
                    if stat.S_ISDIR(info.st_mode):pending.append(entry.path)
    for name in ('.npmrc','frontend/.npmrc'):
        if confined(ROOT/name).exists(): raise ValueError('Local npm override denied')
    for name in ('backend/requirements.lock.txt','frontend/package-lock.json'):
        if not confined(ROOT/name).is_file(): raise ValueError('Missing dependency lock')

def environment(parent: dict[str,str]) -> dict[str,str]:
    cache=ROOT/'.cache'; system=parent.get('SystemRoot',parent.get('SYSTEMROOT',r'C:\Windows'))
    node=cache/'runtimes/node'; python=cache/'runtimes/python'
    return {'SystemRoot':system,'WINDIR':system,'COMSPEC':str(Path(system)/'System32/cmd.exe'),
        'PATH':os.pathsep.join(map(str,(node,python,Path(system)/'System32'))),
        'TEMP':str(cache/'tmp'),'TMP':str(cache/'tmp'),
        'USERPROFILE':str(cache/'profile'),'HOME':str(cache/'profile'),
        'APPDATA':str(cache/'profile/AppData/Roaming'),'LOCALAPPDATA':str(cache/'profile/AppData/Local'),
        'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','PYTHONUTF8':'1',
        'PIP_CONFIG_FILE':os.devnull,'PIP_DISABLE_PIP_VERSION_CHECK':'1','PIP_CACHE_DIR':str(cache/'pip'),
        'npm_config_cache':str(cache/'npm'),'npm_config_userconfig':str(cache/'npm-userconfig'),
        'npm_config_globalconfig':str(cache/'npm-globalconfig'),'npm_config_prefix':str(cache/'npm-prefix'),
        'npm_config_update_notifier':'false','npm_config_audit':'false','npm_config_fund':'false',
        'PLAYWRIGHT_BROWSERS_PATH':str(cache/'browsers'),'OZON_V2_MODE':'LIVE_READ'}

def prepare() -> None:
    for name in ('.cache/lifecycle','.cache/tmp','.cache/pip','.cache/npm','.cache/npm-prefix',
        '.cache/profile/AppData/Roaming','.cache/profile/AppData/Local'):
        confined(ROOT/name).mkdir(parents=True,exist_ok=True)
    for name in ('.cache/npm-userconfig','.cache/npm-globalconfig'):
        confined(ROOT/name).write_text('',encoding='utf-8')

def dependency_fingerprint() -> str:
    digest=hashlib.sha256()
    for name in ('backend/requirements.lock.txt','frontend/package.json','frontend/package-lock.json'):
        digest.update(confined(ROOT/name).read_bytes())
    digest.update(b'python3.14.8-node24.9.0-isolated-v1')
    return digest.hexdigest()

def validate_node_lock() -> None:
    document=json.loads(confined(ROOT/'frontend/package-lock.json').read_text(encoding='utf-8'))
    if document.get('lockfileVersion')!=3 or not isinstance(document.get('packages'),dict):raise ValueError('Unsupported npm lock')
    for name,package in document['packages'].items():
        if name=='':continue
        if not name.startswith('node_modules/') or '..' in Path(name).parts:raise ValueError('External package path denied')
        if not isinstance(package,dict) or package.get('link'):raise ValueError('Linked dependency denied')
        if not str(package.get('resolved','')).startswith('https://registry.npmjs.org/'):
            raise ValueError('External package registry denied')
        if not re.fullmatch(r'sha512-[A-Za-z0-9+/]+={0,2}',str(package.get('integrity',''))):
            raise ValueError('Missing npm integrity')

def hashed_requirements() -> Path:
    """Pin wheel hashes from the trusted PyPI API before invoking pip; no source builds."""
    lines=[]
    for line in confined(ROOT/'backend/requirements.lock.txt').read_text().splitlines():
        if not line.strip(): continue
        match=re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)',line.strip())
        if not match: raise ValueError('Unpinned Python dependency')
        name,version=match.groups()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'https://pypi.org/pypi/{name}/{version}/json',timeout=30) as response:
            if response.geturl()!=f'https://pypi.org/pypi/{name}/{version}/json': raise ValueError('PyPI redirect denied')
            document=json.loads(response.read(4_000_000))
        hashes={row.get('digests',{}).get('sha256') for row in document.get('urls',[]) if row.get('packagetype')=='bdist_wheel'}
        if not hashes or any(not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value) for value in hashes):
            raise ValueError('Missing trusted wheel hashes')
        lines.append(line+' '+' '.join('--hash=sha256:'+value for value in sorted(hashes)))
    path=confined(ROOT/'.cache/lifecycle/requirements-hashed.txt')
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return path

def execute(argv: list[str], cwd: Path, env: dict[str,str]) -> None:
    preflight('start')
    subprocess.run(argv,cwd=confined(cwd),env=env,check=True,shell=False)

def tree_fingerprint(base: Path, *, excluded=()) -> str:
    """Hash names and contents, never secret directories or timestamp stamps."""
    base=confined(base)
    digest=hashlib.sha256()
    if not base.is_dir():return ''
    def denied(error):raise error
    for directory,dirs,files in os.walk(base,followlinks=False,onerror=denied):
        if Path(directory)==base:dirs[:]=[name for name in dirs if name not in excluded]
        for name in sorted(files):
            path=confined(Path(directory)/name)
            if base==ROOT/'frontend' and path.parent==base and name.endswith('.tsbuildinfo'):continue
            digest.update(path.relative_to(base).as_posix().encode('utf-8')+b'\0')
            with path.open('rb') as stream:digest.update(hashlib.file_digest(stream,'sha256').digest())
        dirs.sort()
    return digest.hexdigest()

def build_fingerprint() -> str:
    source=tree_fingerprint(ROOT/'frontend',excluded=('node_modules','dist','.cache','.vite'))
    return hashlib.sha256(('build-v1:'+dependency_fingerprint()+':'+source).encode()).hexdigest()

def build_current(fingerprint: str) -> bool:
    marker=confined(ROOT/'.cache/lifecycle/build.json')
    if not confined(ROOT/'frontend/dist/index.html').is_file() or not marker.is_file():return False
    try:
        if marker.stat().st_size>1024:return False
        cached=json.loads(marker.read_text(encoding='utf-8'))
        return isinstance(cached,dict) and cached.get('source')==fingerprint and cached.get('dist')==tree_fingerprint(ROOT/'frontend/dist')
    except (ValueError,OSError):return False

def install_and_build(env: dict[str,str]) -> None:
    print('[Ozon Manager] Checking installed dependencies...',flush=True)
    validate_node_lock()
    base=str(confined(ROOT/'.cache/runtimes/python/python.exe'))
    python=str(confined(ROOT/'backend/.venv/Scripts/python.exe'))
    node=str(confined(ROOT/'.cache/runtimes/node/node.exe'))
    npm=str(confined(ROOT/'.cache/runtimes/node/node_modules/npm/bin/npm-cli.js'))
    marker=confined(ROOT/'.cache/lifecycle/dependencies.json'); fingerprint=dependency_fingerprint()
    installed=False
    if marker.exists():
        try: installed=json.loads(marker.read_text()).get('fingerprint')==fingerprint
        except (ValueError,OSError): pass
    if not installed or not Path(python).is_file() or not (ROOT/'frontend/node_modules/vite/bin/vite.js').is_file():
        print('[Ozon Manager] Preparing the project Python environment...',flush=True)
        # Rebind even a copied/existing venv to this project's portable runtime.
        execute([base,'-I','-B','-m','venv',str(ROOT/'backend/.venv')],ROOT,env)
        print('[Ozon Manager] Fetching SHA256 hashes for pinned Python wheels...',flush=True)
        lock=hashed_requirements()
        print('[Ozon Manager] Installing pinned Python wheels...',flush=True)
        execute([python,'-I','-B','-m','pip','install','--isolated','--require-hashes','--only-binary=:all:',
            '--index-url','https://pypi.org/simple','--cache-dir',str(ROOT/'.cache/pip'),'-r',str(lock)],ROOT/'backend',env)
        print('[Ozon Manager] Installing locked frontend packages...',flush=True)
        execute([node,npm,'ci','--ignore-scripts','--no-audit','--no-fund','--registry=https://registry.npmjs.org',
            '--maxsockets=5','--fetch-retries=2','--fetch-retry-mintimeout=1000','--fetch-retry-maxtimeout=10000','--fetch-timeout=60000',
            '--cache='+str(ROOT/'.cache/npm'),'--userconfig='+str(ROOT/'.cache/npm-userconfig'),
            '--globalconfig='+str(ROOT/'.cache/npm-globalconfig')],ROOT/'frontend',env)
        marker.write_text(json.dumps({'fingerprint':fingerprint}),encoding='utf-8')
    else:
        print('[Ozon Manager] Dependencies are already installed.',flush=True)
    # Reuse only an intact output built from identical source/config/locks.
    fingerprint=build_fingerprint()
    if build_current(fingerprint):
        print('[Ozon Manager] Website is unchanged; using the verified build.',flush=True)
        return
    print('[Ozon Manager] Checking TypeScript and building the website...',flush=True)
    execute([node,str(ROOT/'frontend/node_modules/typescript/bin/tsc'),'--project','tsconfig.app.json','--noEmit'],ROOT/'frontend',env)
    execute([node,str(ROOT/'frontend/node_modules/typescript/bin/tsc'),'--project','tsconfig.node.json','--noEmit'],ROOT/'frontend',env)
    execute([node,str(ROOT/'frontend/node_modules/vite/bin/vite.js'),'build'],ROOT/'frontend',env)
    if not confined(ROOT/'frontend/dist/index.html').is_file():raise ValueError('Build did not produce index.html')
    # Do not register an output if inputs changed while the compiler was running.
    if build_fingerprint()!=fingerprint:raise ValueError('Frontend changed during build; run START again')
    marker=confined(ROOT/'.cache/lifecycle/build.json')
    temporary=confined(marker.with_suffix('.tmp'))
    temporary.write_text(json.dumps({'source':fingerprint,'dist':tree_fingerprint(ROOT/'frontend/dist')}),encoding='utf-8')
    os.replace(temporary,marker)

def identity(pid: int) -> dict | None:
    """PID reuse protection: executable plus Windows process creation FILETIME."""
    if os.name!='nt': return None
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.restype=ctypes.c_void_p
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return None
    try:
        created=ctypes.c_ulonglong(); exited=ctypes.c_ulonglong(); user=ctypes.c_ulonglong(); system=ctypes.c_ulonglong()
        if not kernel.GetProcessTimes(ctypes.c_void_p(handle),ctypes.byref(created),ctypes.byref(exited),ctypes.byref(system),ctypes.byref(user)):return None
        size=ctypes.c_ulong(32768); path=ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(ctypes.c_void_p(handle),0,path,ctypes.byref(size)):return None
        return {'pid':pid,'created':created.value,'executable':os.path.normcase(path.value)}
    finally:kernel.CloseHandle(ctypes.c_void_p(handle))

def state() -> dict | None:
    if not confined(STATE).exists():return None
    value=json.loads(STATE.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or not isinstance(value.get('pid'),int) or value['pid']<=0:return None
    if identity(value['pid'])!=value.get('identity'):return None
    if os.path.normcase(str(ROOT))!=value.get('root') or value.get('entry')!='scripts/lifecycle_server.py':return None
    if not isinstance(value.get('token'),str) or not re.fullmatch('[a-f0-9]{64}',value['token']):return None
    return value

def request(path: str, token: str, method: str='GET') -> dict:
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req=urllib.request.Request(URL+path,data=b'{}' if method=='POST' else None,method=method,
        headers={'Origin':URL,'X-Lifecycle-Token':token,'Content-Type':'application/json'})
    with opener.open(req,timeout=3) as response:
        if response.geturl()!=URL+path:raise ValueError('Local redirect denied')
        return json.loads(response.read(4096))

def stop() -> None:
    current=state()
    if current is None:
        print('Нет процесса, запущенного через START. Другие процессы не остановлены.');return
    response=request('/api/lifecycle/shutdown',current['token'],'POST')
    if response.get('status')!='stopping':raise ValueError('Shutdown was not acknowledged')
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        if identity(current['pid'])!=current['identity']:
            print('Ozon Manager выключен.');return
        time.sleep(.2)
    raise ValueError('Ожидание завершения истекло; принудительное убийство не выполняется')

def start() -> None:
    print('[Ozon Manager] Checking whether the website is already running...',flush=True)
    current=state()
    if current is not None:
        health=request('/api/lifecycle',current['token'])
        if health.get('instance')!=current['instance']:raise ValueError('Server instance mismatch')
        print('Ozon Manager уже запущен.');return
    with socket.socket() as probe:
        try:probe.bind(('127.0.0.1',8000))
        except OSError:raise ValueError('Порт8000 занят другим запуском. Завершите прежний запуск; чужие процессы не останавливаются.') from None
    prepare(); env=environment(dict(os.environ));install_and_build(env)
    print('[Ozon Manager] Starting the local server...',flush=True)
    # Do not persist raw application exception/response text containing seller data.
    process=subprocess.Popen([str(ROOT/'backend/.venv/Scripts/python.exe'),'-I','-B',str(ROOT/'scripts/lifecycle_server.py')],
        cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,shell=False)
    deadline=time.monotonic()+30
    print('[Ozon Manager] Waiting for the server health check...',flush=True)
    while time.monotonic()<deadline:
        if process.poll() is not None:raise ValueError('Сервер не запустился. Проверьте конфигурацию и отсутствие другого запуска.')
        current=state()
        if current is not None:
            try:
                health=request('/api/lifecycle',current['token'])
                if health.get('instance')==current['instance']:
                    print('Ozon Manager запущен: '+URL);return
            except OSError:pass
        time.sleep(.2)
    raise ValueError('Health check timeout; process was not force-killed')

@contextmanager
def launcher_lock():
    if os.name!='nt':yield;return
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.restype=ctypes.c_void_p
    name='Local\\OzonManager-'+hashlib.sha256(os.path.normcase(str(ROOT)).encode()).hexdigest()
    handle=kernel.CreateMutexW(None,False,name)
    if not handle:raise OSError('Cannot acquire launcher mutex')
    acquired=False
    try:
        result=kernel.WaitForSingleObject(ctypes.c_void_p(handle),30_000)
        if result not in {0,0x80}:raise ValueError('Другой START/STOP ещё выполняется')
        acquired=True
        yield
    finally:
        if acquired:kernel.ReleaseMutex(ctypes.c_void_p(handle))
        kernel.CloseHandle(ctypes.c_void_p(handle))

def run(action: str) -> None:
    preflight(action)
    with launcher_lock():
        if action=='start':start()
        else:stop()

if __name__=='__main__':
    try:
        if len(sys.argv)!=2:raise ValueError('Use start or stop without overrides')
        run(sys.argv[1])
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        print('Запуск остановлен: '+str(error),file=sys.stderr);sys.exit(1)
