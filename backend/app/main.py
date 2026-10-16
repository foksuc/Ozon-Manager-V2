from decimal import Decimal
from contextlib import asynccontextmanager
import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Literal
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.exceptions import ResponseValidationError, RequestValidationError
from .catalog import catalog, timestamp
from .domain import calculate_price, money, stock_value, calculate_stock
from .models import Catalog, Draft, DraftRequest, Preview, PreviewRequest, PreviewSource
from .storage import ROOT, Store, confined, plan_hash
from .connection import Connection, load_connection
from .ozon_read import ReadClient, ReadError, ReadOperation
from .live_catalog import LiveCatalog
from .discount_filter import filter_members
from .models import DiscountFilterRequest
from .models import ReadPreviewRequest
from .read_preview import calculate_read_preview
from .finance import FinancialReader, FinanceQuery, ReportQuery, ProfitQuery, profit
from .finance_mock import FinanceMock
from .performance_read import PerformanceReader
from .performance_statistics import PerformanceStatisticsReader, PerformanceStatisticsQuery, METHODS as PERFORMANCE_METHODS, GENERATE as PERFORMANCE_GENERATE
from .ozon_read import FinanceReport
from .promotion_review import PromotionReviewQuery, review
from .operation_routes import install_operations
from .accounts import AccountStore
from .seller_extension_routes import install_seller_extensions

ALLOWED_ORIGINS = {f"http://{host}:{port}" for host in ("localhost", "127.0.0.1", "[::1]") for port in (8000, 5173)}


def create_app(store: Store | None = None, *, connection: Connection | None = None,
               read_client: ReadClient | None = None) -> FastAPI:
    settings = connection if connection is not None else load_connection()
    client = read_client if read_client is not None else (ReadClient(settings.credentials) if settings.mode == "LIVE_READ" else None)
    performance=PerformanceReader(settings.mode)
    performance_statistics=PerformanceStatisticsReader(settings.mode)
    finance_client=FinanceMock() if settings.mode=='MOCK' else client
    financial_reader=FinancialReader(finance_client,settings.mode)
    report_jobs=None
    report_lock=asyncio.Lock()

    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            if client is not None:
                await client.close()
            if getattr(application.state,'initial_client',None) is not None:
                await application.state.initial_client.close()
            await performance.close()
            await performance_statistics.close()

    app = FastAPI(title="Ozon Manager V2", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.store = store if store is not None else Store()
    app.state.connection = settings
    app.state.catalog_factory=lambda scenario:catalog(scenario)
    app.state.report_lock=report_lock
    app.state.performance_statistics=performance_statistics
    app.state.live_catalog = LiveCatalog(client) if client is not None else None
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if (origin is not None and origin not in ALLOWED_ORIGINS) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Чужой Origin запрещён"}, status_code=403)
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": settings.mode}

    @app.post('/api/finance')
    async def finance_query(request:FinanceQuery):
        try:return await financial_reader.load(request)
        except ValueError as exc:raise HTTPException(422,str(exc)) from None

    @app.get('/api/statistics/methods')
    def statistics_methods():
        return PERFORMANCE_METHODS

    @app.post('/api/statistics/performance')
    async def performance_statistics_query(request:PerformanceStatisticsQuery):
        generating=request.operation in PERFORMANCE_GENERATE
        if generating:
            generation_guard()
            if report_lock.locked() or report_jobs.has_unresolved(app.state.accounts.active_id):
                raise HTTPException(409,'Сначала дождитесь или сверьте финансовый документ')
            app.state.accounts.busy=True
        try:return await performance_statistics.load(request)
        except HistoryError:raise
        except ValueError as exc:raise HTTPException(422,str(exc)) from None
        finally:
            if generating:app.state.accounts.busy=False

    @app.post('/api/finance/profit')
    def profit_query(request:ProfitQuery):
        try:return profit(request)
        except (ValueError,ArithmeticError):raise HTTPException(422,'Введите конечные неотрицательные суммы, до6знаков после запятой') from None

    @app.post('/api/finance/advertising')
    async def advertising(request:FinanceQuery):
        try:return await performance.expenses(request.date_from,request.date_to)
        except ValueError as exc:raise HTTPException(422,str(exc)) from None

    @app.post('/api/finance/reports')
    async def create_report(request:ReportQuery):
        generation_guard()
        binding=(app.state.accounts.active_id,app.state.accounts.revision)
        async def creator(kind,month):
            generation_guard()
            if binding!=(app.state.accounts.active_id,app.state.accounts.revision):
                raise ReadError('INVALID_READ_REQUEST','Магазин изменился во время ожидания; создание отменено',409)
            app.state.accounts.busy=True
            try:return await finance_client.create_finance_report(kind,month)
            finally:app.state.accounts.busy=False
        try:return await report_jobs.create(request,binding[0],creator,mode=settings.mode)
        except HistoryError:raise
        except ValueError as exc:raise HTTPException(422,str(exc)) from None

    def generation_guard():
        if app.state.accounts.busy or performance_statistics.lock.locked():
            raise ReadError('INVALID_READ_REQUEST','Дождитесь завершения текущей операции',409)
        for service_name in ('mutations','seller_extensions','warehouse_reports'):
            service=getattr(app.state,service_name,None)
            if service is not None and service.has_unresolved():
                raise ReadError('INVALID_READ_REQUEST','Сначала сверьте незавершённый результат операций магазина',409)

    @app.get('/api/finance/reports/history')
    def financial_report_history():
        return {'items':report_jobs.history(app.state.accounts.active_id) if app.state.accounts.active_id else []}

    @app.get('/api/statistics/history')
    def statistics_history():
        return {'items':performance_statistics.history()}

    @app.get('/api/operations/history')
    def operation_history():
        from .operation_history_views import event_view
        entries=[item for item in app.state.mutations.history() if item.get('account_id') in (None,app.state.accounts.active_id)]
        for source,events in (
            ('seller',app.state.seller_extensions.history()['items']),
            ('warehouse',app.state.warehouse_reports.history()['items']),
            ('finance',report_jobs.history(app.state.accounts.active_id) if app.state.accounts.active_id else []),
            ('performance',performance_statistics.history())):
            entries.extend(event_view(event,source) for event in events)
        entries.sort(key=lambda entry:entry.get('created_at') or '',reverse=True)
        return {'items':entries}

    @app.post('/api/promotion-review')
    async def promotion_review(request:PromotionReviewQuery):
        if not request.auto_add and request.auto_add_date is not None:raise HTTPException(422,'Дата не относится к обычному составу')
        if settings.mode=='MOCK':
            raw=app.state.mock_catalog('ready')
            raw['products']=[p for p in raw['products'] if p['action_id']==request.action_id and p['role']==request.role]
        else:
            raw=await app.state.live_catalog.catalog(scope='products',action_id=request.action_id,role=request.role,auto_add=request.auto_add,auto_add_date=request.auto_add_date)
        try:
            fresh=Catalog.model_validate(raw)
            matches,unknown=review(fresh.products,request)
        except HistoryError:raise
        except ValueError as exc:raise HTTPException(422,str(exc)) from None
        return {'mode':settings.mode,'fetched_at':fresh.fetched_at,'complete':fresh.complete,'warnings':fresh.warnings,
            'items':[p.model_dump() for p in matches],'unknown':unknown,'total':len(fresh.products),
            'purpose':'prepare_remove' if request.role=='member' else 'prepare_add','executable':False}

    @app.exception_handler(ReadError)
    async def read_error(request: Request, exc: ReadError):
        vendor=getattr(exc,'vendor_error',None)
        fields={key:vendor[key] for key in ('message','reason','reason_ru') if key in vendor} if isinstance(vendor,dict) else {}
        if isinstance(vendor,dict) and vendor.get('code'):fields['vendor_code']=vendor['code']
        return JSONResponse({"detail": exc.message, "code": exc.code, **fields, **({"diagnostic":exc.diagnostic} if exc.diagnostic is not None else {})}, status_code=exc.status)

    @app.exception_handler(ResponseValidationError)
    async def response_contract_error(request: Request, exc: ResponseValidationError):
        return JSONResponse({"detail": "Ответ не прошёл проверку контракта. Каталог не подменялся.",
                             "code": "RESPONSE_CONTRACT"}, status_code=502)

    @app.exception_handler(RequestValidationError)
    async def request_contract_error(request: Request, exc: RequestValidationError):
        # Pydantic's default response includes rejected input, which may contain a key.
        if request.url.path=='/api/accounts':
            return JSONResponse({'detail':'Некорректные поля подключения. Проверьте название, Client-Id и API-ключ.','code':'VALIDATION'},status_code=422)
        return JSONResponse({'detail':[{'msg':error['msg'],'loc':error['loc']} for error in exc.errors()]},status_code=422)

    @app.get("/api/connection")
    def get_connection():
        result=settings.public()
        result.update(read_only=app.state.accounts.active_id is None,manual_execution=True,account_id=app.state.accounts.active_id,account_revision=app.state.accounts.revision)
        return result

    @app.get("/api/connection/check")
    async def check_connection():
        if settings.mode != "LIVE_READ" or client is None:
            raise HTTPException(409, "MOCK не выполняет запросы Ozon. Для чтения используйте start-read.")
        data = await client.read(ReadOperation.ROLES)
        roles = data.get("roles")
        expires = data.get("expires_at")
        if not isinstance(roles, list) or any(not isinstance(role, dict) or
            not isinstance(role.get("name"), str) or not isinstance(role.get("methods"), list) or
            any(not isinstance(method, str) for method in role["methods"]) for role in roles) or (expires is not None and not isinstance(expires, str)):
            raise ReadError("RESPONSE_CONTRACT", "Ozon вернул неизвестный формат прав доступа. Проверка не завершена.")
        return {"status": "ok", "read_only": True, "expires_at": expires, "role_count": len(roles),
                "methods_count": len({method for role in roles for method in role["methods"]})}

    @app.get("/api/catalog", response_model=Catalog)
    async def get_catalog(request: Request, scenario: Literal["ready", "empty", "partial", "error"] = "ready",
                          scope: Literal["actions", "products", "stocks"] = "actions",
                          action_id: str | None = None, role: Literal["member", "candidate"] = "member",
                          auto_add: bool = False, auto_add_date: str | None = None):
        if settings.mode == "MOCK":
            return app.state.mock_catalog(scenario)
        if scenario != "ready":
            raise HTTPException(422, "Сценарии MOCK недоступны в LIVE_READ")
        task = asyncio.create_task(app.state.live_catalog.catalog(scope=scope, action_id=action_id, role=role,
                                                                 auto_add=auto_add, auto_add_date=auto_add_date))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.25)
                if not task.done() and await request.is_disconnected():
                    raise HTTPException(499, "Чтение отменено при закрытии запроса")
            return await task
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    @app.post("/api/preview", response_model=Preview)
    def preview(request: PreviewRequest):
        if settings.mode != "MOCK":
            raise HTTPException(403, "LIVE_READ разрешает только чтение. Предпросмотр изменений отключён.")
        source = app.state.mock_catalog(request.scenario)
        if len(set(request.ids)) != len(request.ids):
            raise HTTPException(422, "Повторные идентификаторы запрещены")
        entries = source["products"] if request.kind == "prices" else source["stocks"]
        lookup = {entry["id"]: entry for entry in entries}
        if any(identifier not in lookup for identifier in request.ids):
            raise HTTPException(422, "Неизвестный идентификатор")
        if request.kind == "prices":
            if request.action_id is None or any(lookup[i]["action_id"] != request.action_id for i in request.ids):
                raise HTTPException(422, "Товары не принадлежат выбранной акции")
        elif request.action_id is not None:
            raise HTTPException(422, "Акция не относится к остаткам")
        rows = []
        errors = list(source["warnings"])
        for identifier in request.ids:
            entry = lookup[identifier]
            before = (entry["action_price"] if entry["role"] == "member" else entry["price"]) if request.kind == "prices" else str(entry["available"])
            after = diff = error = None
            try:
                if request.kind == "prices":
                    if entry["elastic"] and (entry.get("min_price") is None or entry.get("max_price") is None):
                        raise ValueError("Для Elastic отсутствует денежная граница MOCK; расчёт заблокирован")
                    after = calculate_price(entry["price"], request.mode, request.value, request.rounding, request.floor,
                                            entry["min_price"], entry["max_price"])
                    diff = money(Decimal(after) - Decimal(before))
                else:
                    after = str(calculate_stock(entry['available'], request.stock_mode, request.value))
                    diff = str(int(after) - int(before))
            except ValueError as exc:
                error = str(exc)
                errors.append(f"{entry['offer_id']}: {error}")
            rows.append(dict(id=identifier, offer_id=entry["offer_id"], name=entry["name"], before=before,
                             after=after, diff=diff, error=error, currency="RUB"))
        snapshot = PreviewSource.model_validate(dict(mode="MOCK", scenario=request.scenario,
                    fetched_at=source["fetched_at"], action_id=request.action_id,
                    action=next((action for action in source["actions"] if action["id"] == request.action_id), None),
                    items=[lookup[identifier].copy() for identifier in request.ids])).model_dump(mode="json")
        plan = dict(id=str(uuid4()), kind=request.kind, created_at=timestamp(), complete=source["complete"],
                    valid=not errors, source=snapshot, rows=rows, errors=errors)
        plan["hash"] = plan_hash(plan)
        app.state.store.put_plan(plan)
        return plan

    @app.post("/api/discount-filter")
    def discount_filter(request: DiscountFilterRequest):
        # A local view filter, never a removal plan and never an Ozon call.
        try:
            return filter_members(request.items, request.threshold)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/api/read-preview", response_model=Preview)
    async def read_preview(request: ReadPreviewRequest):
        if settings.mode != "LIVE_READ":
            raise HTTPException(409, "Для MOCK используйте обычный предварительный просмотр")
        if request.scenario != "ready" or (not request.auto_add and request.auto_add_date is not None) or (
            request.kind == "prices" and request.action_id is None) or (request.kind == "stocks" and
            (request.action_id is not None or request.auto_add or request.auto_add_date is not None or
             request.mode != "manual" or request.rounding != "1" or request.floor is not None)):
            raise HTTPException(422, "Некорректный контекст расчёта")
        if len(set(request.ids)) != len(request.ids):
            raise HTTPException(422, "Повторные идентификаторы запрещены")
        source = await app.state.live_catalog.catalog(scope="products" if request.kind == "prices" else "stocks",
            action_id=request.action_id, role=request.role, auto_add=request.auto_add, auto_add_date=request.auto_add_date)
        try:
            return calculate_read_preview(Catalog.model_validate(source), request)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/api/drafts", response_model=Draft)
    def save_draft(request: DraftRequest):
        if settings.mode != "MOCK":
            raise HTTPException(403, "LIVE_READ разрешает только чтение. Сохранение плана изменений отключено.")
        try:
            return app.state.store.save_draft(request.plan_id, request.plan_hash)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/drafts")
    def drafts():
        return {"items": app.state.store.history()}

    def configure_reader(credentials):
        nonlocal settings,client,finance_client,financial_reader
        settings=Connection(settings.mode,credentials,'Подключение выбранного магазина настроено.' if credentials else 'Выберите или добавьте магазин во вкладке Аккаунт.')
        client=ReadClient(credentials) if settings.mode=='LIVE_READ' else None
        app.state.connection=settings
        app.state.live_catalog=LiveCatalog(client) if client is not None else None
        finance_client=FinanceMock() if settings.mode=='MOCK' else client
        financial_reader=FinancialReader(finance_client,settings.mode)
        # Cache keys include account binding; UNKNOWN jobs remain blocked when
        # returning to that account instead of being forgotten during a switch.

    async def activate_account(credentials,account_id):
        previous=client
        if settings.mode=='LIVE_READ':
            configure_reader(credentials)
        if previous is not None:
            await previous.close()

    isolated_accounts=None
    if isinstance(store,Store):
        isolated_accounts=AccountStore(settings.mode,path=store.path.parent/'accounts-test.bin',root=store.root)
    elif store is not None:
        isolated_accounts=AccountStore(settings.mode,path=ROOT/'.cache'/f'test-accounts-{uuid4().hex}.bin')
    accounts=install_operations(app,settings,activate_account,account_store=isolated_accounts)
    from .operation_history import account_history,HistoryError
    from .finance_report_jobs import FinanceReportJobs
    report_jobs=FinanceReportJobs(account_history(accounts,'finance'))
    report_lock=report_jobs.lock
    app.state.report_lock=report_lock
    app.state.finance_report_jobs=report_jobs
    performance_statistics.set_history_store(account_history(accounts,'performance'))
    @app.exception_handler(HistoryError)
    async def history_error(request,exc):
        return JSONResponse({'code':'HISTORY_STORAGE','detail':str(exc)},status_code=409)
    async def seller_read(operation,payload):
        if client is None: raise ReadError('NOT_CONNECTED','Выберите или подключите магазин',409)
        return await client.read(operation,payload)
    install_seller_extensions(app,accounts,seller_read,root=isolated_accounts.root if isolated_accounts is not None else ROOT)
    from .analytics_batch import install_analytics_batch
    install_analytics_batch(app,app.state.seller_extensions,seller_read)
    from .warehouse_reports_routes import install_warehouse_reports
    async def warehouse_read(operation,payload):
        if client is None:raise ReadError('NOT_CONNECTED','Выберите или подключите магазин',409)
        return await client.warehouse_read(operation,payload)
    install_warehouse_reports(app,accounts,warehouse_read,root=isolated_accounts.root if isolated_accounts is not None else ROOT,
        blocked=lambda:app.state.mutations.has_unresolved() or app.state.seller_extensions.has_unresolved())
    from .lifecycle import install_lifecycle
    install_lifecycle(app,busy=lambda:accounts.busy or report_lock.locked() or performance_statistics.lock.locked())
    if settings.mode=='LIVE_READ' and accounts.active_id is not None:
        # Create the initial client for the selected encrypted account. The unused
        # initial httpx client has never issued a request; close it in lifespan.
        app.state.initial_client=client
        configure_reader(accounts.credentials())

    @app.get("/{asset_path:path}")
    def frontend(asset_path: str):
        if asset_path == "api" or asset_path.startswith("api/"):
            raise HTTPException(404, "Маршрут API не найден")
        dist = ROOT / "frontend" / "dist"
        if not dist.is_dir():
            raise HTTPException(404, "Frontend ещё не собран")
        # Only serve files beneath dist; arbitrary root/data paths are never SPA routes.
        if ".." in Path(asset_path).parts or asset_path.startswith(".") or asset_path.startswith(("data/", "backend/")):
            raise HTTPException(404, "Файл не найден")
        try:
            target = confined(dist / (asset_path or "index.html"), dist)
            if not target.is_file():
                if asset_path:
                    raise HTTPException(404, "Файл не найден")
                raise HTTPException(404, "Frontend ещё не собран")
            return FileResponse(target,headers={'Cache-Control':'no-store','Pragma':'no-cache'} if target.suffix.lower()=='.html' else None)
        except ValueError as exc:
            raise HTTPException(404, "Файл не найден") from exc

    return app


app = create_app()
