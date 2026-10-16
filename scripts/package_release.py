"""Allowlisted source ZIP: never include credentials, local state or Git metadata."""
from pathlib import Path
import hashlib
import json
import os
import re
import zipfile

ROOT = Path(__file__).absolute().parent.parent
FILES = {'README.md', '.gitignore', 'START.bat', 'STOP.bat',
    'backend/requirements.in', 'backend/requirements.lock.txt',
    'backend/.env.example', 'backend/.performance.env.example',
    'frontend/index.html', 'frontend/package.json', 'frontend/package-lock.json',
    'frontend/tsconfig.json', 'frontend/tsconfig.app.json', 'frontend/tsconfig.node.json',
    'frontend/vite.config.ts', 'docs/DEVELOPMENT.md', 'docs/RELEASE_AUDIT.md'}
SCRIPTS = {'lifecycle.py', 'lifecycle.ps1', 'lifecycle_server.py', 'project.py',
    'audit_dependencies.py', 'package_release.py', 'backup_mock.py',
    'verify_read.py', 'verify_preview.py', 'verify_finance.py', 'finance_shape.py',
    'finance_fixture.py', 'i1_browser_fixture.py'}
PATTERNS = {
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'provider-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|sk-(?:proj-)?[A-Za-z0-9_-]{32,}|AKIA[A-Z0-9]{16})\b'),
    'jwt': re.compile(r'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\b'),
    'embedded-url-password': re.compile(r'https?://[^\s/:]+:[^\s/@]{8,}@'),
    'ozon-env-key': re.compile(r'(?m)^\s*OZON_API_KEY\s*=\s*[\"\x27]?(?!SYNTHETIC|MOCK|example|your_)[A-Za-z0-9_-]{24,}'),
}


def checked(path):
    path = Path(os.path.abspath(path))
    if not path.absolute().is_relative_to(ROOT):
        raise ValueError('External release path denied')
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError('Release reparse point denied')
    if not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('External release target denied')
    return path


def sources():
    selected = {checked(ROOT / name) for name in FILES}
    for folder, extensions in [('backend/app', {'.py', '.json'}), ('backend/tests', {'.py'}),
            ('frontend/src', {'.ts', '.tsx', '.css'}),
            ('frontend/public', {'.svg', '.ttf', '.txt'}),
            ('docs/media', {'.png', '.jpg', '.gif', '.svg'})]:
        for path in checked(ROOT / folder).rglob('*'):
            checked(path)
            if path.is_file() and path.suffix in extensions:
                selected.add(path)
    for path in checked(ROOT / 'scripts').iterdir():
        if path.name in SCRIPTS or (path.name.startswith('test_') and path.suffix in {'.py', '.mjs'}):
            selected.add(checked(path))
    for path in selected:
        if not path.is_file():
            raise ValueError('Missing release source: ' + path.relative_to(ROOT).as_posix())
    return sorted(selected)


def scan(entries):
    """Report file/line/rule only; never log matched secret material."""
    findings = []
    for name, content in entries:
        if Path(name).suffix.lower() in {'.jpg', '.png', '.gif', '.ttf'}:
            continue
        text = content.decode('utf-8-sig')
        for rule, expression in PATTERNS.items():
            for match in expression.finditer(text):
                findings.append({'file': name, 'line': text.count('\n', 0, match.start()) + 1, 'rule': rule})
    return findings


def build():
    entries = [(path.relative_to(ROOT).as_posix(), path.read_bytes()) for path in sources()]
    findings = scan(entries)
    if findings:
        print(json.dumps({'status': 'BLOCKED', 'findings': findings}))
        raise SystemExit(1)
    output = checked(ROOT / 'release')
    output.mkdir(exist_ok=True)
    archive = checked(output / 'Ozon-Manager-V2-source.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, content in entries:
            bundle.writestr('Ozon-Manager-V2/' + name, content)
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        assert len(bundle.namelist()) == len(entries)
        for (name, content), info in zip(entries, bundle.infolist()):
            assert info.filename == 'Ozon-Manager-V2/' + name
            assert bundle.read(info) == content
        assert not scan((info.filename, bundle.read(info)) for info in bundle.infolist())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    report = {'status': 'PASS', 'files': len(entries), 'bytes': archive.stat().st_size,
        'sha256': digest, 'secret_pattern_findings': 0,
        'excluded': ['credentials/env', 'data/history', 'cache/runtimes/dependencies',
                     'git', 'old reports/screenshots', 'one-off diagnostics'],
        'files_sha256': {name: hashlib.sha256(content).hexdigest() for name, content in entries}}
    checked(output / 'MANIFEST.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    checked(output / 'SHA256SUMS.txt').write_text(digest + '  ' + archive.name + '\n', encoding='ascii')
    print(json.dumps({key: value for key, value in report.items() if key != 'files_sha256'}))


if __name__ == '__main__':
    build()
