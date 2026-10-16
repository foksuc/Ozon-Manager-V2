import importlib.util
import json
from pathlib import Path
import sys
import os
import subprocess
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.lifecycle import install_lifecycle

path=Path(__file__).resolve().parents[2]/'scripts/lifecycle.py'
spec=importlib.util.spec_from_file_location('lifecycle_runner_test',path)
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

@pytest.fixture
def root(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'ROOT',tmp_path)
    monkeypatch.setattr(runner,'STATE',tmp_path/'.cache/lifecycle/server.json')
    monkeypatch.chdir(tmp_path)
    (tmp_path/'backend').mkdir();(tmp_path/'frontend').mkdir()
    (tmp_path/'backend/requirements.lock.txt').write_text('httpx==0.28.1\n')
    (tmp_path/'frontend/package-lock.json').write_text('{}')
    (tmp_path/'frontend/package.json').write_text('{}')
    return tmp_path

def test_preflight_no_subprocess_on_bad_action(root,monkeypatch):
    child=Mock();monkeypatch.setattr(runner.subprocess,'run',child)
    with pytest.raises(ValueError):runner.run('start --prefix=C:\\external')
    child.assert_not_called()

def test_nested_symlink_denied_before_child(root,monkeypatch):
    external=root.parent/'outside';external.mkdir(exist_ok=True)
    link=root/'frontend/planted'
    try:link.symlink_to(external,target_is_directory=True)
    except OSError:
        if os.name!='nt':pytest.skip('symlink creation unavailable')
        # A real Windows junction needs no symlink privilege. Both paths are fixture paths.
        assert link.is_relative_to(root) and external.is_relative_to(root.parent)
        subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/cmd.exe'),'/d','/c','mklink','/J',str(link),str(external)],check=True,capture_output=True)
    child=Mock();monkeypatch.setattr(runner.subprocess,'run',child)
    try:
        with pytest.raises(ValueError):runner.run('start')
        child.assert_not_called()
    finally:
        if link.is_symlink():link.unlink()
        elif hasattr(link,'is_junction') and link.is_junction():link.rmdir()

@pytest.mark.parametrize('override',['.npmrc','frontend/.npmrc'])
def test_npm_override_denied(root,override):
    (root/override).write_text('cache=C:\\outside')
    with pytest.raises(ValueError):runner.preflight('start')

def test_confinement_outside(root):
    with pytest.raises(ValueError):runner.confined(root.parent/'external')

def test_environment_scrubs_overrides(root):
    env=runner.environment({'SystemRoot':r'C:\Windows','NODE_OPTIONS':'--require bad',
        'PIP_TARGET':r'C:\external','npm_config_prefix':r'C:\external','OZON_API_KEY':'secret','PATH':'evil'})
    assert 'NODE_OPTIONS' not in env and 'PIP_TARGET' not in env and 'OZON_API_KEY' not in env
    for key in ('TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA','npm_config_prefix','PIP_CACHE_DIR'):
        assert Path(env[key]).is_relative_to(root)
    assert env['OZON_V2_MODE']=='LIVE_READ'

def test_missing_lock_fail_closed(root):
    (root/'frontend/package-lock.json').unlink()
    with pytest.raises(ValueError):runner.preflight('start')

def test_fingerprint_changes_with_lock(root):
    before=runner.dependency_fingerprint();(root/'frontend/package-lock.json').write_text('{"changed":true}')
    assert before!=runner.dependency_fingerprint()

@pytest.mark.parametrize('package',[
    {'resolved':'file:C:/external','integrity':'sha512-AAAA'},
    {'resolved':'https://registry.npmjs.org/test/-/test.tgz','link':True,'integrity':'sha512-AAAA'},
    {'resolved':'https://registry.npmjs.org/test/-/test.tgz','integrity':'missing'},
])
def test_invalid_node_lock_rejected(root,package):
    (root/'frontend/package-lock.json').write_text(json.dumps({'lockfileVersion':3,'packages':{'node_modules/test':package}}))
    with pytest.raises(ValueError):runner.validate_node_lock()

def test_double_shutdown_never_calls_exit_twice():
    app,client,callback=make_app()
    assert client.post('/api/lifecycle/stop',headers=headers(app)).status_code==200
    assert client.post('/api/lifecycle/stop',headers=headers(app)).status_code==200
    callback.assert_called_once()

def test_state_pid_reuse_denied(root,monkeypatch):
    runner.prepare();runner.STATE.write_text(json.dumps({'pid':1,'identity':{'created':100}}))
    monkeypatch.setattr(runner,'identity',lambda pid:{'created':101})
    assert runner.state() is None

def test_stop_without_owned_process_never_calls_http(root,monkeypatch):
    request=Mock();monkeypatch.setattr(runner,'request',request)
    runner.stop();request.assert_not_called()

def test_isolated_runner_never_opens_user_browser(root,monkeypatch):
    # This used to create Windows INetCache junctions under the synthetic profile.
    import webbrowser
    opener=Mock(side_effect=AssertionError('An isolated child must not open the browser'))
    monkeypatch.setattr(webbrowser,'open',opener)
    monkeypatch.setattr(runner,'state',lambda:{'token':'synthetic','instance':'synthetic'})
    monkeypatch.setattr(runner,'request',lambda *args:{'instance':'synthetic'})
    runner.start()
    opener.assert_not_called()

def make_app(busy=False,managed=True):
    app=FastAPI();install_lifecycle(app,busy=lambda:busy)
    callback=Mock()
    if managed:app.state.lifecycle_exit=callback
    @app.post('/write')
    def write():return {'ok':True}
    return app,TestClient(app,client=('127.0.0.1',50000)),callback

def headers(app):return {'Origin':'http://127.0.0.1:8000','X-Lifecycle-Token':app.state.lifecycle_token}

def test_status_requires_origin_and_token():
    app,client,_=make_app()
    assert client.get('/api/lifecycle').status_code==403
    assert client.get('/api/lifecycle',headers={'Origin':'http://127.0.0.1:8000'}).status_code==403
    result=client.get('/api/lifecycle',headers=headers(app)).json()
    assert result['supported'] is True and 'token' not in result

def test_same_origin_browser_can_get_csrf_token():
    app,client,_=make_app()
    result=client.get('/api/lifecycle',headers={'Sec-Fetch-Site':'same-origin'}).json()
    assert result['token']==app.state.lifecycle_token

@pytest.mark.parametrize('evil',[
    {'Origin':'https://evil.example','Sec-Fetch-Site':'same-origin'},
    {'Origin':'http://127.0.0.1:8000','Sec-Fetch-Site':'cross-site'},
    {'Origin':'http://127.0.0.1:8000','X-Lifecycle-Token':'invalid'},
])
def test_shutdown_auth_failures(evil):
    app,client,callback=make_app()
    assert client.post('/api/lifecycle/shutdown',headers=evil).status_code==403
    callback.assert_not_called()

def test_unmanaged_server_never_exits():
    app,client,callback=make_app(managed=False)
    assert client.post('/api/lifecycle/shutdown',headers=headers(app)).status_code==409
    callback.assert_not_called()

def test_busy_server_never_exits():
    app,client,callback=make_app(busy=True)
    assert client.post('/api/lifecycle/shutdown',headers=headers(app)).status_code==409
    assert not app.state.lifecycle_closing
    callback.assert_not_called()

def test_acknowledged_graceful_shutdown_and_write_barrier():
    app,client,callback=make_app()
    result=client.post('/api/lifecycle/shutdown',headers=headers(app))
    assert result.status_code==200 and result.json()=={'status':'stopping'}
    callback.assert_called_once()
    assert client.post('/write').status_code==503

def test_nonloopback_denied():
    app,_,callback=make_app()
    client=TestClient(app,client=('192.0.2.3',1))
    assert client.post('/api/lifecycle/shutdown',headers=headers(app)).status_code==403
    callback.assert_not_called()
