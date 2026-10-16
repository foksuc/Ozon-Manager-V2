"""Closed Windows project runner. No arbitrary command, path, or CLI overrides.

Controls active tool paths and child environment; this is not an OS sandbox for
arbitrary malicious dependency code. Install scripts/source builds are disabled.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import stat

ROOT = Path(__file__).absolute().parent.parent


def installed_tool(local: Path, fallback: Path) -> Path:
    """Prefer the START bootstrap; never resolve executables from caller PATH."""
    return local if local.is_file() else fallback


NODE = installed_tool(ROOT / ".cache/runtimes/node/node.exe", Path(r"C:\Program Files\nodejs\node.exe"))
PYTHON = installed_tool(ROOT / ".cache/runtimes/python/python.exe", Path(sys.base_prefix) / "python.exe")
NPM = NODE.parent / "node_modules/npm/bin/npm-cli.js"
VERBS = {"setup-python", "repair-python", "install-python", "lock-python", "install-node", "ci-node", "build", "test", "test-finance-csv", "test-table-filters", "test-table-stability", "test-price-display", "start", "start-read", "verify-read", "verify-preview", "verify-finance", "finance-shape", "finance-fixture", "browser-fixture", "dev", "paths", "backup"}


def confined(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(ROOT):
        raise ValueError(f"Внешний путь запрещён: {path}")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise ValueError(f"Ссылка/junction запрещена: {ancestor}")
    if not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Путь выходит за папку V2")
    return path


def preflight(verb: str, args: list[str]) -> dict[str, Path]:
    if verb not in VERBS or args:
        raise ValueError("Разрешён только один известный глагол без дополнительных аргументов")
    if ROOT != Path(__file__).absolute().parent.parent or Path.cwd().resolve() != ROOT.resolve():
        raise ValueError("Запускайте из корневой папки проекта")
    paths = {name: confined(ROOT / name) for name in ("frontend", "backend", ".cache", "data", "docs/evidence")}
    for config in (ROOT / ".npmrc", ROOT / "frontend/.npmrc"):
        confined(config)
        if config.exists():
            raise ValueError("Локальный .npmrc override запрещён; runner задаёт конфигурацию сам")
    # Inspect descendants before any write or subprocess, including planted links.
    for name in ("frontend", "backend", ".cache", "data"):
        try:
            paths[name].stat()
        except FileNotFoundError:
            continue
        def denied(error: OSError) -> None:
            raise error
        for directory, dirs, files in os.walk(paths[name], followlinks=False, onerror=denied):
            for item in (*dirs, *files):
                entry = Path(directory) / item
                info = entry.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError(f"Вложенная ссылка/junction запрещена: {entry}")
    for exe in (NODE, PYTHON, NPM):
        if not exe.is_file():
            raise ValueError(f"Не найден установленный инструмент: {exe}")
    for child in ("backend/.venv", "frontend/node_modules", "frontend/dist", ".cache/tmp", ".cache/npm", ".cache/pip", ".cache/profile", ".cache/npm-userconfig", ".cache/npm-globalconfig", "data/mock.sqlite3", "backend/requirements.lock.txt"):
        confined(ROOT / child)
    return paths


def environment(parent: dict[str, str]) -> dict[str, str]:
    cache = ROOT / ".cache"
    # Do not inherit secrets, proxy/user package config, NODE_OPTIONS, PYTHONPATH,
    # PIP_*, npm_config_*, HOME or Codex configuration into child processes.
    normalized = {k.upper(): v for k, v in parent.items()}
    env = {k: normalized[k] for k in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT") if k in normalized}
    env.update({"PATH": str(NODE.parent) + os.pathsep + str(PYTHON.parent) + os.pathsep + str(Path(normalized.get("SYSTEMROOT", r"C:\Windows")) / "System32"),
                "TEMP": str(cache / "tmp"), "TMP": str(cache / "tmp"),
                "APPDATA": str(cache / "profile/AppData/Roaming"),
                "LOCALAPPDATA": str(cache / "profile/AppData/Local"),
                "USERPROFILE": str(cache / "profile"),
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1",
                "PIP_CONFIG_FILE": os.devnull, "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_CACHE_DIR": str(cache / "pip"),
                "npm_config_cache": str(cache / "npm"),
                "npm_config_userconfig": str(cache / "npm-userconfig"),
                "npm_config_globalconfig": str(cache / "npm-globalconfig"),
                "npm_config_prefix": str(cache / "npm-prefix"),
                "npm_config_update_notifier": "false", "npm_config_fund": "false", "npm_config_audit": "false",
                "PLAYWRIGHT_BROWSERS_PATH": str(cache / "browsers"),
                "OZON_V2_MODE": "MOCK"})
    return env


def prepare() -> None:
    for path in (".cache/tmp", ".cache/npm", ".cache/pip", ".cache/profile/AppData/Roaming", ".cache/profile/AppData/Local", ".cache/npm-prefix", "docs/evidence"):
        confined(ROOT / path).mkdir(parents=True, exist_ok=True)
    for name in (".cache/npm-userconfig", ".cache/npm-globalconfig"):
        confined(ROOT / name).write_text("", encoding="utf-8")


def commands(verb: str) -> list[tuple[list[str], Path]]:
    backend = ROOT / "backend"
    frontend = ROOT / "frontend"
    python = str(backend / ".venv/Scripts/python.exe")
    npm = [str(NODE), str(NPM)]
    if verb == "setup-python":
        return [([str(PYTHON), "-B", "-m", "venv", str(backend / ".venv")], ROOT)]
    if verb == "repair-python":
        return [([python, "-B", "-m", "ensurepip", "--upgrade", "--default-pip", "-v"], backend)]
    if verb in ("install-node", "ci-node"):
        return [(npm + ["install" if verb == "install-node" else "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--registry=https://registry.npmjs.org", "--maxsockets=5", "--fetch-retries=2", "--fetch-retry-mintimeout=1000", "--fetch-retry-maxtimeout=10000", "--fetch-timeout=60000", "--cache=" + str(ROOT / ".cache/npm"), "--userconfig=" + str(ROOT / ".cache/npm-userconfig"), "--globalconfig=" + str(ROOT / ".cache/npm-globalconfig")], frontend)]
    if verb == "install-python":
        req = "requirements.lock.txt" if (backend / "requirements.lock.txt").exists() else "requirements.in"
        return [([python, "-B", "-m", "pip", "install", "--only-binary=:all:", "--index-url", "https://pypi.org/simple", "--cache-dir", str(ROOT / ".cache/pip"), "-r", str(backend / req)], backend)]
    if verb == "lock-python":
        return [([python, "-B", "-m", "pip", "freeze"], backend)]
    if verb == "build":
        return [([str(NODE), str(frontend / "node_modules/typescript/bin/tsc"), "--project", "tsconfig.app.json", "--noEmit"], frontend),
                ([str(NODE), str(frontend / "node_modules/typescript/bin/tsc"), "--project", "tsconfig.node.json", "--noEmit"], frontend),
                ([str(NODE), str(frontend / "node_modules/vite/bin/vite.js"), "build"], frontend)]
    if verb == "test":
        return [([python, "-B", "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "--basetemp=" + str(ROOT / ".cache/pytest-runtime")], backend)]
    if verb in ("start", "start-read"):
        return [([python, "-B", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--workers", "1"] + (["--no-access-log"] if verb == "start-read" else []), backend)]
    if verb == "browser-fixture":
        return [([python, "-B", str(ROOT / "scripts/i1_browser_fixture.py")], ROOT)]
    if verb == "verify-read":
        return [([python, "-B", str(ROOT / "scripts/verify_read.py")], ROOT)]
    if verb == "verify-preview":
        return [([python, "-B", str(ROOT / "scripts/verify_preview.py")], ROOT)]
    if verb == "verify-finance":
        return [([python, "-B", str(ROOT / "scripts/verify_finance.py")], ROOT)]
    if verb == "finance-shape":
        return [([python, "-B", str(ROOT / "scripts/finance_shape.py")], ROOT)]
    if verb == "finance-fixture":
        return [([python, "-B", str(ROOT / "scripts/finance_fixture.py")], ROOT)]
    if verb == "test-finance-csv":
        return [([str(NODE), "--experimental-strip-types", str(ROOT / "scripts/test_finance_csv.mjs")], ROOT)]
    if verb == "test-table-filters":
        return [([str(NODE), "--experimental-strip-types", str(ROOT / "scripts/test_table_filters.mjs")], ROOT)]
    if verb == "test-table-stability":
        return [([str(NODE), str(ROOT / "scripts/test_table_stability.mjs")], ROOT)]
    if verb == "test-price-display":
        return [([str(NODE), "--experimental-strip-types", str(ROOT / "scripts/test_price_display.mjs")], ROOT)]
    if verb == "backup":
        return [([python, "-B", str(ROOT / "scripts/backup_mock.py")], ROOT)]
    if verb == "dev":
        return [([str(NODE), str(frontend / "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", "5173", "--strictPort"], frontend)]
    return []


def run(verb: str, args: list[str]) -> int:
    paths = preflight(verb, args)
    prepare()
    env = environment(dict(os.environ))
    # Only this closed verb can enable live reads; caller environment is ignored.
    if verb == "start-read":
        env["OZON_V2_MODE"] = "LIVE_READ"
    if verb == "paths":
        print(json.dumps({"paths": {k: str(v) for k, v in paths.items()}, "environment": env, "scope": "Configured write paths; not an OS sandbox"}, ensure_ascii=False, indent=2))
    for argv, cwd in commands(verb):
        # No shell expansion and no command text composed from user input.
        if verb == "lock-python":
            result = subprocess.run(argv, cwd=cwd, env=env, check=True, capture_output=True, text=True)
            confined(ROOT / "backend/requirements.lock.txt").write_text(result.stdout, encoding="utf-8")
        else:
            result = subprocess.run(argv, cwd=cwd, env=env, check=False)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Пример: python -B scripts/project.py start")
        raise SystemExit(run(sys.argv[1], []))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Запуск остановлен: {error}", file=sys.stderr)
        raise SystemExit(1)
