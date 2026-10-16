"""Same graceful shutdown for UI and STOP; no PID signals or forceful exit."""
import asyncio
import hmac
import secrets
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

ORIGINS={'http://127.0.0.1:8000','http://localhost:8000','http://[::1]:8000'}
LOOPBACK={'127.0.0.1','::1','localhost'}

def install_lifecycle(app, *, busy=lambda:False):
    app.state.lifecycle_token=secrets.token_hex(32)
    app.state.lifecycle_instance=secrets.token_hex(16)
    app.state.lifecycle_exit=None
    app.state.lifecycle_closing=False

    def local(request):
        if request.client is None or request.client.host not in LOOPBACK:
            raise HTTPException(403,'Только локальное подключение')
        origin=request.headers.get('origin')
        fetch=request.headers.get('sec-fetch-site')
        if origin not in ORIGINS and fetch!='same-origin':
            raise HTTPException(403,'Требуется локальная страница приложения')
        if origin is not None and origin not in ORIGINS or fetch=='cross-site':
            raise HTTPException(403,'Чужой Origin запрещён')

    @app.middleware('http')
    async def closing_guard(request,call_next):
        if app.state.lifecycle_closing and request.method not in {'GET','HEAD'} and request.url.path not in {'/api/lifecycle/shutdown','/api/lifecycle/stop'}:
            return JSONResponse({'detail':'Приложение выключается; новая операция запрещена'},status_code=503)
        return await call_next(request)

    @app.get('/api/lifecycle')
    async def status(request:Request):
        local(request)
        supplied=request.headers.get('x-lifecycle-token','')
        authenticated=hmac.compare_digest(supplied,app.state.lifecycle_token)
        # Token may be requested only by an actual same-origin browser fetch.
        browser=request.headers.get('sec-fetch-site')=='same-origin'
        if not authenticated and not browser:raise HTTPException(403,'Неверный токен процесса')
        return {'supported':callable(app.state.lifecycle_exit),'busy':bool(busy()),
            'closing':app.state.lifecycle_closing,'instance':app.state.lifecycle_instance,
            **({'token':app.state.lifecycle_token} if browser else {})}

    @app.post('/api/lifecycle/shutdown')
    @app.post('/api/lifecycle/stop')
    async def shutdown(request:Request):
        local(request)
        if not hmac.compare_digest(request.headers.get('x-lifecycle-token',''),app.state.lifecycle_token):
            raise HTTPException(403,'Неверный токен процесса')
        callback=app.state.lifecycle_exit
        if not callable(callback):raise HTTPException(409,'Этот запуск не управляется START.bat. Завершите прежний запуск и запустите START.bat.')
        if busy():raise HTTPException(409,'Дождитесь завершения текущей операции и нажмите Выключить снова')
        if app.state.lifecycle_closing:return JSONResponse({'status':'stopping'})
        app.state.lifecycle_closing=True
        async def exit_after_response():
            await asyncio.sleep(.25)
            callback()
        return JSONResponse({'status':'stopping'},background=BackgroundTask(exit_after_response))
