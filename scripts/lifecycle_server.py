"""Only managed entrypoint owns a shutdown callback; ordinary uvicorn is untouched."""
from pathlib import Path
import json
import os
import secrets
import sys
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
import lifecycle
lifecycle.preflight('start')
lifecycle.prepare()
sys.path.insert(0,str(ROOT/'backend'))
import uvicorn
from app.main import app
token=secrets.token_hex(32);instance=secrets.token_hex(16)
server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8000,workers=1,access_log=False,timeout_graceful_shutdown=30))
app.state.lifecycle_token=token
app.state.lifecycle_instance=instance
app.state.lifecycle_exit=lambda:setattr(server,'should_exit',True)
record={'pid':os.getpid(),'identity':lifecycle.identity(os.getpid()),'root':os.path.normcase(str(ROOT)),
    'entry':'scripts/lifecycle_server.py','instance':instance,'token':token}
path=lifecycle.confined(lifecycle.STATE)
temporary=lifecycle.confined(path.with_suffix('.tmp'))
temporary.write_text(json.dumps(record),encoding='utf-8');temporary.replace(path)
try:server.run()
finally:
    # Do not unlink a later instance's state.
    try:
        if json.loads(path.read_text()).get('instance')==instance:path.unlink()
    except (OSError,ValueError):pass
