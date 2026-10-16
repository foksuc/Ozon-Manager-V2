"""Local UI account management and manual execution of immutable shop plans."""
from copy import deepcopy
import asyncio
from typing import Literal
from fastapi import HTTPException
from pydantic import Field
from .accounts import AccountStore, AccountError
from .connection import Credentials
from .catalog import catalog, timestamp
from .models import Catalog, Product, Stock, ReadPreviewRequest, StrictDTO
from .domain import calculate_price, calculate_stock, discount_fields, minimum_discount_price
from .mutations import MutationService
from .ozon_write import WriteClient, WriteOperation as Op, WriteError, contract_profile, LEGACY_PROFILE


class AccountRequest(StrictDTO):
    action: Literal['add','update','delete','select','import']
    id: str | None = None
    name: str = Field(default='', max_length=100)
    client_id: str = Field(default='', max_length=32, repr=False)
    api_key: str = Field(default='', max_length=4096, repr=False)


class OperationRequest(StrictDTO):
    operation: Literal['prices','stocks','action_add','action_remove','auto_add','auto_remove']
    request: ReadPreviewRequest


class ConfirmRequest(StrictDTO):
    plan_id: str = Field(max_length=64)
    plan_hash: str = Field(max_length=64)
    confirmed: bool = False


class RollbackRequest(StrictDTO):
    operation_id: str = Field(min_length=36, max_length=36, pattern=r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')


def install_operations(app, settings, activate, *, account_store=None):
    accounts = account_store if account_store is not None else AccountStore(settings.mode)
    app.state.accounts = accounts
    contexts = {}
    product_ids = {}
    mock_values = {}
    account_lock = asyncio.Lock()
    legacy = settings.credentials

    def mock_catalog(scenario='ready'):
        result = app.state.catalog_factory(scenario)
        changed = mock_values.get(accounts.active_id, {})
        result['products'] = [dict(row, **changed.get(row['id'], {})) for row in result['products'] if changed.get(row['id'], {}).get('removed') is not True]
        for row in result['products']:
            row.update(discount_fields(row))
        for row in result['stocks']:
            row.update(changed.get(row['id'], {}))
        return result

    app.state.mock_catalog = mock_catalog

    async def source(request):
        if settings.mode == 'MOCK':
            result = mock_catalog(request.scenario)
        else:
            result = await app.state.live_catalog.catalog(scope='stocks' if request.kind == 'stocks' else 'products',
                action_id=request.action_id, role=request.role, auto_add=request.auto_add, auto_add_date=request.auto_add_date)
        checked = Catalog.model_validate(result)
        for row in checked.products:
            if row.action_id == request.action_id and row.role == request.role:
                contexts[row.id] = {'action_id':row.action_id,'role':row.role,'auto_add':request.auto_add,'auto_add_date':request.auto_add_date}
                product_ids[row.id]=row.product_id
        return checked

    def normalize(row,op,row_id=None):
        row=(Stock if op is Op.STOCK else Product).model_validate(row).model_dump()
        item=deepcopy(row)
        if row_id is not None:
            item['id']=row_id
        if op is Op.STOCK:
            item.update(current=row['available'],currency=None)
            if settings.mode=='MOCK':
                item['warehouse_id']=str(100+int(row['warehouse_id'].rsplit('-',1)[1]))
        else:
            item['currency_binding_fields']=[]
            if (op is Op.ACTION_UPDATE and contract_profile(op) != LEGACY_PROFILE
                and row.get('card_currency')=='RUB' and row.get('card_price') is not None
                and row.get('currency')=='RUB' and 'price' not in row.get('money_assumed_fields',[])):
                # Explicit user authorization13.10: only missing units bind to
                # this SAME product's freshly confirmed card currency. Keep the
                # original read metadata separately inside the immutable source.
                original=dict(row.get('money_currencies',{}))
                effective=dict(original)
                for field_name in ('action_price','min_price','max_price','max_action_price','price_min_elastic','price_max_elastic'):
                    if row.get(field_name) is not None and effective.get(field_name) in (None,''):
                        effective[field_name]='RUB'
                        item['currency_binding_fields'].append(field_name)
                item['money_currencies']=effective
                item['original_money_currencies']=original
            field='price' if op is Op.PRICE else 'action_price'
            unit_field='price' if row['role']=='candidate' else field
            legacy = settings.mode != 'MOCK' and contract_profile(op) == LEGACY_PROFILE
            if legacy and op in (Op.ACTION_UPDATE,Op.AUTO_UPDATE):
                unit_field='price'
            # A candidate has no existing participation to change. Seller can
            # return zero (or a proposed price) here; neither is its current price.
            current = None if row['role']=='candidate' and op is not Op.PRICE else row.get(field)
            item.update(current=current,currency=item.get('money_currencies',{}).get(unit_field,row.get('currency')),member=row['role']=='member')
            if unit_field in row.get('money_assumed_fields',[]) or (row.get('currency_assumed') and not row.get('money_assumed_fields')):
                item['currency']=None
            for field_name in ('minimum_discount','maximum_discount','actual_discount','auto_add_discount'):
                item.pop(field_name,None)
        return item

    async def fresh(op, scope, ids):
        if settings.mode == 'MOCK':
            raw = mock_catalog()
            aid = scope.get('action_id')
            if aid:
                aid = f'mock-action-{int(aid)-100}'
            entries = raw['stocks'] if op is Op.STOCK else raw['products']
            if aid:
                entries = [row for row in entries if row['action_id'] == aid]
        else:
            entries = []
            if op is Op.STOCK:
                raw = await app.state.live_catalog.catalog(scope='stocks')
                if raw['complete'] is not True:
                    return {'complete':False,'items':[]}
                entries = raw['stocks']
            else:
                # Contexts originate from a verified server catalogue, never client IDs.
                groups = {}
                for row_id in ids:
                    context = contexts.get(row_id)
                    if context is None:
                        raise WriteError('MISSING_CONTEXT','Обновите каталог и подготовьте операцию заново',409)
                    if scope and (context['action_id'] != scope.get('action_id') or
                        context['auto_add_date'] != scope.get('auto_add_date')):
                        raise WriteError('SCOPE_CHANGED','Область операции изменилась',409)
                    key = tuple(context.items())
                    groups[key] = context
                for context in groups.values():
                    raw = await app.state.live_catalog.catalog(scope='products',**context)
                    if raw['complete'] is not True:
                        return {'complete':False,'items':[]}
                    entries.extend(raw['products'])
                    if op not in (Op.PRICE,Op.STOCK):
                        other={**context,'role':'member' if context['role']=='candidate' else 'candidate'}
                        raw=await app.state.live_catalog.catalog(scope='products',**other)
                        if raw['complete'] is not True:
                            return {'complete':False,'items':[]}
                        entries.extend(raw['products'])
        result = []
        for row in entries:
            aliases=[row['id']] if row['id'] in ids else [row_id for row_id in ids if op not in (Op.PRICE,Op.STOCK) and product_ids.get(row_id)==row.get('product_id') and contexts[row_id]['action_id']==row.get('action_id')]
            for row_id in aliases:
                result.append(normalize(row,op,row_id))
        return {'complete':True,'items':result}

    class MockWriter:
        async def close(self):
            pass

        async def send(self,op,body):
            rows = body.get('prices',body.get('stocks',body.get('products',body.get('product_ids',[]))))
            changes = mock_values.setdefault(accounts.active_id,{})
            output = []
            raw = mock_catalog()
            for target in rows:
                pid = str(target.get('product_id',target.get('id'))) if isinstance(target,dict) else str(target)
                entries = raw['stocks'] if op is Op.STOCK else raw['products']
                for row in entries:
                    if (row['offer_id'] != target['offer_id'] if op is Op.STOCK and 'offer_id' in target else row['product_id'] != pid):
                        continue
                    if op is Op.STOCK and str(100+int(row['warehouse_id'].rsplit('-',1)[1])) != str(target['warehouse_id']):
                        continue
                    change = changes.setdefault(row['id'],{})
                    if op is Op.STOCK:
                        change.update(available=target['stock'],present=target['stock']+row['reserved'])
                    elif op is Op.PRICE:
                        change['price']=target['price']
                        if row.get('card_currency') is not None:change['card_price']=target['price']
                    elif op in (Op.ACTION_REMOVE,Op.AUTO_REMOVE):
                        change.update(role='candidate',action_price=row.get('action_price') if op is Op.AUTO_REMOVE else None)
                    else:
                        change.update(role='member',action_price=target['action_price']['amount'])
                        if op is Op.ACTION_UPDATE and row.get('is_voucher_action') is False:
                            change.update(price=target['action_price']['amount'],card_price=target['action_price']['amount'])
                    output.append({'product_id':int(row['product_id']),'updated':True,'errors':[],**({'warehouse_id':target['warehouse_id']} if op is Op.STOCK else {})})
            identities=[str(item['product_id']) for item in output]
            if op in (Op.PRICE,Op.STOCK):return {'result':output}
            if op is Op.AUTO_REMOVE:return {'product_ids':identities}
            if op is Op.ACTION_REMOVE:return {'result':{'deactivated_product_ids':identities}}
            return {'result':{'active_product_ids':identities}}

    def restore_context(document):
        if document['operation']=='STOCK':return
        auto=document['operation'] in ('AUTO_UPDATE','AUTO_REMOVE')
        for planned in document['rows']:
            row=planned['source']
            contexts[planned['id']]={'action_id':row.get('action_id'),'role':row.get('role','member' if row.get('member') else 'candidate'),
                'auto_add':auto,'auto_add_date':document['scope'].get('auto_add_date')}
            product_ids[planned['id']]=row.get('product_id')
    service = MutationService(accounts,lambda credentials:MockWriter() if settings.mode=='MOCK' else WriteClient(credentials),fresh,
        path=accounts.path.with_suffix('.attempts') if account_store is not None else None,root=accounts.root,restore_context=restore_context)
    app.state.mutations = service
    def public_accounts():
        data = accounts.list()
        return {**{key:value for key,value in data.items() if key!='accounts'},'items':data['accounts'],'legacy_available':legacy is not None}

    @app.exception_handler(AccountError)
    async def account_error(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':str(exc),'code':'ACCOUNT_ERROR'},status_code=409)

    @app.exception_handler(WriteError)
    async def write_error(request,exc):
        from fastapi.responses import JSONResponse
        vendor=getattr(exc,'vendor_error',None)
        fields={key:vendor[key] for key in ('message','reason','reason_ru') if key in vendor} if isinstance(vendor,dict) else {}
        if isinstance(vendor,dict) and vendor.get('code'):fields['vendor_code']=vendor['code']
        return JSONResponse({'detail':exc.message,'code':exc.code,**fields},status_code=exc.status)

    @app.get('/api/accounts')
    def get_accounts():
        return public_accounts()

    @app.post('/api/accounts')
    async def manage_accounts(request:AccountRequest):
        async with account_lock:
            extensions=getattr(app.state,'seller_extensions',None)
            warehouse=getattr(app.state,'warehouse_reports',None)
            if request.action in ('select','update','delete') and (service.has_unresolved() or (extensions is not None and extensions.has_unresolved()) or (warehouse is not None and warehouse.has_unresolved())):
                raise AccountError('Сначала сверьте неизвестный результат операции магазина')
            if app.state.report_lock.locked():
                raise AccountError('Дождитесь завершения запроса финансового документа')
            if request.action=='add':
                accounts.add(request.name,request.client_id,request.api_key)
            elif request.action=='import':
                if legacy is None:
                    raise AccountError('Текущего локального подключения нет')
                accounts.add(request.name or 'Мой магазин',legacy.client_id,legacy.api_key)
            elif request.action=='update':
                accounts.update(request.id,request.name,request.client_id,request.api_key)
            elif request.action=='delete':
                accounts.delete(request.id)
            else:
                accounts.select(request.id)
            service.invalidate()
            contexts.clear()
            product_ids.clear()
            await activate(accounts.credentials(),accounts.active_id)
            return public_accounts()

    @app.post('/api/mutations/preview')
    async def prepare_operation(query:OperationRequest):
        op = Op.parse(query.operation)
        request = query.request
        if len(set(request.ids))!=len(request.ids) or (op is Op.STOCK)!=(request.kind=='stocks'):
            raise HTTPException(422,'Некорректные идентификаторы или тип операции')
        if settings.mode!='MOCK' and request.scenario!='ready':
            raise HTTPException(422,'Сценарий MOCK недоступен для реального магазина')
        if request.kind=='stocks' and (request.action_id is not None or request.auto_add or request.auto_add_date is not None):
            raise HTTPException(422,'Акция не относится к остаткам')
        if request.kind=='prices' and (request.action_id is None or (not request.auto_add and request.auto_add_date is not None)):
            raise HTTPException(422,'Не выбрана корректная область акции')
        if op in (Op.ACTION_UPDATE,Op.ACTION_REMOVE) and request.auto_add or op in (Op.AUTO_UPDATE,Op.AUTO_REMOVE) and not request.auto_add:
            raise HTTPException(422,'Метод не соответствует разделу акции')
        if op in (Op.ACTION_REMOVE,Op.AUTO_REMOVE) and request.role!='member':
            raise HTTPException(422,'Удалить можно только участников')
        binding=(accounts.active_id,accounts.revision)
        checked = await source(request)
        if not checked.complete:
            raise HTTPException(409,'Неполные данные: запись запрещена')
        entries = checked.stocks if op is Op.STOCK else [row for row in checked.products if row.action_id==request.action_id and row.role==request.role and (not request.auto_add or row.auto_add_at==request.auto_add_date)]
        lookup={row.id:row for row in entries}
        if any(row_id not in lookup for row_id in request.ids):
            raise HTTPException(409,'Выбранная строка отсутствует в свежем составе')
        changes=[]
        for row_id in request.ids:
            row=lookup[row_id]
            # ID-only removal carries no monetary amount or currency.
            money_fields={'price'}
            legacy = settings.mode != 'MOCK' and contract_profile(op) == LEGACY_PROFILE
            if op in (Op.ACTION_UPDATE,Op.AUTO_UPDATE) and not legacy:
                money_fields.update(field for field in ('action_price','min_price','max_price','max_action_price','price_min_elastic','price_max_elastic') if getattr(row,field,None) is not None)
            if op in (Op.PRICE,Op.ACTION_UPDATE,Op.AUTO_UPDATE) and settings.mode=='LIVE_READ' and row.currency_assumed and (not row.money_assumed_fields or money_fields.intersection(row.money_assumed_fields)):
                raise WriteError('CURRENCY_ASSUMED','RUB задана временно для просмотра и расчёта; валюта записи не подтверждена',409)
            if op in (Op.ACTION_REMOVE,Op.AUTO_REMOVE):
                after=None
            elif op is Op.STOCK:
                try:after=calculate_stock(row.available,request.stock_mode,request.value)
                except ValueError as exc:raise HTTPException(422,str(exc)) from None
            elif op is Op.AUTO_UPDATE and not legacy:
                if request.role != 'candidate':
                    raise WriteError('AUTO_RETURN_ONLY','В автодобавлении доступен возврат кандидатов с ценой Ozon',409)
                if row.money_currencies.get('action_price',row.currency) != 'RUB':
                    raise WriteError('CURRENCY_UNKNOWN','Валюта цены автодобавления не подтверждена',409)
                from .ozon_write import money
                after=money(row.action_price)
            elif op is Op.ACTION_UPDATE and not legacy:
                currencies=normalize(row.model_dump(),op).get('money_currencies',row.money_currencies)
                for field in ('price','min_price','max_price','max_action_price','price_min_elastic','price_max_elastic'):
                    if getattr(row,field,None) is not None and currencies.get(field,row.currency)!='RUB':
                        raise WriteError('CURRENCY_UNKNOWN','Валюта цены или границы участия не подтверждена',409)
                try:after=minimum_discount_price(row.model_dump())
                except ValueError as exc:raise WriteError('UNKNOWN_BOUNDS',str(exc),409) from None
            else:
                currencies=normalize(row.model_dump(),op).get('money_currencies',row.money_currencies)
                if currencies.get('price',row.currency)!='RUB':
                    raise WriteError('CURRENCY_UNKNOWN','Валюта базовой цены не подтверждена; запись запрещена',409)
                low=high=None
                if op is not Op.PRICE:
                    for field in ('min_price','max_price','max_action_price'):
                        if getattr(row,field) is not None and currencies.get(field,row.currency)!='RUB':
                            raise WriteError('CURRENCY_UNKNOWN','Валюта ограничения цены не подтверждена',409)
                    low,high=row.min_price,row.max_price
                    if row.elastic and (low is None or high is None):
                        raise WriteError('UNKNOWN_BOUNDS','Границы акции не подтверждены',409)
                try:after=calculate_price(row.price,request.mode,request.value,request.rounding,request.floor,low,high)
                except ValueError as exc:raise HTTPException(422,str(exc)) from None
                from decimal import Decimal
                if Decimal(after)!=Decimal(after).to_integral_value():
                    raise HTTPException(422,'Цена отправки должна быть целым количеством рублей. Выберите округление от 1 ₽ и целый нижний предел.')
            changes.append({'id':row_id,'after':after})
        if binding!=(accounts.active_id,accounts.revision):
            raise WriteError('ACCOUNT_CHANGED','Кабинет изменился во время расчёта',409)
        scope={}
        if op not in (Op.PRICE,Op.STOCK):
            scope['action_id']=str(100+int(request.action_id.rsplit('-',1)[1])) if settings.mode=='MOCK' else request.action_id
            if op in (Op.AUTO_UPDATE,Op.AUTO_REMOVE):
                scope['auto_add_date']=request.auto_add_date
        try:
            expected={'complete':True,'items':[normalize(lookup[row_id].model_dump(),op) for row_id in request.ids]}
            return await service.preview(op,scope,changes,expected_source=expected)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None

    @app.post('/api/mutations/execute')
    async def execute_operation(query:ConfirmRequest):
        warehouse=getattr(app.state,'warehouse_reports',None)
        if warehouse is not None and warehouse.has_unresolved():
            raise WriteError('UNRESOLVED','Сначала сверьте незавершённый результат товаров, цен или отчётов',409)
        extensions=getattr(app.state,'seller_extensions',None)
        if extensions is not None and extensions.has_unresolved():
            raise WriteError('UNRESOLVED','Сначала сверьте неизвестный результат стратегии',409)
        return await service.execute(query.plan_id,query.plan_hash,confirmed=query.confirmed)

    @app.post('/api/mutations/rollback-preview')
    async def prepare_rollback(query:RollbackRequest):
        return await service.rollback_preview(query.operation_id)

    @app.post('/api/mutations/reconcile')
    async def reconcile_operation(query:ConfirmRequest):
        return await service.reconcile(query.plan_id,query.plan_hash)

    @app.post('/api/mutations/reconcile-history')
    async def reconcile_history(query:RollbackRequest):
        return await service.reconcile_history(query.operation_id)

    @app.get('/api/mutations/history')
    def history():
        return {'items':service.history()}

    return accounts
