import asyncio
from copy import deepcopy
from types import SimpleNamespace
import httpx
import pytest
from app.connection import Credentials
from app.ozon_read import ReadError
from app.ozon_write import WriteError
from app.warehouse_reports import WarehouseReports,WarehouseWrite,WarehouseWriteClient,validate_warehouse_request,report_download,normalize_warehouse_response,rows_of

ATTR=[{'id':85,'values':[{'dictionary_value_id':15,'value':'Учебный бренд'}]}]
WRITES={
 'PRODUCT_IMPORT':{'items':[{'offer_id':'NEW-1','name':'Новый учебный товар','price':'1250.01','currency_code':'RUB','description_category_id':11,'type_id':12,'attributes':ATTR}]},
 'PRODUCT_IMPORT_SKU':{'items':[{'offer_id':'NEW-SKU','sku':301,'price':'1500','currency_code':'RUB'}]},
 'PRODUCT_UPDATE_ATTRIBUTES':{'items':[{'offer_id':'DEMO-1','attributes':ATTR}]},
 'PRODUCT_UPDATE_PICTURES':{'items':[{'offer_id':'DEMO-1','images':['https://example.org/picture.png']}]},
 'PRICE_UPDATE':{'prices':[{'offer_id':'DEMO-1','price':'999.99','old_price':'1200','currency_code':'RUB'}]},
 'PRICE_TIMER_UPDATE':{'product_ids':['101']},
 'PRICE_DISCOUNT_UPDATE':{'product_id':101,'discount':10},
 'REPORT_PRODUCTS':{'language':'RU'},
 'REPORT_RETURNS':{'filter':{'date_from':'2026-10-01T00:00:00Z','date_to':'2026-10-09T00:00:00Z','status':'ReceivedBySeller','delivery_schema':'ALL'},'language':'RU'},
 'REPORT_POSTINGS':{'filter':{'processed_at_from':'2026-10-01T00:00:00Z','processed_at_to':'2026-10-09T00:00:00Z','delivery_schema':['fbs']},'language':'RU'},
 'REPORT_DISCOUNTED':{},
 'REPORT_WAREHOUSE_STOCK':{'warehouseId':['1'],'language':'RU'},
 'REPORT_PLACEMENT_BY_PRODUCTS':{'date_from':'2026-10-01','date_to':'2026-10-09'},
 'REPORT_PLACEMENT_BY_SUPPLIES':{'date_from':'2026-10-01','date_to':'2026-10-09'},
 'REPORT_MARKED_PRODUCTS_SALES':{'date':{'from':'2026-10-01','to':'2026-10-09'}},
}
def service(tmp_path,**kwargs):
 accounts=SimpleNamespace(mode='MOCK',active_id='synthetic-account',revision=1,busy=False,credentials=lambda:Credentials('1','synthetic-key'))
 async def no_network(*args):raise AssertionError('network in MOCK')
 return WarehouseReports(accounts,no_network,root=tmp_path,**kwargs)

@pytest.mark.parametrize('name',WRITES)
def test_all_fifteen_one_send_and_verified_result(tmp_path,name):
 async def run():
  subject=service(tmp_path);plan=await subject.preview(name,WRITES[name]);result=await subject.execute(plan['plan_id'],plan['plan_hash'],True)
  assert result['status']==('ACCEPTED'if name=='PRICE_DISCOUNT_UPDATE'else'SUCCESS')
  assert not subject.accounts.busy
  with pytest.raises(WriteError,match='Повторная'):await subject.execute(plan['plan_id'],plan['plan_hash'],True)
 asyncio.run(run())

@pytest.mark.parametrize('body',[
 {'prices':[{'offer_id':'DEMO-1','price':999.99,'currency_code':'RUB'}]},
 {'prices':[{'offer_id':'DEMO-1','product_id':101,'price':'999','currency_code':'RUB'}]},
 {'prices':[{'offer_id':'DEMO-1','price':'NaN','currency_code':'RUB'}]},
 {'prices':[{'offer_id':'DEMO-1','price':'0','currency_code':'RUB'}]},
 {'prices':[{'offer_id':'DEMO-1','price':'1000','min_price':'1001','currency_code':'RUB'}]},
 {'prices':[{'offer_id':'DEMO-1','price':'1000','old_price':'1000','currency_code':'RUB'}]},
 {'prices':[{'offer_id':'DEMO-1','currency_code':'RUB'}]},
 {'prices':[]},
 {'prices':[{'offer_id':'DEMO-1','price':'999','currency_code':'RUB','unsupported':True}]},
])
def test_invalid_money_or_selector(body):
 with pytest.raises(ReadError):validate_warehouse_request('PRICE_UPDATE',body)

def test_currency_and_required_category_attributes(tmp_path):
 async def run():
  subject=service(tmp_path);body=deepcopy(WRITES['PRICE_UPDATE']);body['prices'][0]['currency_code']='USD'
  with pytest.raises(WriteError,match='валюту'):await subject.preview('PRICE_UPDATE',body)
  body=deepcopy(WRITES['PRODUCT_IMPORT']);body['items'][0]['attributes']=[]
  with pytest.raises(WriteError,match='обязательные'):await subject.preview('PRODUCT_IMPORT',body)
 asyncio.run(run())

def test_fresh_change_account_binding_and_hash(tmp_path):
 async def run():
  subject=service(tmp_path);plan=await subject.preview('PRICE_UPDATE',WRITES['PRICE_UPDATE'])
  with pytest.raises(WriteError):await subject.execute(plan['plan_id'],'0'*64,True)
  subject.accounts.revision+=1
  with pytest.raises(WriteError,match='Магазин'):await subject.execute(plan['plan_id'],plan['plan_hash'],True)
  subject.accounts.revision-=1;subject.mock_products['DEMO-1']['price']='1001'
  with pytest.raises(WriteError,match='Данные изменились'):await subject.execute(plan['plan_id'],plan['plan_hash'],True)
  assert subject.plans[plan['plan_id']]['attempted']is False
 asyncio.run(run())

def test_timeout_pending_reconciliation_and_cross_guard(tmp_path):
 async def run():
  subject=service(tmp_path);plan=await subject.preview('PRODUCT_IMPORT',WRITES['PRODUCT_IMPORT']);original=subject._mock_write
  def pending(name,body):
   response=original(name,body);subject.mock_tasks[str(response['task_id'])][0]['status']='pending';return response
  subject._mock_write=pending;result=await subject.execute(plan['plan_id'],plan['plan_hash'],True)
  assert result['status']=='PENDING'and subject.has_unresolved()
  later=await subject.preview('REPORT_PRODUCTS',WRITES['REPORT_PRODUCTS'])
  with pytest.raises(WriteError,match='Сначала'):await subject.execute(later['plan_id'],later['plan_hash'],True)
  subject.mock_tasks[result['task_id']][0]['status']='imported';assert(await subject.reconcile(plan['plan_id'],plan['plan_hash']))['status']=='SUCCESS'
 asyncio.run(run())

def test_unknown_send_never_retries(tmp_path):
 async def run():
  subject=service(tmp_path);calls=[]
  def timeout(*args):calls.append(1);raise TimeoutError()
  subject._mock_write=timeout;plan=await subject.preview('REPORT_PRODUCTS',WRITES['REPORT_PRODUCTS'])
  assert(await subject.execute(plan['plan_id'],plan['plan_hash'],True))['status']=='UNKNOWN_RESULT'
  assert(await subject.reconcile(plan['plan_id'],plan['plan_hash']))['status']=='UNKNOWN_RESULT'
  with pytest.raises(WriteError):await subject.execute(plan['plan_id'],plan['plan_hash'],True)
  assert len(calls)==1
 asyncio.run(run())

@pytest.mark.parametrize('url',['http://ir.ozone.ru/a','https://ozone.ru.evil.org/a','https://user:pass@ir.ozone.ru/a','https://127.0.0.1/a','https://ir.ozone.ru:444/a','javascript:alert(1)'])
def test_report_download_safelink(url):assert report_download(url)is None

def test_safe_report_and_exact_response():
 assert report_download('https://ir.ozone.ru/s3/report.csv')=='https://ir.ozone.ru/s3/report.csv'
 value=normalize_warehouse_response('PRODUCT_PRICES',{'items':[{'product_id':9223372036854775806,'price':{'price':'999.99'}}],'unexpected_secret':'no'})
 assert value['items'][0]['product_id']=='9223372036854775806'and'unexpected_secret'not in value

def test_reports_limits_and_period(tmp_path):
 async def run():
  subject=service(tmp_path)
  for _ in range(2):
   plan=await subject.preview('REPORT_DISCOUNTED',{})
   if _: 
    with pytest.raises(WriteError,match='лимит'):await subject.execute(plan['plan_id'],plan['plan_hash'],True)
   else:assert(await subject.execute(plan['plan_id'],plan['plan_hash'],True))['status']=='SUCCESS'
 asyncio.run(run())
 with pytest.raises(ReadError):validate_warehouse_request('REPORT_PLACEMENT_BY_PRODUCTS',{'date_from':'2026-01-01','date_to':'2026-02-01'})

def test_live_closed_transport_decimal_and_no_retry():
 async def run():
  requests=[]
  async def transport(request):requests.append(request);return httpx.Response(200,json={'result':[{'offer_id':'DEMO-1','product_id':101,'updated':True,'errors':[]}]})
  client=WarehouseWriteClient(Credentials('1','synthetic-key'),transport=httpx.MockTransport(transport))
  try:
   result=await client.send(WarehouseWrite.PRICE_UPDATE,WRITES['PRICE_UPDATE']);assert result['result'][0]['updated']is True
   assert b'"999.99"'in requests[0].content and len(requests)==1
   with pytest.raises(WriteError):await client.send('/arbitrary',{})
  finally:await client.close()
 asyncio.run(run())

READS={
 'CATEGORY_TREE':{'language':'RU'},'CATEGORY_ATTRIBUTES':{'description_category_id':11,'type_id':12},
 'CATEGORY_VALUES':{'description_category_id':11,'type_id':12,'attribute_id':85,'limit':100},
 'CATEGORY_SEARCH':{'description_category_id':11,'type_id':12,'attribute_id':85,'limit':100,'value':'Учебный'},
 'CATEGORY_DEPENDENT':{'description_category_id':11,'type_id':12},
 'CATEGORY_DEPENDENT_VALUES':{'parent_attribute_id':85,'child_attribute_id':10096,'limit':100},
 'PRODUCT_IMPORT_INFO':{'task_id':1},'PRODUCT_ATTRIBUTES':{'filter':{'visibility':'ALL'},'limit':1000},
 'PRODUCT_LIMITS':{},'PRODUCT_DESCRIPTION':{'offer_id':'DEMO-1'},'PRODUCT_PICTURES':{'product_id':['101']},
 'PRICE_TIMER_STATUS':{'product_ids':['101']},'PRICE_DISCOUNTED':{'discounted_skus':['201']},
 'REPORT_LIST':{'page':1,'page_size':100,'report_type':'ALL'},'REPORT_INFO':{'code':'SYNTHETIC'},
 'PRODUCT_LIST':{'filter':{'visibility':'ALL'},'limit':1000},'PRODUCT_INFO':{'offer_id':['DEMO-1']},
 'PRODUCT_PRICES':{'filter':{'visibility':'ALL'},'limit':1000},
}
@pytest.mark.parametrize('name',READS)
def test_all_eighteen_read_contracts_no_network(tmp_path,name):
 async def run():
  result=await service(tmp_path).read(name,READS[name]);assert result['operation']==name and isinstance(result['rows'],list)
 asyncio.run(run())

def test_real_unknown_marker_recovery_privacy(tmp_path):
 subject=service(tmp_path);subject.accounts.mode='LIVE_READ';subject._marker('00000000-0000-4000-8000-000000000003','UNKNOWN_RESULT')
 marker=(tmp_path/'.cache/warehouse-attempts/00000000-0000-4000-8000-000000000003.json').read_text('utf8')
 assert 'synthetic-account'not in marker and 'DEMO-1'not in marker and 'price'not in marker
 restored=WarehouseReports(subject.accounts,subject.reader,root=tmp_path)
 assert restored.has_unresolved()and restored.history()['requires_manual_reconciliation']

def test_stale_ttl_and_concurrent_execution(tmp_path):
 async def run():
  clock=[0];subject=service(tmp_path,clock=lambda:clock[0]);plan=await subject.preview('PRICE_UPDATE',WRITES['PRICE_UPDATE']);clock[0]=121
  with pytest.raises(WriteError,match='устарел'):await subject.execute(plan['plan_id'],plan['plan_hash'],True)
  clock[0]=0;plan=await subject.preview('PRICE_UPDATE',WRITES['PRICE_UPDATE'])
  results=await asyncio.gather(subject.execute(plan['plan_id'],plan['plan_hash'],True),subject.execute(plan['plan_id'],plan['plan_hash'],True),return_exceptions=True)
  assert sum(isinstance(result,WriteError)for result in results)==1 and sum(isinstance(result,dict)and result['status']=='SUCCESS'for result in results)==1
 asyncio.run(run())

@pytest.mark.parametrize('extra,expected',[
 ({'auto_add_to_ozon_actions_list_enabled':'ENABLED'},'SUCCESS'),
 ({'declared_price':'777.77','vat':'0.2'},'SUCCESS'),
 ({'price_strategy_enabled':'ENABLED'},'ACCEPTED'),
 ({'quant_size':2},'ACCEPTED'),
])
def test_price_flags_declared_amount_and_unobservable_acceptance(tmp_path,extra,expected):
 async def run():
  subject=service(tmp_path);body=deepcopy(WRITES['PRICE_UPDATE']);body['prices'][0].update(extra)
  plan=await subject.preview('PRICE_UPDATE',body);result=await subject.execute(plan['plan_id'],plan['plan_hash'],True)
  assert result['status']==expected
  if expected=='ACCEPTED':assert result['success_count']==0 and result['accepted_count']==1 and result['verification_unavailable']
 asyncio.run(run())

@pytest.mark.parametrize('operation,read_name,change,expected,field',[
 ('PRODUCT_UPDATE_ATTRIBUTES','PRODUCT_ATTRIBUTES','mismatch','UNKNOWN_RESULT','attributes'),
 ('PRODUCT_UPDATE_ATTRIBUTES','PRODUCT_ATTRIBUTES','missing','ACCEPTED','attributes'),
 ('PRODUCT_IMPORT','PRODUCT_ATTRIBUTES','mismatch','UNKNOWN_RESULT','attributes'),
 ('PRODUCT_IMPORT','PRODUCT_ATTRIBUTES','missing','ACCEPTED','attributes'),
 ('PRODUCT_UPDATE_PICTURES','PRODUCT_PICTURES','mismatch','UNKNOWN_RESULT','images'),
 ('PRODUCT_UPDATE_PICTURES','PRODUCT_PICTURES','missing','ACCEPTED','images'),
 ('PRODUCT_UPDATE_PICTURES','PRODUCT_PICTURES','cdn','ACCEPTED','images'),
 ('PRODUCT_UPDATE_PICTURES','PRODUCT_PICTURES','errors','UNKNOWN_RESULT','images'),
])
def test_imported_task_does_not_prove_attributes_or_images(tmp_path,operation,read_name,change,expected,field):
 async def run():
  subject=service(tmp_path);original=subject._mock_read
  def changed_read(name,body):
   data=original(name,body)
   if name==read_name:
    row=data['result'if name=='PRODUCT_ATTRIBUTES'else'items'][0]
    if change=='missing':row.pop('attributes'if name=='PRODUCT_ATTRIBUTES'else'photo',None)
    elif change=='mismatch' and name=='PRODUCT_ATTRIBUTES':row['attributes']=[{'id':85,'values':[{'dictionary_value_id':999,'value':'Другое значение'}]}]
    elif change=='mismatch':row['photo']=['https://example.org/old-picture.png']
    elif change=='cdn':row['photo']=['https://ir.ozone.ru/s3/transformed/picture.png']
    elif change=='errors':row['errors']=[{'message':'Synthetic image failure','url':'https://example.org/picture.png'}]
   return data
  subject._mock_read=changed_read;plan=await subject.preview(operation,WRITES[operation]);result=await subject.execute(plan['plan_id'],plan['plan_hash'],True)
  assert result['status']==expected and result['success_count']==0
  assert field in result['items'][0]['unverified_fields'if expected=='ACCEPTED'else'mismatched_fields']
  if expected=='ACCEPTED':assert result['accepted_count']==1 and result['verification_scope'] and result['verification_unavailable']
 asyncio.run(run())

def test_complex_attributes_dimensions_and_exact_images_verified(tmp_path):
 async def run():
  subject=service(tmp_path);body=deepcopy(WRITES['PRODUCT_IMPORT']);body['items'][0].update(complex_attributes=[{'attributes':[{'id':21,'complex_id':100,'values':[{'dictionary_value_id':22,'value':'Учебный вариант'}]}]}],width=10,height=20,depth=30,weight=100,dimension_unit='mm',weight_unit='g',images=['https://example.org/a.png'],primary_image='https://example.org/a.png',color_image='https://example.org/color.png')
  plan=await subject.preview('PRODUCT_IMPORT',body);result=await subject.execute(plan['plan_id'],plan['plan_hash'],True)
  assert result['status']=='SUCCESS',result
  assert set(result['items'][0]['verified_fields'])>={'complex_attributes','images','primary_image','color_image','width','height','weight'}
 asyncio.run(run())

def test_import_unobservable_fields_are_accepted_not_success(tmp_path):
 async def run():
  subject=service(tmp_path);body=deepcopy(WRITES['PRODUCT_IMPORT']);body['items'][0]['service_type']='IS_CODE_SERVICE'
  plan=await subject.preview('PRODUCT_IMPORT',body);result=await subject.execute(plan['plan_id'],plan['plan_hash'],True)
  assert result['status']=='ACCEPTED'and result['items'][0]['unverified_fields']==['service_type']
 asyncio.run(run())

def test_response_drift_diagnostic_has_only_public_path_and_types():
 with pytest.raises(ReadError)as captured:
  normalize_warehouse_response('PRODUCT_LIMITS',{'operation_limits':['synthetic-private-value']})
 assert captured.value.diagnostic=={'field_path':'$.operation_limits[0]','expected':'object','actual_type':'string'}
 assert 'synthetic-private-value'not in str(captured.value.diagnostic)

def test_limit_shape_diagnostic_excludes_values_and_arbitrary_keys():
 with pytest.raises(ReadError)as captured:
  normalize_warehouse_response('PRODUCT_LIMITS',{'operation_limits':[{'limit':987654321,'limit_type':'unknown-private-enum','synthetic-private-key':'synthetic-private-value'}]})
 diagnostic=captured.value.diagnostic
 assert diagnostic=={'field_path':'$.operation_limits[0].limit_type','expected':'documented limit_type enum','actual_type':'string'}
 assert all(value not in str(diagnostic)for value in ('987654321','unknown-private-enum','synthetic-private-key','synthetic-private-value'))

def test_current_attributes_integer_sku_is_exact_string():
 result=normalize_warehouse_response('PRODUCT_ATTRIBUTES',{'result':[{'id':101,'offer_id':'DEMO-1','sku':9223372036854775806}]})
 assert result['result'][0]['sku']=='9223372036854775806'
 assert normalize_warehouse_response('PRODUCT_ATTRIBUTES',{'result':[{'sku':'201'}]})['result'][0]['sku']=='201'
 for invalid in (201.5,True,2**63):
  with pytest.raises(ReadError)as captured:normalize_warehouse_response('PRODUCT_ATTRIBUTES',{'result':[{'sku':invalid}]})
  assert captured.value.diagnostic['field_path']=='$.result[0].sku'

def test_current_attributes_integer_total_is_exact_string():
 for value in (0,9223372036854775806):
  assert normalize_warehouse_response('PRODUCT_ATTRIBUTES',{'total':value})['total']==str(value)
 assert normalize_warehouse_response('PRODUCT_ATTRIBUTES',{'total':'123'})['total']=='123'
 for invalid in (-1,1.5,True,2**63):
  with pytest.raises(ReadError)as captured:normalize_warehouse_response('PRODUCT_ATTRIBUTES',{'total':invalid})
  assert captured.value.diagnostic['field_path']=='$.total'

def test_current_operation_limits_array_validates_exact_public_item_shape():
 body={'operation_limits':[{'limit':9223372036854775806,'limit_type':'RATE_LIMIT_PER_MINUTE'},{'limit':0,'limit_type':'UNSPECIFIED'}]}
 assert normalize_warehouse_response('PRODUCT_LIMITS',body)['operation_limits']==[{'limit':'9223372036854775806','limit_type':'RATE_LIMIT_PER_MINUTE'},{'limit':'0','limit_type':'UNSPECIFIED'}]
 assert normalize_warehouse_response('PRODUCT_LIMITS',{'operation_limits':[]})['operation_limits']==[]
 assert normalize_warehouse_response('PRODUCT_LIMITS',{'operation_limits':{'limit':100,'limit_type':'RATE_LIMIT_PER_MINUTE'}})['operation_limits']['limit']=='100'

@pytest.mark.parametrize('invalid',[
 {'limit':1,'limit_type':'OTHER'},{'limit':False,'limit_type':'UNSPECIFIED'},
 {'limit':1.5,'limit_type':'UNSPECIFIED'},{'limit':-1,'limit_type':'UNSPECIFIED'},
 {'limit':2**63,'limit_type':'UNSPECIFIED'},{'limit_type':'UNSPECIFIED'},
 {'limit':1},{'limit':None,'limit_type':'UNSPECIFIED'},
])
def test_operation_limits_array_checks_every_item(invalid):
 with pytest.raises(ReadError)as captured:
  normalize_warehouse_response('PRODUCT_LIMITS',{'operation_limits':[{'limit':1,'limit_type':'RATE_LIMIT_PER_MINUTE'},invalid]})
 assert captured.value.diagnostic['field_path'].startswith('$.operation_limits[1].')

def test_operation_limits_alternate_array_does_not_relax_other_object_fields():
 with pytest.raises(ReadError)as captured:normalize_warehouse_response('PRODUCT_LIMITS',{'daily_create':[]})
 assert captured.value.diagnostic['field_path']=='$.daily_create'

@pytest.mark.parametrize('empty',[{'result':{}},{'result':{'items':[]}},{'result':{'reports':[]}}])
def test_empty_envelope_stays_empty(empty):
 assert rows_of(empty)==[]
