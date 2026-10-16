"""Synthetic application on8001; no credentials or remote requests."""
import sys
from pathlib import Path
ROOT=Path(__file__).absolute().parent.parent
sys.path.insert(0,str(ROOT/'backend'))
from app.connection import Connection
from app.main import create_app
from app import main as application_module
from app.storage import Store
import uvicorn

if __name__=='__main__':
    if len(sys.argv)!=1 or Path.cwd().resolve()!=ROOT.resolve():raise SystemExit('Closed command only')
    application_module.ALLOWED_ORIGINS=application_module.ALLOWED_ORIGINS | {'http://127.0.0.1:8001'}
    app=create_app(connection=Connection('MOCK'),store=Store(ROOT/'.cache/finance-fixture.sqlite3'))
    uvicorn.run(app,host='127.0.0.1',port=8001,access_log=False)
