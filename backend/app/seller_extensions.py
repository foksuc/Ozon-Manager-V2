"""Closed Seller strategies, inventory analytics and rating operations."""
import json
import asyncio
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import hmac
import os
import re
from pathlib import Path
import time
from uuid import uuid4, UUID
import httpx
from .ozon_read import ReadOperation, ReadError, BASE_URL, MAX_BYTES, rejection_diagnostic
from .vendor_errors import normalize_vendor_error
from .operation_history import account_history
from .catalog import timestamp
from .ozon_write import WriteError
from .storage import ROOT, confined, canonical
SCHEMA_DOCUMENT = json.loads(Path(__file__).with_name('seller_extension_schemas.json').read_text(encoding='utf8'))


class StrategyOperation(Enum):
    STRATEGY_CREATE = '/v1/pricing-strategy/create'
    STRATEGY_UPDATE = '/v1/pricing-strategy/update'
    STRATEGY_ADD_PRODUCTS = '/v1/pricing-strategy/products/add'
    STRATEGY_REMOVE_PRODUCTS = '/v1/pricing-strategy/products/delete'
    STRATEGY_STATUS = '/v1/pricing-strategy/status'
    STRATEGY_DELETE = '/v1/pricing-strategy/delete'


def schema(value):
    if '$ref' in value:
        return SCHEMA_DOCUMENT['schemas'][value['$ref'].split('/')[-1]]
    return value


def operation_path(name):
    try:
        return ReadOperation[name].value[1]
    except KeyError:
        try: return StrategyOperation[name].value
        except KeyError: raise ReadError('INVALID_OPERATION', 'Неизвестная операция Seller', 422) from None


def _validate(value, contract):
    contract = schema(contract)
    kind = contract.get('type', 'array' if 'items' in contract else 'object' if 'properties' in contract else None)
    if kind == 'object':
        if type(value) is not dict or set(value) - set(contract.get('properties', {})) or set(contract.get('required', [])) - set(value):
            raise ValueError('object')
        for key, item in value.items(): _validate(item, contract['properties'][key])
    elif kind == 'array':
        if type(value) is not list or len(value) > 1000: raise ValueError('array')
        for item in value: _validate(item, contract['items'])
    elif kind == 'boolean':
        if type(value) is not bool: raise ValueError('boolean')
    elif kind == 'integer':
        if type(value) is not int or not -(2**63) <= value < 2**63: raise ValueError('integer')
        if value < contract.get('minimum', -(2**63)) or value > contract.get('maximum', 2**63-1): raise ValueError('bounds')
    elif kind == 'number':
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal, float)): raise ValueError('number')
        if not Decimal(str(value)).is_finite(): raise ValueError('finite')
    elif kind == 'string':
        if not isinstance(value, str) or len(value) > 4096: raise ValueError('string')
        if contract.get('format') == 'int64' and (not value.isdecimal() or not 0 < int(value) < 2**63): raise ValueError('id')
    if 'enum' in contract and value not in contract['enum']: raise ValueError('enum')


def validate_extension_request(name, body):
    path = operation_path(name)
    if path not in SCHEMA_DOCUMENT['operations'] or type(body) is not dict:
        raise ReadError('INVALID_SELLER_REQUEST', 'Некорректный запрос Seller', 422)
    try:
        _validate(body, SCHEMA_DOCUMENT['operations'][path]['request'])
        for field in ('strategy_id', 'strategy_name'):
            if field in body and not body[field].strip(): raise ValueError('empty')
        for field in ('page', 'limit'):
            if field in body and body[field] <= 0: raise ValueError('positive')
        if 'offset' in body and body['offset'] < 0: raise ValueError('offset')
        if name in ('STRATEGY_LIST', 'STRATEGY_COMPETITORS') and body['limit'] > 50: raise ValueError('limit')
        if 'product_id' in body:
            values = body['product_id']
            if isinstance(values, list):
                if not 1 <= len(values) <= 50 or len(set(values)) != len(values): raise ValueError('products')
            elif values <= 0: raise ValueError('product')
        if name == 'STRATEGY_STATUS' and type(body.get('enabled')) is not bool: raise ValueError('status')
        if 'competitors' in body:
            if not 1 <= len(body['competitors']) <= 50: raise ValueError('competitors')
            ids = [item['competitor_id'] for item in body['competitors']]
            if len(set(ids)) != len(ids) or any(item <= 0 for item in ids): raise ValueError('competitor ids')
            if any(not Decimal('0.5') <= Decimal(str(item['coefficient'])) <= Decimal('1.2') for item in body['competitors']): raise ValueError('coefficient')
        if name == 'ANALYTICS_WAREHOUSES' and body['limit'] > 1000: raise ValueError('limit')
        if name == 'ANALYTICS_TURNOVER' and not body.get('sku') and 'limit' not in body: raise ValueError('pagination')
        for field in ('skus', 'sku', 'cluster_ids', 'macrolocal_cluster_ids', 'warehouse_ids'):
            if field in body and (not body[field] or len(set(body[field])) != len(body[field])): raise ValueError('ids')
        if name == 'ANALYTICS_STOCKS' and len(body['skus']) > 100: raise ValueError('sku limit')
        if body.get('cluster_ids') and body.get('macrolocal_cluster_ids'): raise ValueError('clusters')
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
        raise ReadError('INVALID_SELLER_REQUEST', 'Проверьте обязательные поля, идентификаторы и ограничения Seller API', 422) from None


class ResponseShapeError(ValueError):
    def __init__(self,path,expected,value):
        self.diagnostic={'field_path':path,'expected':expected,'actual_type':'null' if value is None else 'object' if type(value) is dict else 'array' if type(value) is list else 'boolean' if type(value) is bool else 'number' if isinstance(value,(int,float,Decimal)) else 'string' if isinstance(value,str) else 'unknown'}
        super().__init__('Response field shape differs')


def _normalize(value, contract, path='$'):
    contract = schema(contract)
    if value is None: return None
    kind = contract.get('type', 'array' if 'items' in contract else 'object' if 'properties' in contract else None)
    if kind == 'object':
        if type(value) is not dict: raise ResponseShapeError(path,'object',value)
        if re.search(r'\.errors\[\d+\]$',path):
            result=normalize_vendor_error(value)
            if 'product_id' in value and 'product_id' in contract.get('properties',{}):result['product_id']=_normalize(value['product_id'],contract['properties']['product_id'],path+'.product_id')
            return result
        return {key: _normalize(item, contract['properties'][key],path+'.'+key) for key, item in value.items() if key in contract.get('properties', {})}
    if kind == 'array':
        # Current live inventory uses one named placement zone rather than the
        # snapshot's array. Only this addressed enum field accepts the scalar.
        if path.endswith('.placement_zone') and isinstance(value,str) and value in schema(contract['items']).get('enum',[]):
            return [value]
        # Rating's public snapshot omits type for localization_index and uses
        # items for both singular objects and lists; preserve either safe shape.
        if 'type' not in contract and type(value) is dict:
            return _normalize(value, contract['items'],path)
        if type(value) is not list or len(value) > 50000: raise ResponseShapeError(path,'array',value)
        return [_normalize(item, contract['items'],path+'['+str(index)+']') for index,item in enumerate(value)]
    if kind == 'number':
        try: result = Decimal(str(value))
        except (InvalidOperation,ValueError): raise ResponseShapeError(path,'finite decimal',value) from None
        if not result.is_finite(): raise ResponseShapeError(path,'finite decimal',value)
        return format(result, 'f')
    if kind == 'integer':
        if isinstance(value, bool) or not isinstance(value, (int, str)) or str(int(value)) != str(value): raise ResponseShapeError(path,'integer',value)
        return str(value) if contract.get('format') == 'int64' else int(value)
    if kind == 'string':
        if not isinstance(value, str): raise ResponseShapeError(path,'string',value)
        return value
    if kind == 'boolean':
        if type(value) is not bool: raise ResponseShapeError(path,'boolean',value)
        return value
    return value


def normalize_response(name, value):
    try:
        value=deepcopy(value)
        # Addressed current rendered examples and schema differ in these fields.
        # Normalize only the named variants; inconsistent identities stay unknown.
        if name=='STRATEGY_CREATE':
            root=value.get('result',{})
            ids=[item for item in (value.get('strategy_id'),root.get('strategy_id'),root.get('id')) if item is not None]
            if any(not isinstance(item,str) or not item for item in ids) or len(set(ids))>1: raise ValueError('conflicting strategy identity')
            if ids: value={'result':{'strategy_id':ids[0]}}
        if name=='STRATEGY_INFO' and 'strategy_name' in value.get('result',{}):
            root=value['result']
            if 'name' in root and root['name']!=root['strategy_name']: raise ValueError('conflicting strategy name')
            root['name']=root.pop('strategy_name')
        if name=='STRATEGY_PRODUCT_INFO':
            competitor=value.get('result',{}).get('strategy_competitor_id')
            if isinstance(competitor,str) and not competitor.isdecimal():
                UUID(competitor)
                value['result'].pop('strategy_competitor_id')
                result=_normalize(value,SCHEMA_DOCUMENT['operations'][operation_path(name)]['response'])
                result['result']['strategy_competitor_id']=competitor
                return result
        return _normalize(value, SCHEMA_DOCUMENT['operations'][operation_path(name)]['response'])
    except ResponseShapeError as exc:
        raise ReadError('RESPONSE_CONTRACT','Seller вернул поле другого типа',502,diagnostic=exc.diagnostic) from None
    except (ValueError, TypeError, KeyError, InvalidOperation):
        raise ReadError('RESPONSE_CONTRACT', 'Seller вернул неизвестный формат данных', 502) from None


def response_rows(name, data):
    root = data.get('result', data)
    if name == 'STRATEGY_LIST': return data.get('strategies', [])
    if name == 'STRATEGY_COMPETITORS': return data.get('competitor', [])
    if name == 'STRATEGY_IDS': return root.get('products_info', [])
    if name == 'STRATEGY_PRODUCTS': return [{'product_id': value} for value in root.get('product_id', [])]
    if name == 'ANALYTICS_WAREHOUSES': return root.get('rows', [])
    if name in ('ANALYTICS_STOCKS', 'ANALYTICS_TURNOVER'): return data.get('items', [])
    if name == 'RATING_SUMMARY': return [dict(item, group_name=group.get('group_name', '')) for group in data.get('groups', []) for item in group.get('items', [])]
    return [root] if root else []


def _wire(value):
    if isinstance(value, Decimal): return format(value, 'f')
    if type(value) is dict: return '{' + ','.join(json.dumps(key)+':'+_wire(item) for key,item in value.items()) + '}'
    if type(value) is list: return '[' + ','.join(map(_wire,value)) + ']'
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class StrategyWriteClient:
    """Fixed host, closed enum, no retry or redirection; never accepts a URL."""
    def __init__(self, credentials, *, transport=None):
        self.credentials = credentials
        self.http = httpx.AsyncClient(base_url=BASE_URL, transport=transport, trust_env=False, follow_redirects=False, timeout=httpx.Timeout(12,connect=5))

    async def close(self): await self.http.aclose()

    async def send(self, operation, request):
        if type(operation) is not StrategyOperation: raise WriteError('INVALID_OPERATION','Неизвестная операция',422)
        validate_extension_request(operation.name, request)
        if self.credentials is None: raise WriteError('NOT_CONFIGURED','Подключение Seller не настроено',503)
        body=deepcopy(request)
        for item in body.get('competitors', []): item['coefficient']=Decimal(str(item['coefficient']))
        try:
            async with self.http.stream('POST',operation.value,content=_wire(body).encode('utf8'),headers={'Client-Id':self.credentials.client_id,'Api-Key':self.credentials.api_key,'Content-Type':'application/json'}) as response:
                if response.status_code in (400,401,403,404,422):
                    diagnostic=await rejection_diagnostic(response,secrets=(self.credentials.client_id,self.credentials.api_key))
                    raise WriteError('REJECTED','Ozon отклонил изменение стратегии',response.status_code,vendor_error=diagnostic.get('vendor_error'))
                if not 200 <= response.status_code < 300:
                    diagnostic=await rejection_diagnostic(response,secrets=(self.credentials.client_id,self.credentials.api_key))
                    raise WriteError('UNKNOWN_RESULT','Результат неизвестен; автоматический повтор запрещён',502,vendor_error=diagnostic.get('vendor_error'))
                chunks=[]; size=0
                async for chunk in response.aiter_bytes():
                    size+=len(chunk)
                    if size>MAX_BYTES: raise WriteError('UNKNOWN_RESULT','Ответ слишком велик; требуется сверка',502)
                    chunks.append(chunk)
                raw=json.loads(b''.join(chunks),parse_float=Decimal)
                if isinstance(raw,dict) and 'code' in raw and 'message' in raw:
                    error=normalize_vendor_error(raw,secrets=(self.credentials.client_id,self.credentials.api_key))
                    raise WriteError('REJECTED',error['message'],502,vendor_error=error)
                result=normalize_response(operation.name,raw)
                for item in result.get('result',{}).get('errors',[]):
                    item.update(normalize_vendor_error(item,secrets=(self.credentials.client_id,self.credentials.api_key)))
                return result
        except (httpx.RequestError, ValueError, ReadError):
            raise WriteError('UNKNOWN_RESULT','Ответ не подтверждён; требуется сверка без повторной отправки',502) from None


class SellerExtensions:
    """RAM-only immutable strategy plans, account binding, read-after-write."""
    def __init__(self, accounts, read, *, writer_factory=StrategyWriteClient, root=ROOT, path=None, clock=time.monotonic, verified_writes=(), history_store=None):
        self.accounts,self.reader,self.writer_factory=accounts,read,writer_factory
        self.root,self.path,self.clock=root,Path(path) if path else root/'.cache/strategy-attempts',clock
        self.plans={}; self.lock=asyncio.Lock(); self.turnover={}; self.recovered=[]
        self.turnover_lock=asyncio.Lock()
        self.verified_writes=frozenset(verified_writes)
        namespace='warehouse' if self.__class__.__name__=='WarehouseReports' else 'seller'
        self.journal=history_store or account_history(accounts,namespace,root=root)
        self.mock_strategies={'synthetic-strategy':{'name':'Синтетическая стратегия','type':'COMP_PRICE','enabled':False,'competitors':[{'competitor_id':11,'coefficient':'0.95'}],'update_type':'strategyCreated'}}
        self.mock_members={'101':'synthetic-strategy'}
        if accounts.mode!='MOCK' and confined(self.path,root).exists():
            for entry in self.path.iterdir():
                confined(entry,root)
                if entry.suffix!='.json': continue
                try:
                    if entry.stat().st_size>512: raise ValueError('marker')
                    marker=json.loads(entry.read_text('utf8')); UUID(entry.stem)
                    if set(marker)!={'id','status'} or marker['id']!=entry.stem: raise ValueError('marker')
                    if marker['status']=='UNKNOWN_RESULT': self.recovered.append(marker)
                except (ValueError,OSError):
                    raise WriteError('ATTEMPT_CORRUPT','Маркер попытки стратегии повреждён',409) from None

    def binding(self): return (self.accounts.active_id,self.accounts.revision)

    def _save_outcome(self,stored):
        doc=stored['document']
        event=deepcopy(stored['outcome'])|{'id':doc['plan_id'],'account_id':doc['account_id'],'operation':doc['operation'],'mode':self.accounts.mode,'created_at':stored.setdefault('created_at',timestamp())}
        self.journal.upsert(event)

    def _vendor_error(self,error):
        credentials=self.accounts.credentials() if self.accounts.mode!='MOCK' else None
        secrets=(credentials.client_id,credentials.api_key) if credentials else ()
        return normalize_vendor_error(error,secrets=secrets)

    def _marker(self, plan_id,status):
        if self.accounts.mode=='MOCK': return
        directory=confined(self.path,self.root); directory.mkdir(parents=True,exist_ok=True)
        target=confined(directory/(str(UUID(plan_id))+'.json'),self.root)
        temporary=confined(directory/(str(UUID(plan_id))+'.tmp-'+uuid4().hex),self.root)
        with temporary.open('x',encoding='utf8') as stream:
            json.dump({'id':plan_id,'status':status},stream);stream.flush();os.fsync(stream.fileno())
        confined(target,self.root);os.replace(temporary,target)

    async def _raw(self,name,request):
        validate_extension_request(name,request)
        if self.accounts.mode=='MOCK': return normalize_response(name,self._mock_read(name,request))
        return normalize_response(name,await self.reader(ReadOperation[name],deepcopy(request)))

    async def read(self,name,request):
        if name=='ANALYTICS_TURNOVER':
            async with self.turnover_lock:
                return await self._read_response(name,request)
        return await self._read_response(name,request)

    async def _read_response(self,name,request):
        if name not in ReadOperation.__members__ or not name.startswith(('STRATEGY_','ANALYTICS_','RATING_')): raise ReadError('INVALID_OPERATION','Неизвестная операция',422)
        binding=self.binding(); validate_extension_request(name,request)
        if name=='ANALYTICS_TURNOVER':
            cached=self.turnover.get(binding[0])
            if cached and self.clock()-cached['time']<60:
                if cached['binding']!=binding or cached['request']!=request or cached['response'] is None: raise ReadError('LOCAL_RATE_LIMIT','Оборачиваемость доступна раз в минуту; повторите позже',429)
                return deepcopy(cached['response'])
            self.turnover[binding[0]]={'time':self.clock(),'binding':binding,'request':deepcopy(request),'response':None}
        data=await self._raw(name,request)
        if binding!=self.binding(): raise ReadError('ACCOUNT_CHANGED','Магазин изменился во время загрузки',409)
        rows=response_rows(name,data); next_request=None
        if name in ('STRATEGY_COMPETITORS','STRATEGY_LIST') and int(data.get('total',0))>request['page']*request['limit']: next_request=dict(request,page=request['page']+1)
        if name in ('ANALYTICS_WAREHOUSES','ANALYTICS_TURNOVER') and not request.get('sku') and len(rows)==request.get('limit',100): next_request=dict(request,offset=request.get('offset',0)+request.get('limit',100))
        result={'operation':name,'data':data,'rows':rows,'complete':next_request is None,'next_request':next_request,'warnings':['Этот метод заменяется /v1/analytics/stocks; дата отключения не указана'] if name=='ANALYTICS_WAREHOUSES' else []}
        if name=='ANALYTICS_TURNOVER': self.turnover[binding[0]]={'time':self.clock(),'binding':binding,'request':deepcopy(request),'response':deepcopy(result)}
        return result

    async def _all(self,name):
        page=1; result=[]; seen=set()
        while page<=200:
            # Current operational example and live read both accept20; rendered
            # prose50 currently returns INVALID_ARGUMENT for these two lists.
            data=await self._raw(name,{'page':page,'limit':20}); rows=response_rows(name,data)
            for row in rows:
                key=str(row.get('id'))
                if key in seen: raise WriteError('INCOMPLETE_SOURCE','Список стратегий/конкурентов повторяется',409)
                seen.add(key);result.append(row)
            total=data.get('total')
            if type(total) is not int or total<0: raise WriteError('INCOMPLETE_SOURCE','Нет общего количества в ответе',409)
            if len(result)==total: return result
            if len(result)>total: raise WriteError('INCOMPLETE_SOURCE','Количество элементов не совпало со списком',409)
            if not rows: break
            page+=1
        raise WriteError('INCOMPLETE_SOURCE','Не удалось полностью загрузить стратегии',409)

    async def _memberships(self,product_ids):
        """A missing bulk mapping is not proof of no strategy. Confirm individually."""
        info=await self._raw('STRATEGY_IDS',{'product_id':product_ids})
        rows=info.get('result',{}).get('products_info')
        if not isinstance(rows,list): raise WriteError('INCOMPLETE_SOURCE','Ответ привязок товаров неполон',409)
        lookup={}; requested=set(product_ids);checks={}
        for row in rows:
            key=str(row.get('product_id'))
            if key not in requested or key in lookup or not isinstance(row.get('strategy_id'),str): raise WriteError('INCOMPLETE_SOURCE','Ответ привязок содержит повтор или неизвестный товар',409)
            lookup[key]=deepcopy(row)
        for product_id in product_ids:
            if product_id in lookup: continue
            checked=await self._raw('STRATEGY_PRODUCT_INFO',{'product_id':int(product_id)})
            result=checked.get('result',{})
            strategy_id=result.get('strategy_id'); enabled=result.get('is_enabled')
            if not isinstance(strategy_id,str) or type(enabled) is not bool or (strategy_id=='' and enabled is not False): raise WriteError('INCOMPLETE_SOURCE','Отсутствие стратегии товара не подтверждено отдельным чтением',409)
            lookup[product_id]={'product_id':product_id,'strategy_id':strategy_id}
            checks[product_id]=checked
        return {'result':{'products_info':[lookup[key] for key in product_ids]},'product_checks':checks}

    async def _snapshot(self,name,request):
        if name=='STRATEGY_CREATE':
            return {'strategies':await self._all('STRATEGY_LIST'),'competitors':await self._all('STRATEGY_COMPETITORS')}
        if name=='STRATEGY_REMOVE_PRODUCTS':
            return await self._memberships(request['product_id'])
        info=await self._raw('STRATEGY_INFO',{'strategy_id':request['strategy_id']})
        result=info.get('result',{})
        if not all(key in result for key in ('type','name','enabled','competitors')): raise WriteError('INCOMPLETE_SOURCE','Данные стратегии неполны',409)
        if result['type']=='MIN_EXT_PRICE' and name in ('STRATEGY_UPDATE','STRATEGY_DELETE','STRATEGY_STATUS'): raise WriteError('SYSTEM_STRATEGY','Системную стратегию изменять или удалять нельзя',409)
        if name in ('STRATEGY_DELETE','STRATEGY_ADD_PRODUCTS'):
            info['products']=await self._raw('STRATEGY_PRODUCTS',{'strategy_id':request['strategy_id']})
            if 'product_id' not in info['products'].get('result',{}): raise WriteError('INCOMPLETE_SOURCE','Список товаров стратегии неполон',409)
            if name=='STRATEGY_ADD_PRODUCTS':
                info['memberships']=await self._memberships(request['product_id'])
        if name=='STRATEGY_UPDATE': info['available_competitors']=await self._all('STRATEGY_COMPETITORS')
        return info

    async def preview(self,name,request):
        if name not in StrategyOperation.__members__: raise WriteError('INVALID_OPERATION','Неизвестная операция стратегии',422)
        if self.accounts.mode!='MOCK' and name not in self.verified_writes: raise WriteError('CONTRACT_UNVERIFIED','Текущий контракт этой операции стратегии ещё проверяется; отправка не разрешена',409)
        validate_extension_request(name,request); binding=self.binding()
        before=await self._snapshot(name,request)
        available=before.get('competitors') if name=='STRATEGY_CREATE' else before.get('available_competitors')
        if available is not None and any(str(item['competitor_id']) not in {str(value['id']) for value in available} for item in request['competitors']): raise WriteError('INVALID_COMPETITOR','Конкурент отсутствует в актуальном списке',422)
        if binding!=self.binding(): raise WriteError('ACCOUNT_CHANGED','Магазин изменился',409)
        document={'plan_id':str(uuid4()),'operation':name,'request':deepcopy(request),'before':before,'account_id':binding[0],'revision':binding[1],'api_path':operation_path(name)}
        digest=hashlib.sha256(canonical(document).encode()).hexdigest()
        self.plans[document['plan_id']]={'document':document,'hash':digest,'created':self.clock(),'attempted':False,'outcome':None}
        return self._public(self.plans[document['plan_id']])

    def _public(self,stored):
        doc=stored['document']
        return {key:deepcopy(doc[key]) for key in ('plan_id','operation','request','before','api_path')}|{'plan_hash':stored['hash'],'expires_in':max(0,120-int(self.clock()-stored['created'])),'valid':True,'status':'PREVIEW'}

    def _plan(self,plan_id,digest):
        stored=self.plans.get(plan_id)
        if not stored or not hmac.compare_digest(stored['hash'],str(digest)) or not hmac.compare_digest(hashlib.sha256(canonical(stored['document']).encode()).hexdigest(),str(digest)): raise WriteError('PLAN_INVALID','План не найден или изменён',409)
        if (stored['document']['account_id'],stored['document']['revision'])!=self.binding(): raise WriteError('ACCOUNT_CHANGED','Магазин изменился',409)
        return stored

    async def execute(self,plan_id,plan_hash,confirmed):
        if confirmed is not True: raise WriteError('CONFIRM_REQUIRED','Подтвердите конкретный план',409)
        async with self.lock:
            stored=self._plan(plan_id,plan_hash);doc=stored['document']
            if self.accounts.mode!='MOCK' and doc['operation'] not in self.verified_writes: raise WriteError('CONTRACT_UNVERIFIED','Текущий контракт операции стратегии не подтверждён',409)
            if stored['attempted']: raise WriteError('ALREADY_ATTEMPTED','План уже отправлялся; повтор запрещён',409)
            if self.has_unresolved(): raise WriteError('UNRESOLVED','Сначала сверка неизвестного результата стратегии',409)
            if self.accounts.busy: raise WriteError('BUSY','Дождитесь текущей операции',409)
            self.accounts.busy=True;writer=None
            try:
                if self.clock()-stored['created']>120: raise WriteError('STALE_PLAN','План устарел',409)
                if await self._snapshot(doc['operation'],doc['request'])!=doc['before']: raise WriteError('FRESH_CHANGED','Свежие данные изменились; подготовьте новый план',409)
                self._plan(plan_id,plan_hash)
                if self.clock()-stored['created']>120: raise WriteError('STALE_PLAN','План устарел во время проверки',409)
                self._marker(plan_id,'UNKNOWN_RESULT');stored['attempted']=True
                stored['outcome']={'plan_id':plan_id,'plan_hash':plan_hash,'status':'UNKNOWN_RESULT','success_count':0,'error_count':0,'items':[]}
                self._save_outcome(stored)
                response=None
                try:
                    if self.accounts.mode=='MOCK': response=self._mock_write(doc['operation'],doc['request'])
                    else:
                        writer=self.writer_factory(self.accounts.credentials());response=await writer.send(StrategyOperation[doc['operation']],deepcopy(doc['request']))
                except WriteError as error:
                    stored['outcome']['transport_error']=getattr(error,'vendor_error',None) or {'code':error.code,'message':error.message}
                    if error.code=='REJECTED':
                        stored['outcome'].update(status='REJECTED',error_count=len(doc['request'].get('product_id',[])) or 1)
                        self._save_outcome(stored)
                        self._marker(plan_id,'REJECTED');return deepcopy(stored['outcome'])
                except Exception: pass
                stored['response']=response
                return await self._verify(stored)
            finally:
                self.accounts.busy=False
                if writer: await writer.close()

    async def reconcile(self,plan_id,plan_hash):
        async with self.lock:
            stored=self._plan(plan_id,plan_hash)
            if not stored['attempted']: raise WriteError('NOT_ATTEMPTED','План ещё не отправлялся',409)
            if self.accounts.busy: raise WriteError('BUSY','Дождитесь текущей операции',409)
            self.accounts.busy=True
            try:
                return await self._verify(stored)
            finally:
                self.accounts.busy=False

    def history(self):
        binding=self.binding()
        result=[event for event in self.journal.entries() if event.get('account_id')==binding[0] and event.get('mode')==self.accounts.mode]
        for event in result:
            event['can_reconcile']=event['id'] in self.plans and bool(self.plans[event['id']]['attempted'])
            event['recovered']=event['id'] not in self.plans
        existing={event['id'] for event in result}
        result.extend(deepcopy(stored['outcome']) for stored in self.plans.values() if stored['outcome'] and stored['document']['plan_id'] not in existing and (stored['document']['account_id'],stored['document']['revision'])==binding)
        return {'items':result,'recovered':deepcopy(self.recovered),'requires_manual_reconciliation':bool(self.recovered)}

    def has_unresolved(self):
        return bool(self.recovered or any(item['outcome'] and item['outcome']['status']=='UNKNOWN_RESULT' for item in self.plans.values()) or any(event.get('account_id')==self.accounts.active_id and event.get('status')=='UNKNOWN_RESULT' for event in self.journal.entries()))

    async def _verify(self,stored):
        doc=stored['document'];name=doc['operation'];request=doc['request'];response=stored.get('response');items=[]
        try:
            if name in ('STRATEGY_ADD_PRODUCTS','STRATEGY_REMOVE_PRODUCTS'):
                result=await self._memberships(request['product_id'])
                memberships={str(item['product_id']):item.get('strategy_id') for item in result.get('result',{}).get('products_info',[])}
                if len(memberships)!=len(result.get('result',{}).get('products_info',[])): raise ValueError('duplicate verification')
                rejected={str(item['product_id']):self._vendor_error(item) for item in (response or {}).get('result',{}).get('errors',[]) if str(item.get('product_id')) in request['product_id']}
                failed=int((response or {}).get('result',{}).get('failed_product_count',0))
                for value in request['product_id']:
                    ok=value in memberships and (memberships[value]==request['strategy_id'] if name=='STRATEGY_ADD_PRODUCTS' else not memberships[value])
                    items.append({'id':value,'status':'REJECTED' if value in rejected else 'SUCCESS' if ok else 'UNKNOWN_RESULT','errors':[rejected[value]] if value in rejected else []})
                if failed and not rejected and all(item['status']=='SUCCESS' for item in items):
                    items=[dict(item,status='UNKNOWN_RESULT') for item in items]
            elif name=='STRATEGY_CREATE':
                strategy_id=(response or {}).get('result',{}).get('strategy_id')
                if not strategy_id: raise ValueError('missing create id')
                current=(await self._raw('STRATEGY_INFO',{'strategy_id':strategy_id})).get('result',{})
                ok=current.get('name')==request['strategy_name'] and current.get('competitors')==self._normalized_competitors(request['competitors'])
                items=[{'id':strategy_id,'status':'SUCCESS' if ok else 'UNKNOWN_RESULT'}]
            elif name=='STRATEGY_DELETE':
                current=await self._all('STRATEGY_LIST')
                ok=all(item.get('id')!=request['strategy_id'] for item in current)
                items=[{'id':request['strategy_id'],'status':'SUCCESS' if ok else 'UNKNOWN_RESULT'}]
            else:
                current=(await self._raw('STRATEGY_INFO',{'strategy_id':request['strategy_id']})).get('result',{})
                ok=current.get('enabled')==request['enabled'] if name=='STRATEGY_STATUS' else current.get('name')==request['strategy_name'] and current.get('competitors')==self._normalized_competitors(request['competitors'])
                items=[{'id':request['strategy_id'],'status':'SUCCESS' if ok else 'UNKNOWN_RESULT'}]
            self._plan(doc['plan_id'],stored['hash'])
        except Exception:
            self._save_outcome(stored)
            return deepcopy(stored['outcome'])
        success=sum(item['status']=='SUCCESS' for item in items);errors=sum(item['status']=='REJECTED' for item in items);unknown=any(item['status']=='UNKNOWN_RESULT' for item in items)
        status='UNKNOWN_RESULT' if unknown else 'SUCCESS' if success==len(items) else 'PARTIAL' if success else 'REJECTED'
        result={'plan_id':doc['plan_id'],'plan_hash':stored['hash'],'status':status,'success_count':success,'error_count':errors,'items':items}
        if stored['outcome'].get('transport_error'):result['transport_error']=deepcopy(stored['outcome']['transport_error'])
        self._marker(doc['plan_id'],status);stored['outcome']=result
        self._save_outcome(stored)
        return deepcopy(result)

    @staticmethod
    def _normalized_competitors(items): return [{'competitor_id':str(item['competitor_id']),'coefficient':format(Decimal(str(item['coefficient'])),'f')} for item in items]

    def _mock_read(self,name,request):
        strategies=self.mock_strategies
        if name=='STRATEGY_COMPETITORS': return {'competitor':[{'id':11,'name':'Синтетический конкурент'}],'total':1}
        if name=='STRATEGY_LIST': return {'strategies':[dict(value,id=key,products_count=sum(v==key for v in self.mock_members.values()),competitors_count=len(value['competitors'])) for key,value in strategies.items()],'total':len(strategies)}
        if name=='STRATEGY_INFO': return {'result':deepcopy(strategies[request['strategy_id']])}
        if name=='STRATEGY_PRODUCTS': return {'result':{'product_id':[key for key,value in self.mock_members.items() if value==request['strategy_id']]}}
        if name=='STRATEGY_IDS': return {'result':{'products_info':[{'product_id':int(key),'strategy_id':self.mock_members.get(key,'')} for key in request['product_id']]}}
        if name=='STRATEGY_PRODUCT_INFO': return {'result':{'strategy_id':self.mock_members.get(str(request['product_id']),''),'is_enabled':False,'strategy_product_price':950,'price_downloaded_at':'2026-10-09T00:00:00Z','strategy_competitor_product_url':'https://example.invalid/synthetic-product'}}
        if name=='ANALYTICS_WAREHOUSES': return {'result':{'rows':[{'sku':30001,'item_code':'SYNTHETIC-1','item_name':'Синтетический товар','free_to_sell_amount':24,'promised_amount':6,'reserved_amount':2,'warehouse_name':'Синтетический склад'}] if request.get('offset',0)==0 else []}}
        if name=='ANALYTICS_TURNOVER': return {'items':[{'sku':30001,'name':'Синтетический товар','offer_id':'SYNTHETIC-1','current_stock':24,'ads':Decimal('2.5'),'idc':Decimal('9.6'),'turnover':Decimal('12.2'),'idc_grade':'GRADES_CRITICAL','turnover_grade':'GRADES_YELLOW'}] if request.get('offset',0)==0 else []}
        if name=='ANALYTICS_STOCKS': return {'items':[{'sku':int(sku),'name':'Синтетический товар','offer_id':'SYNTHETIC-'+str(index+1),'warehouse_id':101,'warehouse_name':'Синтетический склад','cluster_id':1,'cluster_name':'Синтетический кластер','available_stock_count':24,'transit_stock_count':6,'idc':Decimal('9.6'),'ads':Decimal('2.5'),'turnover_grade':'DEFICIT'} for index,sku in enumerate(request['skus'])]}
        if name=='RATING_SUMMARY': return {'groups':[{'group_name':'Качество обслуживания','items':[{'name':'Доставка вовремя','rating':'delivery_on_time','current_value':Decimal('98.5'),'past_value':Decimal('97.2'),'rating_direction':'HIGHER_IS_BETTER','status':'OK','value_type':'PERCENT'}]}],'premium':False,'premium_plus':False,'penalty_score_exceeded':False,'localization_index':{'calculation_date':'2026-10-09T00:00:00Z','localization_percentage':80}}
        raise ReadError('INVALID_OPERATION','Неизвестная операция',422)

    def _mock_write(self,name,request):
        if name=='STRATEGY_CREATE':
            key='synthetic-'+uuid4().hex
            self.mock_strategies[key]={'name':request['strategy_name'],'type':'COMP_PRICE','enabled':False,'competitors':deepcopy(request['competitors']),'update_type':'strategyCreated'}
            return {'result':{'strategy_id':key}}
        if name=='STRATEGY_UPDATE': self.mock_strategies[request['strategy_id']].update(name=request['strategy_name'],competitors=deepcopy(request['competitors']))
        elif name=='STRATEGY_STATUS': self.mock_strategies[request['strategy_id']]['enabled']=request['enabled']
        elif name=='STRATEGY_ADD_PRODUCTS':
            for key in request['product_id']: self.mock_members[key]=request['strategy_id']
            return {'result':{'failed_product_count':0,'errors':[]}}
        elif name=='STRATEGY_REMOVE_PRODUCTS':
            for key in request['product_id']: self.mock_members.pop(key,None)
            return {'result':{'failed_product_count':0}}
        elif name=='STRATEGY_DELETE':
            self.mock_strategies.pop(request['strategy_id'])
            self.mock_members={key:value for key,value in self.mock_members.items() if value!=request['strategy_id']}
        return {}
