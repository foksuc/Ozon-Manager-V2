"""Route acceptance uses synthetic accounts, never keys or an Ozon connection."""
import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import Store
from app.connection import Connection
from test_warehouse_reports import WRITES

def client(tmp_path,monkeypatch):
 async def forbidden(*args,**kwargs):raise AssertionError('MOCK network')
 monkeypatch.setattr(httpx.AsyncClient,'send',forbidden)
 return TestClient(create_app(Store(tmp_path/'synthetic.sqlite3',root=tmp_path),connection=Connection('MOCK',None)),base_url='http://localhost')

@pytest.mark.parametrize('name',WRITES)
def test_all_new_http_routes(tmp_path,monkeypatch,name):
 with client(tmp_path,monkeypatch)as http:
  prepared=http.post('/api/warehouse/preview',json={'operation':name,'request':WRITES[name]})
  assert prepared.status_code==200,prepared.text
  plan=prepared.json();confirmation={'plan_id':plan['plan_id'],'plan_hash':plan['plan_hash'],'confirmed':True}
  assert http.post('/api/warehouse/execute',json=confirmation|{'request':{'price':'0'}}).status_code==422
  sent=http.post('/api/warehouse/execute',json=confirmation);assert sent.status_code==200,sent.text
  assert sent.json()['status']==('ACCEPTED'if name=='PRICE_DISCOUNT_UPDATE'else'SUCCESS')
  assert http.post('/api/warehouse/execute',json=confirmation).status_code==409
  assert http.get('/api/warehouse/history').json()['items'][0]['plan_id']==plan['plan_id']

def test_closed_reads_and_contracts(tmp_path,monkeypatch):
 with client(tmp_path,monkeypatch)as http:
  contracts=http.get('/api/warehouse/contracts').json();assert len(contracts['operations'])==33
  assert http.post('/api/warehouse/read',json={'operation':'REPORT_PRODUCTS','request':{}}).status_code==422
  assert http.post('/api/warehouse/read',json={'operation':'https://evil.invalid','request':{}}).status_code==422
  response=http.post('/api/warehouse/read',json={'operation':'PRODUCT_PRICES','request':{'filter':{'visibility':'ALL'},'limit':1000}})
  assert response.status_code==200,response.text
  assert response.json()['rows'][0]['price']['price']=='1000'

def test_limits_top_level_object_is_visible_in_read_table(tmp_path,monkeypatch):
 with client(tmp_path,monkeypatch)as http:
  response=http.post('/api/warehouse/read',json={'operation':'PRODUCT_LIMITS','request':{}})
  assert response.status_code==200,response.text
  result=response.json()
  assert result['complete'] is True and result['rows']==[result['data']]
  assert len(result['rows'])==1 and result['rows'][0]['daily_create']['limit']=='1000'
