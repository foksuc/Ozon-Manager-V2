"""Backup the synthetic I0 database through SQLite's online backup API."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).absolute().parent.parent
if Path.cwd().resolve() != ROOT.resolve() or len(sys.argv) != 1:
    raise SystemExit("Run project.py backup from V2; no path overrides")
sys.path.insert(0, str(ROOT / "backend"))
from app.storage import Store, confined

if not confined(ROOT / "data/mock.sqlite3").is_file():
    raise SystemExit("Пока нет MOCK базы; сначала сохраните черновик")
target = ROOT / "data/backups" / ("mock-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite3")
Store().backup(target)
print(json.dumps({"status": "PASS", "backup": str(target), "verification": "SQLite integrity, schema version and every plan hash read back"}, ensure_ascii=False))
