"""Closed product, price and report contracts; RAM plans and one-shot writes."""
import asyncio
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4
import httpx
from .ozon_read import BASE_URL, MAX_BYTES, ReadError, rejection_diagnostic
from .vendor_errors import normalize_vendor_error
from .ozon_write import WriteError
from .seller_extensions import SellerExtensions, ResponseShapeError
from .storage import ROOT, canonical

CONTRACTS=json.loads(Path(__file__).with_name('warehouse_reports_schemas.json').read_text('utf8'))['operations']
for contract in CONTRACTS.values():
    if not contract['request']:
        contract['request']={'type':'object','properties':{}}
# Local required fields make the form useful even where public schemas omit
# required on array containers; this deliberately narrows accepted requests.
for name,fields in {'PRODUCT_IMPORT':['items'],'PRODUCT_IMPORT_SKU':['items'],'PRODUCT_UPDATE_ATTRIBUTES':['items'],'PRODUCT_UPDATE_PICTURES':['items'],'PRICE_UPDATE':['prices'],'PRICE_TIMER_UPDATE':['product_ids'],'PRICE_TIMER_STATUS':['product_ids']}.items():
    CONTRACTS[name]['request']['required']=fields
for name in ('PRODUCT_IMPORT','PRODUCT_IMPORT_SKU'):
    row=CONTRACTS[name]['request']['properties']['items']['items']
    row['required']=sorted(set(row.get('required',[]))|{'offer_id','currency_code','price'}|({'name'} if name=='PRODUCT_IMPORT' else set()))
CONTRACTS['PRICE_UPDATE']['request']['properties']['prices']['items']['required']=['currency_code']
# The published pictures-info schema has type=array and properties=items;
# current rendered response example is an object with its items array.
CONTRACTS['PRODUCT_PICTURES']['response']['type']='object'
WarehouseRead=Enum('WarehouseRead',{name:('POST',item['path']) for name,item in CONTRACTS.items() if item['read']})
WarehouseWrite=Enum('WarehouseWrite',{name:item['path'] for name,item in CONTRACTS.items() if not item['read']})
MONEY_FIELDS=frozenset(('price','old_price','min_price','net_price','declared_price'))
LIVE_BLOCKED={}

def validate_shape(value,schema,depth=0):
    if depth>16: raise ValueError('depth')
    kind=schema.get('type')
    if kind=='object':
        if type(value)is not dict or set(value)-set(schema.get('properties',{})) or set(schema.get('required',[]))-set(value): raise ValueError('object')
        for key,item in value.items(): validate_shape(item,schema['properties'][key],depth+1)
    elif kind=='array':
        if type(value)is not list or len(value)>min(1000,schema.get('maxItems',1000)): raise ValueError('array')
        for item in value: validate_shape(item,schema.get('items',{}),depth+1)
    elif kind=='integer':
        if type(value)is not int or not -(2**63)<=value<2**63: raise ValueError('integer')
    elif kind=='boolean':
        if type(value)is not bool: raise ValueError('boolean')
    elif kind=='string':
        if type(value)is not str or len(value)>20000: raise ValueError('string')
        if schema.get('format')=='int64' and (not re.fullmatch(r'[1-9][0-9]{0,18}',value) or int(value)>=2**63): raise ValueError('id')
    if schema.get('enum') and value not in schema['enum']: raise ValueError('enum')
    if kind in ('integer','number') and (value<schema.get('minimum',-(2**63)) or value>schema.get('maximum',2**63-1)): raise ValueError('bounds')

def validate_warehouse_request(name,body):
    try:
        if name not in CONTRACTS or type(body)is not dict: raise ValueError('operation')
        validate_shape(body,CONTRACTS[name]['request'])
        if len(canonical(body))>1024*1024: raise ValueError('size')
        for key in ('limit','page','page_size'):
            if key in body and (type(body[key])is not int or not 1<=body[key]<=1000): raise ValueError('page')
        for key in ('items','prices','product_ids','discounted_skus','warehouseId'):
            if key in body and (not body[key] or len(body[key])>100): raise ValueError('local batch cap')
        if name=='PRICE_DISCOUNT_UPDATE' and not 3<=body['discount']<=99: raise ValueError('discount')
        if name=='PRICE_DISCOUNT_UPDATE' and body['product_id']<=0:raise ValueError('product')
        for field in ('description_category_id','type_id','attribute_id','task_id','parent_attribute_id','child_attribute_id'):
            if field in body and body[field]<=0:raise ValueError('id')
        if name=='CATEGORY_SEARCH' and not body['value'].strip():raise ValueError('search')
        if name=='REPORT_INFO' and not body['code'].strip():raise ValueError('code')
        selectors=body.get('filter',{}) if name in ('PRODUCT_LIST','PRODUCT_ATTRIBUTES','PRODUCT_PRICES') else body
        groups=[key for key in ('offer_id','product_id','sku','skus')if selectors.get(key)]
        if name in ('PRODUCT_LIST','PRODUCT_ATTRIBUTES','PRODUCT_PRICES','PRODUCT_INFO') and len(groups)>1:raise ValueError('selector groups')
        for key in ('offer_id','product_id','sku','skus','product_ids','discounted_skus','warehouseId'):
            values=selectors.get(key)
            if isinstance(values,list) and (not values or len(set(map(str,values)))!=len(values)or len(values)>1000):raise ValueError('selectors')
        items=body.get('items',body.get('prices',[]));seen=set()
        for item in items:
            selector=[key for key in ('offer_id','product_id') if item.get(key) not in ('',None)]
            if name=='PRICE_UPDATE' and len(selector)!=1: raise ValueError('selector')
            key=str(item.get('offer_id',item.get('product_id',item.get('sku'))))
            if key=='None' or key in seen: raise ValueError('identity')
            seen.add(key)
            if item.get('offer_id') and (len(item['offer_id'])>50 or not item['offer_id'].strip()): raise ValueError('offer')
            for key in ('product_id','sku','description_category_id','type_id','quant_size'):
                if key in item and item[key]<=0:raise ValueError('positive id')
            for field in MONEY_FIELDS:
                if field in item:
                    if type(item[field])is not str or not re.fullmatch(r'\d{1,15}(?:\.\d{1,4})?',item[field]): raise ValueError('money string')
                    amount=Decimal(item[field])
                    if amount<0 or (field=='price' and amount==0): raise ValueError('money')
            if 'price'in item and item.get('old_price') and Decimal(item['old_price']) and Decimal(item['old_price'])<=Decimal(item['price']): raise ValueError('oldprice')
            if item.get('min_price') and 'price'in item and Decimal(item['min_price'])>Decimal(item['price']): raise ValueError('minprice')
            if item.get('currency_code') and item['currency_code'] not in ('RUB','BYN','KZT','EUR','USD','CNY'): raise ValueError('currency')
            if name in ('PRODUCT_IMPORT','PRODUCT_IMPORT_SKU') and (not item.get('currency_code') or not item.get('offer_id')): raise ValueError('explicit currency and offer')
            if item.get('vat') and item['vat'] not in ('0','0.05','0.07','0.1','0.2','0.22'): raise ValueError('vat')
            for key in ('primary_image','color_image'):
                if item.get(key) and not public_https(item[key]): raise ValueError('url')
            if any(not public_https(url) for url in item.get('images',[])): raise ValueError('url')
            if item.get('primary_image') and len(item.get('images',[]))>49: raise ValueError('images')
            if 'name'in item and len(item['name'])>500: raise ValueError('name')
            if name=='PRODUCT_IMPORT' and not item.get('name'): raise ValueError('name')
            if item.get('dimension_unit')and item['dimension_unit']not in ('mm','cm','in'):raise ValueError('dimension')
            if item.get('weight_unit')and item['weight_unit']not in ('g','kg','lb'):raise ValueError('weight')
            for field in ('width','height','depth','weight'):
                if field in item and item[field]<=0:raise ValueError('dimensions')
            for attribute in item.get('attributes',[]):
                if attribute.get('id',0)<=0 or not attribute.get('values'):raise ValueError('attribute')
                if attribute.get('id')==8229:raise ValueError('type_id is a separate field')
                for entry in attribute['values']:
                    if entry.get('dictionary_value_id',0)<0 or not entry.get('value') and not entry.get('dictionary_value_id'):raise ValueError('attribute value')
        if name in ('PRODUCT_INFO','PRODUCT_DESCRIPTION'):
            if len([key for key in ('offer_id','product_id','sku') if body.get(key)])!=1: raise ValueError('selector')
        if name=='PRICE_UPDATE':
            for item in items:
                if not(set(item)-{'offer_id','product_id','currency_code'}):raise ValueError('no changes')
        if name.startswith('REPORT_') and not CONTRACTS[name]['read']:
            period=body.get('filter',body.get('date',body))
            first=period.get('date_from',period.get('from',period.get('processed_at_from')));last=period.get('date_to',period.get('to',period.get('processed_at_to')))
            if first or last:
                if not first or not last: raise ValueError('period')
                start=datetime.fromisoformat(first.replace('Z','+00:00'));end=datetime.fromisoformat(last.replace('Z','+00:00'))
                if start>end: raise ValueError('period')
                if name.startswith('REPORT_PLACEMENT_') and ((end-start).days>30 or len(first)!=10 or len(last)!=10): raise ValueError('31days')
                if name in ('REPORT_RETURNS','REPORT_POSTINGS') and (start.tzinfo is None or end.tzinfo is None): raise ValueError('UTC')
    except (ValueError,KeyError,TypeError,InvalidOperation,OverflowError):
        raise ReadError('INVALID_WAREHOUSE_REQUEST','Проверьте обязательные поля, точные цены, идентификаторы и ограничения операции',422) from None

def public_https(value):
    try:
        parsed=urlsplit(value)
        host=(parsed.hostname or '').lower()
        return parsed.scheme=='https' and bool(host) and not parsed.username and not parsed.password and parsed.port in (None,443) and host not in ('localhost','0.0.0.0') and not re.fullmatch(r'[0-9.]+',host) and ':'not in host and not host.endswith('.local')
    except ValueError:return False

def report_download(value):
    if not isinstance(value,str) or not public_https(value): return None
    host=urlsplit(value).hostname
    return value if host and (host=='ozone.ru' or host.endswith('.ozone.ru') or host=='ozon.ru' or host.endswith('.ozon.ru')) else None

def normalize_warehouse_response(name,body):
    """Keep documented response fields only; numbers remain exact decimal strings."""
    def walk(value,schema,path='$'):
        if value is None:return None
        kind=schema.get('type')
        # Addressed LIVE contract09.10: attributes sku is an exact JSON integer
        # although the public schema calls it a string. No float/string coercion
        # is enabled for unrelated response fields.
        if name=='PRODUCT_ATTRIBUTES' and re.fullmatch(r'\$\.result\[\d+\]\.sku',path) and kind=='string' and type(value)is int:
            if not 0<value<2**63:raise ResponseShapeError(path,'positive int64 SKU',value)
            return str(value)
        if name=='PRODUCT_ATTRIBUTES' and path=='$.total' and kind=='string' and type(value)is int:
            if not 0<=value<2**63:raise ResponseShapeError(path,'nonnegative int64 total',value)
            return str(value)
        # The addressed LIVE proof confirms an array of the documented limit
        # object. Keep this alternate wire shape confined to this one field.
        if name=='PRODUCT_LIMITS' and path=='$.operation_limits' and kind=='object' and type(value)is list:
            if len(value)>50000:raise ResponseShapeError(path,'bounded array',value)
            result=[]
            for index,item in enumerate(value):
                item_path=path+'['+str(index)+']'
                if type(item)is not dict:raise ResponseShapeError(item_path,'object',item)
                limit=item.get('limit');limit_type=item.get('limit_type')
                if isinstance(limit,bool)or not isinstance(limit,(int,str))or not re.fullmatch(r'\d+',str(limit))or not 0<=int(limit)<2**63:
                    raise ResponseShapeError(item_path+'.limit','nonnegative int64 limit',limit)
                if limit_type not in ('UNSPECIFIED','RATE_LIMIT_PER_MINUTE'):
                    raise ResponseShapeError(item_path+'.limit_type','documented limit_type enum',limit_type)
                result.append(walk(item,schema,item_path))
            return result
        if kind=='object':
            if type(value)is not dict: raise ResponseShapeError(path,'object',value)
            if re.search(r'\.errors\[\d+\]$',path):return normalize_vendor_error(value)
            return {key:walk(item,schema['properties'][key],path+'.'+key) for key,item in value.items() if key in schema.get('properties',{})}
        if kind=='array':
            if type(value)is not list or len(value)>50000: raise ResponseShapeError(path,'array',value)
            return [walk(item,schema.get('items',{}),path+'['+str(index)+']')for index,item in enumerate(value)]
        if kind=='number':
            try:number=Decimal(str(value))
            except InvalidOperation:raise ResponseShapeError(path,'number',value)from None
            if not number.is_finite():raise ResponseShapeError(path,'finite number',value)
            return format(number,'f')
        if kind=='integer':
            if isinstance(value,bool)or not re.fullmatch(r'-?\d+',str(value)):raise ResponseShapeError(path,'integer',value)
            return str(value)
        if kind=='boolean' and type(value)is not bool:raise ResponseShapeError(path,'boolean',value)
        if kind=='string' and not isinstance(value,str):raise ResponseShapeError(path,'string',value)
        return value
    try:
        result=walk(body,CONTRACTS[name]['response'])
        if type(result)is not dict:raise ValueError('response')
        return result
    except ResponseShapeError as error:raise ReadError('RESPONSE_CONTRACT','Ответ Ozon не соответствует документированным полям',502,diagnostic=error.diagnostic)from None
    except (ValueError,TypeError,InvalidOperation):raise ReadError('RESPONSE_CONTRACT','Ответ Ozon не соответствует документированным полям',502) from None

def rows_of(data):
    for field in ('items','result','statuses'):
        if isinstance(data.get(field),list): return data[field]
    result=data.get('result')
    if isinstance(result,dict):
        for field in ('items','reports'):
            if isinstance(result.get(field),list):return result[field]
        return [result] if result else []
    return [data] if data else []

def attribute_values(attribute):
    """Dictionary ID is authoritative; returned display labels may differ."""
    values=[]
    for entry in attribute.get('values',[]):
        dictionary=entry.get('dictionary_value_id',entry.get('dictionaryValueId',0))
        values.append(('dictionary',str(dictionary)) if str(dictionary) not in ('0','None','') else ('value',entry.get('value')))
    return sorted(values,key=str)

def attributes_match(requested,actual):
    if not isinstance(actual,list):return None
    lookup={}
    for attribute in actual:
        key=(str(attribute.get('id')),str(attribute.get('complex_id',0)))
        if key in lookup:return False
        lookup[key]=attribute
    for attribute in requested:
        key=(str(attribute.get('id')),str(attribute.get('complex_id',0)))
        if key not in lookup or attribute_values(attribute)!=attribute_values(lookup[key]):return False
    return True

def media_verdict(requested,actual):
    """CDN rewrites do not prove source-image identity; literal matches do."""
    if actual is None:return 'unavailable'
    if requested==actual:return 'verified'
    if isinstance(actual,list) and len(actual)==len(requested) and actual and all(report_download(value) for value in actual):return 'transformed'
    return 'mismatch'

def verify_product_fields(requested,attributes,pictures):
    verified=[];unverified=[];mismatched=[]
    for field,value in requested.items():
        if field in ('offer_id','sku','name','price','old_price','currency_code','description_category_id','type_id','vat'):continue
        verdict=None
        if field=='attributes':verdict=attributes_match(value,(attributes or {}).get('attributes'))
        elif field=='complex_attributes':
            flattened=[attribute for group in value for attribute in group.get('attributes',[])]
            verdict=attributes_match(flattened,(attributes or {}).get('complex_attributes'))
        elif field in ('images','primary_image','color_image'):
            actual=(pictures or {}).get({'images':'photo','primary_image':'primary_photo','color_image':'color_photo'}[field])
            expected=value if isinstance(value,list) else [value] if value else []
            media=media_verdict(expected,actual)
            verdict=False if (pictures or {}).get('errors') else True if media=='verified' else False if media=='mismatch' else None
        elif field=='new_description_category_id':
            actual=(attributes or {}).get('description_category_id');verdict=None if actual is None else str(actual)==str(value)
        elif field in ('width','height','depth','weight','dimension_unit','weight_unit','barcode'):
            actual=(attributes or {}).get(field);verdict=None if actual is None else str(actual)==str(value)
        # PDF links, review-promotion flags, geo_names and service_type have no
        # addressable identical source-value readback in these public schemas.
        (verified if verdict is True else mismatched if verdict is False else unverified).append(field)
    return verified,unverified,mismatched

class WarehouseWriteClient:
    def __init__(self,credentials,*,transport=None):
        self.credentials=credentials
        self.client=httpx.AsyncClient(base_url=BASE_URL,headers={'Client-Id':credentials.client_id,'Api-Key':credentials.api_key,'Content-Type':'application/json'},timeout=30,follow_redirects=False,trust_env=False,transport=transport)
    async def send(self,operation,body):
        if type(operation)is not WarehouseWrite:raise WriteError('INVALID_OPERATION','Неизвестная операция',422)
        validate_warehouse_request(operation.name,body)
        try:
            async with self.client.stream('POST',operation.value,json=body) as response:
                if response.status_code in (400,401,403,404,409,422,429):
                    diagnostic=await rejection_diagnostic(response,secrets=(self.credentials.client_id,self.credentials.api_key))
                    raise WriteError('REJECTED','Ozon отклонил отправку; автоматического повтора нет',response.status_code,vendor_error=diagnostic.get('vendor_error'))
                if response.status_code!=200:
                    diagnostic=await rejection_diagnostic(response,secrets=(self.credentials.client_id,self.credentials.api_key))
                    raise WriteError('UNKNOWN_RESULT','Результат отправки неизвестен',502,vendor_error=diagnostic.get('vendor_error'))
                chunks=[];size=0
                async for chunk in response.aiter_bytes():
                    size+=len(chunk)
                    if size>MAX_BYTES:raise ValueError('size')
                    chunks.append(chunk)
                raw=json.loads(b''.join(chunks)or b'{}',parse_float=Decimal)
                if isinstance(raw,dict) and 'code' in raw and 'message' in raw:
                    error=normalize_vendor_error(raw,secrets=(self.credentials.client_id,self.credentials.api_key))
                    raise WriteError('REJECTED',error['message'],502,vendor_error=error)
                result=normalize_warehouse_response(operation.name,raw)
                rows=result.get('result',[]) if isinstance(result.get('result'),list) else result.get('result',{}).get('items',[])
                for row in rows:
                    if isinstance(row,dict) and isinstance(row.get('errors'),list):row['errors']=[normalize_vendor_error(error,secrets=(self.credentials.client_id,self.credentials.api_key)) for error in row['errors'][:10]]
                return result
        except WriteError:raise
        except Exception:raise WriteError('UNKNOWN_RESULT','Связь прервана; сначала проверка результата',502)from None
    async def close(self):await self.client.aclose()

class WarehouseReports(SellerExtensions):
    def __init__(self,accounts,read,*,root=ROOT,writer_factory=WarehouseWriteClient,clock=time.monotonic,blocked=lambda:False,verified_writes=()):
        super().__init__(accounts,read,root=root,path=root/'.cache/warehouse-attempts',writer_factory=writer_factory,clock=clock,verified_writes=verified_writes)
        self.blocked=blocked;self.rate={};self.mock_tasks={};self.mock_reports={};self.mock_products={'DEMO-1':{'id':'101','offer_id':'DEMO-1','sku':'201','name':'Учебный товар','currency_code':'RUB','price':'1000','old_price':'1200','min_price':'800','vat':'0.2'}}

    async def _raw(self,name,body):
        validate_warehouse_request(name,body)
        if self.accounts.mode=='MOCK':return normalize_warehouse_response(name,self._mock_read(name,body))
        return normalize_warehouse_response(name,await self.reader(WarehouseRead[name],deepcopy(body)))

    async def read(self,name,body):
        if name not in WarehouseRead.__members__:raise ReadError('INVALID_OPERATION','Неизвестное чтение',422)
        binding=self.binding();data=await self._raw(name,body)
        if binding!=self.binding():raise ReadError('ACCOUNT_CHANGED','Магазин изменился',409)
        rows=rows_of(data);next_request=None
        result=data.get('result',{})
        if name=='REPORT_LIST' and int(result.get('total',0))>body['page']*body['page_size']:next_request=dict(body,page=body['page']+1)
        if name=='CATEGORY_VALUES' and data.get('has_next'):
            if not rows or int(rows[-1]['id'])==body.get('last_value_id'):raise ReadError('PAGINATION_STALLED','Страница справочника не продвинулась',502)
            next_request=dict(body,last_value_id=int(rows[-1]['id']))
        if name in ('PRODUCT_LIST','PRODUCT_ATTRIBUTES'):
            cursor=(result if name=='PRODUCT_LIST'else data).get('last_id')
            if cursor and rows and (len(rows)>=body.get('limit',100) or name=='PRODUCT_ATTRIBUTES' or body.get('last_id') or int(result.get('total_items',result.get('total',0)))>len(rows)):
                if cursor==body.get('last_id'):raise ReadError('PAGINATION_STALLED','Страница повторилась',502)
                next_request=dict(body,last_id=cursor)
        if name=='PRODUCT_PRICES' and data.get('cursor') and rows:
            if data['cursor']==body.get('cursor'):raise ReadError('PAGINATION_STALLED','Страница цен не продвинулась',502)
            next_request=dict(body,cursor=data['cursor'])
        if name=='CATEGORY_DEPENDENT_VALUES' and data.get('cursor') and rows:
            if data['cursor']==body.get('cursor'):raise ReadError('PAGINATION_STALLED','Страница повторилась',502)
            next_request=dict(body,cursor=data['cursor'])
        for row in rows:
            if 'file'in row:row['download_url']=report_download(row['file'])
        return {'operation':name,'data':data,'rows':rows,'complete':next_request is None,'next_request':next_request,'warnings':[]}

    async def _products(self,items):
        groups={key:[str(item[key])for item in items if item.get(key)]for key in ('offer_id','product_id')}
        result=[]
        for key,values in groups.items():
            if values:
                loaded=rows_of(await self._raw('PRODUCT_INFO',{key:values}))
                if any(not row.get('id') or str(row.get('offer_id' if key=='offer_id' else 'id'))not in set(values)for row in loaded):raise WriteError('INCOMPLETE_SOURCE','Ответ содержит неизвестный товар или не содержит его ID',409)
                result.extend(loaded)
        identities=[str(item.get('id'))for item in result]
        if len(set(identities))!=len(identities):raise WriteError('INCOMPLETE_SOURCE','Повтор товара в свежих данных',409)
        return result

    async def _snapshot(self,name,body):
        if name.startswith('REPORT_'):return {'report_type':name,'request':deepcopy(body)}
        items=body.get('items',body.get('prices',[{'product_id':value}for value in body.get('product_ids',[body.get('product_id')])if value]))
        goods=await self._products(items)
        if name not in ('PRODUCT_IMPORT','PRODUCT_IMPORT_SKU') and len(goods)!=len(items):raise WriteError('INCOMPLETE_SOURCE','Не все товары найдены',409)
        snapshot={'products':goods}
        if name=='PRICE_UPDATE':
            snapshot['prices']=rows_of(await self._raw('PRODUCT_PRICES',{'filter':{'product_id':[str(item['id'])for item in goods],'visibility':'ALL'},'limit':100,'cursor':''}))
            if len(snapshot['prices'])!=len(items):raise WriteError('INCOMPLETE_SOURCE','Свежие цены неполны',409)
            for item in body['prices']:
                match=next((row for row in snapshot['prices']if str(row.get('offer_id'))==str(item.get('offer_id'))or str(row.get('product_id'))==str(item.get('product_id'))),None)
                currency=(match or {}).get('price',{}).get('currency_code')
                if not currency or item.get('currency_code')!=currency:raise WriteError('CURRENCY_UNVERIFIED','Выберите явную валюту, совпадающую со свежими данными Ozon',409)
        if name in ('PRODUCT_IMPORT','PRODUCT_IMPORT_SKU','PRODUCT_UPDATE_ATTRIBUTES','PRODUCT_UPDATE_PICTURES'):
            snapshot['limits']=await self._raw('PRODUCT_LIMITS',{})
            quotas=snapshot['limits']
            for field in ('daily_create','daily_update'):
                quota=quotas.get(field,{})
                requested=len(items)-len(goods)if field=='daily_create'else len(goods)
                if requested and ('limit'not in quota or 'usage'not in quota):raise WriteError('INCOMPLETE_SOURCE','Лимиты загрузки не подтверждены',409)
                if requested and int(quota['usage'])+requested>int(quota['limit']):raise WriteError('PRODUCT_LIMIT','Дневной лимит товаров исчерпан',409)
        if name=='PRODUCT_IMPORT':
            snapshot['attributes']={}
            for item in body['items']:
                category=await self._raw('CATEGORY_ATTRIBUTES',{'description_category_id':item['description_category_id'],'type_id':item['type_id'],'language':'RU'})
                required={int(row['id'])for row in rows_of(category)if row.get('is_required') and int(row['id'])!=8229}
                supplied={attr.get('id')for attr in item.get('attributes',[])}
                if required-supplied:raise WriteError('REQUIRED_ATTRIBUTES','Не заполнены обязательные характеристики категории',422)
                snapshot['attributes'][item['offer_id']]=category
                existing=next((row for row in goods if row.get('offer_id')==item['offer_id']),None)
                if existing and existing.get('currency_code')!=item['currency_code']:raise WriteError('CURRENCY_UNVERIFIED','Валюта существующего товара не совпала со свежим ответом',409)
        if name=='PRICE_TIMER_UPDATE':snapshot['timers']=await self._raw('PRICE_TIMER_STATUS',body)
        return snapshot

    async def preview(self,name,body):
        if name not in WarehouseWrite.__members__:raise WriteError('INVALID_OPERATION','Неизвестная операция',422)
        if self.accounts.mode!='MOCK'and(name in LIVE_BLOCKED or name not in self.verified_writes):raise WriteError('CONTRACT_UNVERIFIED',LIVE_BLOCKED.get(name,'Текущий контракт ещё не подтверждён'),409)
        validate_warehouse_request(name,body);binding=self.binding();before=await self._snapshot(name,body)
        if binding!=self.binding():raise WriteError('ACCOUNT_CHANGED','Магазин изменился',409)
        document={'plan_id':str(uuid4()),'operation':name,'request':deepcopy(body),'before':before,'account_id':binding[0],'revision':binding[1],'api_path':WarehouseWrite[name].value}
        digest=hashlib.sha256(canonical(document).encode()).hexdigest();stored={'document':document,'hash':digest,'created':self.clock(),'attempted':False,'outcome':None}
        self.plans[document['plan_id']]=stored
        return self._public(stored)

    def _reserve_rate(self,doc):
        name=doc['operation'];now=self.clock();window=60 if name=='REPORT_DISCOUNTED' else 86400 if name.startswith('REPORT_PLACEMENT_') else 3600;maximum=1 if name=='REPORT_DISCOUNTED' else 5 if name.startswith('REPORT_PLACEMENT_') else 10
        products=doc['before'].get('products',[])
        keys=[str(next(product['id'] for product in products if product.get('offer_id')==row.get('offer_id') or str(product.get('id'))==str(row.get('product_id')))) for row in doc['request'].get('prices',[])] if name=='PRICE_UPDATE'else[name]
        if name!='PRICE_UPDATE'and name!='REPORT_DISCOUNTED'and not name.startswith('REPORT_PLACEMENT_'):return
        for key in keys:
            rate_key=(doc['account_id'],name,key);values=[stamp for stamp in self.rate.get(rate_key,[])if now-stamp<window]
            if len(values)>=maximum:raise WriteError('LOCAL_RATE_LIMIT','Достигнут лимит отправки; повторите позже',429)
        for key in keys:
            rate_key=(doc['account_id'],name,key);self.rate[rate_key]=[stamp for stamp in self.rate.get(rate_key,[])if now-stamp<window]+[now]

    async def execute(self,plan_id,plan_hash,confirmed):
        if confirmed is not True:raise WriteError('CONFIRM_REQUIRED','Подтвердите неизменяемый план',409)
        async with self.lock:
            stored=self._plan(plan_id,plan_hash);doc=stored['document'];name=doc['operation']
            if self.accounts.mode!='MOCK'and(name in LIVE_BLOCKED or name not in self.verified_writes):raise WriteError('CONTRACT_UNVERIFIED',LIVE_BLOCKED.get(name,'Контракт не подтверждён'),409)
            if stored['attempted']:raise WriteError('ALREADY_ATTEMPTED','Повторная отправка запрещена',409)
            if self.has_unresolved()or self.blocked():raise WriteError('UNRESOLVED','Сначала сверьте предыдущую отправку',409)
            if self.accounts.busy:raise WriteError('BUSY','Другая операция выполняется',409)
            self.accounts.busy=True;writer=None
            try:
                if self.clock()-stored['created']>120:raise WriteError('STALE_PLAN','План устарел',409)
                if await self._snapshot(name,doc['request'])!=doc['before']:raise WriteError('FRESH_CHANGED','Данные изменились, подготовьте новый план',409)
                self._plan(plan_id,plan_hash)
                if self.clock()-stored['created']>120:raise WriteError('STALE_PLAN','План устарел во время проверки',409)
                self._reserve_rate(doc);self._marker(plan_id,'UNKNOWN_RESULT');stored['attempted']=True
                stored['outcome']={'plan_id':plan_id,'plan_hash':plan_hash,'status':'UNKNOWN_RESULT','items':[],'success_count':0,'error_count':0}
                self._save_outcome(stored)
                try:
                    if self.accounts.mode=='MOCK':stored['response']=self._mock_write(name,doc['request'])
                    else:
                        writer=self.writer_factory(self.accounts.credentials());stored['response']=await writer.send(WarehouseWrite[name],deepcopy(doc['request']))
                except WriteError as error:
                    stored['outcome']['transport_error']=getattr(error,'vendor_error',None) or {'code':error.code,'message':error.message}
                    if error.code=='REJECTED':
                        stored['outcome'].update(status='REJECTED',error_count=len(doc['request'].get('items',doc['request'].get('prices',[1]))));self._marker(plan_id,'REJECTED');self._save_outcome(stored);return deepcopy(stored['outcome'])
                except Exception:pass
                return await self._verify(stored)
            finally:
                self.accounts.busy=False
                if writer:await writer.close()

    def has_unresolved(self):
        return super().has_unresolved()or any(stored['outcome']and stored['outcome']['status']=='PENDING'for stored in self.plans.values())or any(event.get('account_id')==self.accounts.active_id and event.get('status')=='PENDING'for event in self.journal.entries())

    async def _verify(self,stored):
        doc=stored['document'];name=doc['operation'];body=doc['request'];response=stored.get('response')or{};items=[];extra={}
        try:
            if name.startswith('REPORT_'):
                code=response.get('code',response.get('result',{}).get('code'))
                if not code:return deepcopy(stored['outcome'])
                result=(await self._raw('REPORT_INFO',{'code':code})).get('result',{})
                if result.get('code')!=code:raise ValueError('report identity')
                status='SUCCESS'if result.get('status')=='success'and report_download(result.get('file'))else'REJECTED'if result.get('status')=='failed'else'PENDING'if result.get('status')in('waiting','processing')else'UNKNOWN_RESULT'
                items=[{'id':code,'status':status,'errors':[self._vendor_error(result['error'] if isinstance(result.get('error'),dict) else {'reason':result.get('error')})] if status=='REJECTED' else []}];extra={'code':code,'download_url':report_download(result.get('file')),'report_status':result.get('status')}
            elif name.startswith('PRODUCT_'):
                task=response.get('task_id',response.get('result',{}).get('task_id'))
                if not task:return deepcopy(stored['outcome'])
                result=(await self._raw('PRODUCT_IMPORT_INFO',{'task_id':int(task)})).get('result',{});rows=result.get('items',[]);lookup={row.get('offer_id'):row for row in rows}
                if len(lookup)!=len(rows):raise ValueError('duplicate')
                updated=await self._products(body['items']) if any(row.get('status')=='imported'for row in rows)else[]
                product_lookup={row['offer_id']:row for row in updated}
                product_ids=[str(row['id'])for row in updated]
                attribute_lookup={};picture_lookup={}
                if product_ids and any(set(item)&{'attributes','complex_attributes','width','height','depth','weight','dimension_unit','weight_unit','barcode','new_description_category_id'} for item in body['items']):
                    try:
                        metadata=rows_of(await self._raw('PRODUCT_ATTRIBUTES',{'filter':{'product_id':product_ids,'visibility':'ALL'},'limit':100,'last_id':''}))
                        attribute_lookup={str(row['id']):row for row in metadata}
                        if len(attribute_lookup)!=len(metadata) or set(attribute_lookup)-set(product_ids):raise ValueError('metadata identity')
                    except ReadError:pass
                if product_ids and any(set(item)&{'images','primary_image','color_image'} for item in body['items']):
                    try:
                        media=rows_of(await self._raw('PRODUCT_PICTURES',{'product_id':product_ids}))
                        picture_lookup={str(row['product_id']):row for row in media}
                        if len(picture_lookup)!=len(media) or set(picture_lookup)-set(product_ids):raise ValueError('picture identity')
                    except ReadError:pass
                for requested in body['items']:
                    row=lookup.get(requested.get('offer_id'),{})
                    status='REJECTED'if row.get('errors')or row.get('status')=='failed'else'SUCCESS'if row.get('status')=='imported'else'PENDING'if row.get('status')in('pending','processing')else'UNKNOWN_RESULT'
                    product=product_lookup.get(requested.get('offer_id'),{})
                    verified_fields=[];unverified_fields=[];mismatched_fields=[]
                    if status=='SUCCESS':
                        if str(product.get('id'))!=str(row.get('product_id')):status='UNKNOWN_RESULT'
                        for field in ('name','price','old_price','currency_code','description_category_id','type_id','vat'):
                            if field not in requested:continue
                            if field=='description_category_id' and requested.get('new_description_category_id'):continue
                            actual=product.get(field)
                            if actual is None or not(Decimal(str(actual))==Decimal(requested[field])if field in MONEY_FIELDS else str(actual)==str(requested[field])):status='UNKNOWN_RESULT'
                            else:verified_fields.append(field)
                        verified,unverified,mismatched=verify_product_fields(requested,attribute_lookup.get(str(product.get('id'))),picture_lookup.get(str(product.get('id'))))
                        verified_fields.extend(verified);unverified_fields.extend(unverified);mismatched_fields.extend(mismatched)
                        if mismatched:status='UNKNOWN_RESULT'
                        elif unverified and status=='SUCCESS':status='ACCEPTED'
                    items.append({'id':requested.get('offer_id',str(requested.get('sku'))),'status':status,'errors':[self._vendor_error(error) for error in row.get('errors',[])[:10]],'verified_fields':verified_fields,'unverified_fields':unverified_fields,'mismatched_fields':mismatched_fields})
                extra={'task_id':str(task),'accepted_count':sum(item['status']=='ACCEPTED'for item in items),'verification_scope':'Статус обработки и ID каждого товара; переданные наблюдаемые поля сверены отдельным чтением. Преобразованные ссылки и недоступные поля не объявляются проверенными. Модерация/публикация отдельно.'}
                if any(item['unverified_fields']for item in items):extra['verification_unavailable']='Ozon обработал задачу; исходные URL после преобразования CDN и недоступные поля не могут быть точно сопоставлены. Список указан для каждого товара.'
            elif name=='PRICE_UPDATE':
                current=await self._snapshot(name,body);lookup={str(row.get('offer_id')):row for row in current['prices']};products={str(row.get('id')):row for row in current['products']}
                outcomes=response.get('result',[])
                unobservable_fields=set()
                for target in body['prices']:
                    key=target.get('offer_id')or products.get(str(target.get('product_id')),{}).get('offer_id');price=lookup.get(str(key),{}).get('price',{});answered=[row for row in outcomes if (key is not None and row.get('offer_id')==key) or (target.get('product_id') is not None and row.get('product_id') is not None and str(row['product_id'])==str(target['product_id']))]
                    rejected=len(answered)==1 and bool(answered[0].get('errors'))
                    checks=[]
                    for field,value in target.items():
                        if field in ('offer_id','product_id'):continue
                        if value=='UNKNOWN' and field.endswith('_enabled'):continue
                        known=CONTRACTS['PRODUCT_PRICES']['response']['properties']['items']['items']['properties']['price']['properties']
                        if field not in known:
                            unobservable_fields.add(field);continue
                        actual=price.get(field)
                        if field=='declared_price' and isinstance(actual,dict):
                            if actual.get('currency')!=target['currency_code']:actual=None
                            else:actual=actual.get('amount')
                        expected=(value=='ENABLED')if field in ('auto_action_enabled','auto_add_to_ozon_actions_list_enabled')else value
                        checks.append(actual is not None and (Decimal(str(actual))==Decimal(value)if field in MONEY_FIELDS or field=='vat'else actual==expected))
                    accepted=len(answered)==1 and answered[0].get('updated')is True
                    unobservable={key for key,value in target.items()if key not in known and key not in ('offer_id','product_id') and value!='UNKNOWN'}
                    status='REJECTED'if rejected else'ACCEPTED'if unobservable and accepted and all(checks)else'SUCCESS'if checks and all(checks)else'UNKNOWN_RESULT'
                    items.append({'id':str(key),'status':status,'errors':[self._vendor_error(error) for error in answered[0].get('errors',[])[:10]] if rejected else []})
                if unobservable_fields:extra={'accepted_count':sum(item['status']=='ACCEPTED'for item in items),'verification_unavailable':'Принятие подтверждено; API чтения не возвращает поля: '+', '.join(sorted(unobservable_fields))}
            elif name=='PRICE_TIMER_UPDATE':
                after=await self._raw('PRICE_TIMER_STATUS',body);previous={str(row['product_id']):row for row in doc['before']['timers'].get('statuses',[])}
                for target in body['product_ids']:
                    row=next((row for row in after.get('statuses',[])if str(row.get('product_id'))==target),{})
                    old=previous.get(target,{}).get('expired_at','');new=row.get('expired_at','');ok=bool(new)and new>old
                    items.append({'id':target,'status':'SUCCESS'if ok else'UNKNOWN_RESULT'})
            else:
                # The documented update response confirms acceptance, while no
                # documented read exposes the exact installed discount percent.
                await self._snapshot(name,body)
                items=[{'id':str(body['product_id']),'status':'ACCEPTED' if response.get('result') is True else 'UNKNOWN_RESULT'}]
                extra={'accepted_count':1 if response.get('result') is True else 0,'verification_unavailable':'Точный установленный процент скидки не возвращается описанными API; подтверждён только приём Ozon.'}
            self._plan(doc['plan_id'],stored['hash'])
        except Exception:
            self._save_outcome(stored)
            return deepcopy(stored['outcome'])
        success=sum(row['status']=='SUCCESS'for row in items);errors=sum(row['status']=='REJECTED'for row in items)
        status='UNKNOWN_RESULT'if any(row['status']=='UNKNOWN_RESULT'for row in items)else'PENDING'if any(row['status']=='PENDING'for row in items)else'ACCEPTED'if any(row['status']=='ACCEPTED'for row in items)and not errors else'SUCCESS'if success==len(items)else'PARTIAL'if success else'REJECTED'
        transport_error=stored['outcome'].get('transport_error')
        stored['outcome']={'plan_id':doc['plan_id'],'plan_hash':stored['hash'],'status':status,'items':items,'success_count':success,'error_count':errors,**extra};self._marker(doc['plan_id'],'UNKNOWN_RESULT'if status=='PENDING'else status)
        if transport_error:stored['outcome']['transport_error']=transport_error
        self._save_outcome(stored)
        return deepcopy(stored['outcome'])

    def _mock_read(self,name,body):
        goods=list(self.mock_products.values())
        if name=='PRODUCT_INFO':
            key=next(iter(body));wanted=set(map(str,body[key]));selected=[deepcopy(row) for row in goods if str(row.get('id'if key=='product_id'else key))in wanted]
            for row in selected:
                if 'declared_price'in row:row['declared_price']={'amount':row['declared_price'],'currency':row['currency_code']}
                for field in ('primary_image','color_image'):
                    if isinstance(row.get(field),str):row[field]=[row[field]] if row[field] else []
            return {'items':selected}
        if name=='PRODUCT_LIST':return {'result':{'items':[{'product_id':row['id'],'offer_id':row['offer_id'],'sku':row['sku']}for row in goods],'total_items':len(goods),'last_id':''}}
        if name=='PRODUCT_PRICES':
            filters=body.get('filter',{});wanted=filters.get('product_id');goods=[row for row in goods if not wanted or row['id']in list(map(str,wanted))]
            def mock_price(row):
                result={key:row[key]for key in row if key in MONEY_FIELDS or key in ('currency_code','vat','auto_action_enabled','auto_add_to_ozon_actions_list_enabled')}
                if 'declared_price'in result:result['declared_price']={'amount':result['declared_price'],'currency':row['currency_code']}
                for field in ('auto_action_enabled','auto_add_to_ozon_actions_list_enabled'):
                    if field in result:result[field]=result[field]=='ENABLED'
                return result
            return {'items':[{'product_id':row['id'],'offer_id':row['offer_id'],'price':mock_price(row)}for row in goods],'cursor':''}
        if name=='PRODUCT_LIMITS':return {'daily_create':{'limit':1000,'usage':0},'daily_update':{'limit':1000,'usage':0},'total':{'limit':10000,'usage':len(goods)}}
        if name=='CATEGORY_TREE':return {'result':[{'description_category_id':11,'category_name':'Учебная категория','disabled':False,'children':[{'type_id':12,'type_name':'Учебный тип','disabled':False,'children':[]}]}]}
        if name=='CATEGORY_ATTRIBUTES':return {'result':[{'id':85,'name':'Бренд','is_required':True,'dictionary_id':1,'is_collection':False}]}
        if name in ('CATEGORY_VALUES','CATEGORY_SEARCH'):return {'result':[{'id':15,'value':'Учебный бренд','info':'','picture':''}],'has_next':False}
        if name=='CATEGORY_DEPENDENT':return {'result':[{'parent_attribute_id':85,'child_attribute_id':10096}]}
        if name=='CATEGORY_DEPENDENT_VALUES':return {'result':[],'cursor':''}
        if name=='PRODUCT_IMPORT_INFO':return {'result':{'items':deepcopy(self.mock_tasks.get(str(body['task_id']),[])),'total':len(self.mock_tasks.get(str(body['task_id']),[]))}}
        if name=='PRODUCT_ATTRIBUTES':
            wanted=set(body.get('filter',{}).get('product_id',[row['id']for row in goods]))
            result=[]
            for row in goods:
                if row['id']not in wanted:continue
                item=deepcopy(row);item['attributes']=row.get('attributes',[])
                item['complex_attributes']=[deepcopy(attribute) for group in row.get('complex_attributes',[])for attribute in group.get('attributes',[])]
                for attribute in item['complex_attributes']:
                    for entry in attribute.get('values',[]):
                        if 'dictionary_value_id'in entry:entry['dictionaryValueId']=entry.pop('dictionary_value_id')
                result.append(item)
            return {'result':result,'last_id':''}
        if name=='PRODUCT_PICTURES':return {'items':[{'product_id':row['id'],'photo':row.get('images',[]),'primary_photo':[row['primary_image']]if row.get('primary_image')else[],'color_photo':[row['color_image']]if row.get('color_image')else[]}for row in goods if row['id']in body['product_id']]}
        if name=='PRODUCT_DESCRIPTION':return {'result':{'id':101,'offer_id':'DEMO-1','name':'Учебный товар','description':'Описание учебного товара'}}
        if name=='PRICE_TIMER_STATUS':return {'statuses':[{'product_id':value,'expired_at':self.mock_products.get('DEMO-1',{}).get('expired_at','2026-10-10T00:00:00Z'),'min_price_for_auto_actions_enabled':True}for value in body['product_ids']]}
        if name=='PRICE_DISCOUNTED':return {'items':[]}
        if name=='REPORT_LIST':return {'result':{'reports':list(self.mock_reports.values()),'total':len(self.mock_reports)}}
        if name=='REPORT_INFO':return {'result':self.mock_reports.get(body['code'],{})}
        return {}

    def _mock_write(self,name,body):
        if name.startswith('REPORT_'):
            code='SYNTHETIC-'+uuid4().hex;self.mock_reports[code]={'code':code,'status':'success','file':'https://ir.ozone.ru/s3/synthetic/report.xlsx','report_type':name,'created_at':'2026-10-09T00:00:00Z','expires_at':'2026-10-10T00:00:00Z'};return {'code':code}
        if name.startswith('PRODUCT_'):
            task=str(len(self.mock_tasks)+1);rows=[]
            for item in body['items']:
                offer=item.get('offer_id')or'SYNTHETIC-SKU-'+str(item['sku']);current=self.mock_products.get(offer,{'id':str(101+len(self.mock_products)),'sku':str(201+len(self.mock_products))});self.mock_products[offer]={**current,**deepcopy(item),'offer_id':offer};rows.append({'offer_id':offer,'product_id':current['id'],'status':'imported','errors':[]})
            self.mock_tasks[task]=rows;return {'task_id':task}
        if name=='PRICE_UPDATE':
            result=[]
            for item in body['prices']:
                offer=item.get('offer_id')or next(key for key,row in self.mock_products.items()if row['id']==str(item['product_id']));self.mock_products[offer].update(deepcopy(item));result.append({'offer_id':offer,'product_id':self.mock_products[offer]['id'],'updated':True,'errors':[]})
            return {'result':result}
        if name=='PRICE_TIMER_UPDATE':self.mock_products['DEMO-1']['expired_at']='2026-11-09T00:00:00Z';return {}
        return {'result':True}
