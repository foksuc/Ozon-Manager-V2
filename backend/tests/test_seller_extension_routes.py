"""Real HTTP routes, synthetic fixtures only; never use seller credentials."""
import asyncio
from copy import deepcopy
import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import Store
from app.connection import Connection
from app.ozon_write import WriteError
from test_seller_extensions import READ_REQUESTS, WRITE_REQUESTS


def client(tmp_path,monkeypatch):
    async def forbidden(*args,**kwargs): raise AssertionError('MOCK must not access network')
    monkeypatch.setattr(httpx.AsyncClient,'send',forbidden)
    return TestClient(create_app(Store(tmp_path/'synthetic.sqlite3',root=tmp_path),connection=Connection('MOCK',None)),base_url='http://localhost')


@pytest.mark.parametrize('name',READ_REQUESTS)
def test_all_read_routes(tmp_path,monkeypatch,name):
    with client(tmp_path,monkeypatch) as http:
        result=http.post('/api/seller/read',json={'operation':name,'request':READ_REQUESTS[name]})
        assert result.status_code==200,result.text
        assert result.json()['operation']==name and isinstance(result.json()['rows'],list)


@pytest.mark.parametrize('name',WRITE_REQUESTS)
def test_all_strategy_routes_confirm_and_history(tmp_path,monkeypatch,name):
    with client(tmp_path,monkeypatch) as http:
        response=http.post('/api/seller/preview',json={'operation':name,'request':WRITE_REQUESTS[name]})
        assert response.status_code==200,response.text
        plan=response.json()
        confirm={'plan_id':plan['plan_id'],'plan_hash':plan['plan_hash'],'confirmed':True}
        response=http.post('/api/seller/execute',json=confirm)
        assert response.status_code==200,response.text
        result=response.json()
        assert result['status']=='SUCCESS' and result['success_count']>0
        assert http.post('/api/seller/execute',json=confirm).status_code==409
        history=http.get('/api/seller/history').json()
        assert history['items'][0]['plan_id']==plan['plan_id']
        reconcile=http.post('/api/seller/reconcile',json=confirm)
        assert reconcile.status_code==200 and reconcile.json()['status']=='SUCCESS'


def test_browser_cannot_replace_confirmed_body_or_expand_operations(tmp_path,monkeypatch):
    with client(tmp_path,monkeypatch) as http:
        plan=http.post('/api/seller/preview',json={'operation':'STRATEGY_STATUS','request':WRITE_REQUESTS['STRATEGY_STATUS']}).json()
        confirm={'plan_id':plan['plan_id'],'plan_hash':plan['plan_hash'],'confirmed':True}
        assert http.post('/api/seller/execute',json=confirm|{'request':{'enabled':False}}).status_code==422
        assert http.post('/api/seller/execute',json=confirm|{'plan_hash':'0'*64}).status_code==409
        assert http.post('/api/seller/execute',json=confirm|{'confirmed':False}).status_code==409
        assert http.post('/api/seller/read',json={'operation':'STRATEGY_CREATE','request':WRITE_REQUESTS['STRATEGY_CREATE']}).status_code==422
        assert http.post('/api/seller/read',json={'operation':'https://evil.invalid','request':{}}).status_code==422
        assert http.post('/api/seller/preview',json={'operation':'STRATEGY_STATUS','request':WRITE_REQUESTS['STRATEGY_STATUS']|{'api_key':'synthetic-injection'}}).status_code==422
        assert http.post('/api/seller/execute',json=confirm).json()['status']=='SUCCESS'


def test_route_account_revision_blocks_old_plan(tmp_path,monkeypatch):
    with client(tmp_path,monkeypatch) as http:
        plan=http.post('/api/seller/preview',json={'operation':'STRATEGY_STATUS','request':WRITE_REQUESTS['STRATEGY_STATUS']}).json()
        http.app.state.accounts.revision+=1
        response=http.post('/api/seller/execute',json={'plan_id':plan['plan_id'],'plan_hash':plan['plan_hash'],'confirmed':True})
        assert response.status_code==409 and response.json()['code']=='ACCOUNT_CHANGED'


def test_route_reconcile_after_ttl_does_not_resend(tmp_path,monkeypatch):
    with client(tmp_path,monkeypatch) as http:
        service=http.app.state.seller_extensions
        now=[0];service.clock=lambda:now[0]
        plan=http.post('/api/seller/preview',json={'operation':'STRATEGY_STATUS','request':WRITE_REQUESTS['STRATEGY_STATUS']}).json()
        original=service._mock_write
        sends=[]
        def lost(name,request):
            sends.append(name)
            raise WriteError('UNKNOWN_RESULT','Synthetic lost response')
        service._mock_write=lost
        confirm={'plan_id':plan['plan_id'],'plan_hash':plan['plan_hash'],'confirmed':True}
        assert http.post('/api/seller/execute',json=confirm).json()['status']=='UNKNOWN_RESULT'
        now[0]=180;service.mock_strategies['synthetic-strategy']['enabled']=True
        assert http.post('/api/seller/reconcile',json=confirm).json()['status']=='SUCCESS'
        assert sends==['STRATEGY_STATUS']


def test_cross_service_unknown_blocks_send_and_account_changes(tmp_path,monkeypatch):
    from test_operation_routes import payload
    with client(tmp_path,monkeypatch) as http:
        service=http.app.state.seller_extensions
        plan=http.post('/api/seller/preview',json={'operation':'STRATEGY_STATUS','request':WRITE_REQUESTS['STRATEGY_STATUS']}).json()
        confirm={'plan_id':plan['plan_id'],'plan_hash':plan['plan_hash'],'confirmed':True}
        stock=http.post('/api/mutations/preview',json=payload('stocks')).json()
        stock_confirm={'plan_id':stock['id'],'plan_hash':stock['hash'],'confirmed':True}
        shop=http.app.state.mutations
        shop._history['synthetic-attempt']={'rows':[{'status':'UNKNOWN_RESULT'}]}
        response=http.post('/api/seller/execute',json=confirm)
        assert response.status_code==409 and response.json()['code']=='UNRESOLVED'
        assert not service.plans[plan['plan_id']]['attempted']
        shop._history.clear()
        service.recovered=[{'id':'00000000-0000-0000-0000-000000000001','status':'UNKNOWN_RESULT'}]
        response=http.post('/api/mutations/execute',json=stock_confirm)
        assert response.status_code==409 and response.json()['code']=='UNRESOLVED'
        assert not shop._plans[stock['id']]['attempted']
        account=http.get('/api/accounts').json()['active_id']
        for action in ('select','update','delete'):
            assert http.post('/api/accounts',json={'action':action,'id':account,'name':'Synthetic'}).status_code==409


def test_shared_busy_blocks_both_services_without_send(tmp_path,monkeypatch):
    from test_operation_routes import payload
    with client(tmp_path,monkeypatch) as http:
        strategy=http.post('/api/seller/preview',json={'operation':'STRATEGY_STATUS','request':WRITE_REQUESTS['STRATEGY_STATUS']}).json()
        stock=http.post('/api/mutations/preview',json=payload('stocks')).json()
        http.app.state.accounts.busy=True
        strategy_response=http.post('/api/seller/execute',json={'plan_id':strategy['plan_id'],'plan_hash':strategy['plan_hash'],'confirmed':True})
        stock_response=http.post('/api/mutations/execute',json={'plan_id':stock['id'],'plan_hash':stock['hash'],'confirmed':True})
        assert strategy_response.status_code==409 and strategy_response.json()['code']=='BUSY'
        assert stock_response.status_code==409 and stock_response.json()['code']=='BUSY'
        assert not http.app.state.seller_extensions.plans[strategy['plan_id']]['attempted']
        assert not http.app.state.mutations._plans[stock['id']]['attempted']
