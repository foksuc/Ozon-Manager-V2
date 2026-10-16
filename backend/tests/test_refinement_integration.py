"""Cross-service safety gates exercised through the integrated MOCK application."""
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from test_warehouse_reports_routes import client
from test_warehouse_reports import WRITES

def test_empty_limits_schema_accepts_only_an_empty_object(tmp_path,monkeypatch):
    with client(tmp_path,monkeypatch)as http:
        schema=http.get('/api/warehouse/contracts').json()['operations']['PRODUCT_LIMITS']['request']
        assert schema=={'type':'object','properties':{}}
        assert http.post('/api/warehouse/read',json={'operation':'PRODUCT_LIMITS','request':{}}).status_code==200
        assert http.post('/api/warehouse/read',json={'operation':'PRODUCT_LIMITS','request':{'unexpected':1}}).status_code==422

@pytest.mark.parametrize('path',['/api/mutations/execute','/api/seller/execute'])
def test_unresolved_warehouse_prevents_other_execution(tmp_path,monkeypatch,path):
    with client(tmp_path,monkeypatch)as http:
        monkeypatch.setattr(http.app.state.warehouse_reports,'has_unresolved',lambda:True)
        response=http.post(path,json={'plan_id':'synthetic','plan_hash':'synthetic','confirmed':True})
        assert response.status_code==409 and response.json()['code']=='UNRESOLVED'

@pytest.mark.parametrize('action',['select','update','delete'])
def test_unresolved_warehouse_prevents_account_rebinding(tmp_path,monkeypatch,action):
    with client(tmp_path,monkeypatch)as http:
        monkeypatch.setattr(http.app.state.warehouse_reports,'has_unresolved',lambda:True)
        response=http.post('/api/accounts',json={'action':action,'id':'synthetic'})
        assert response.status_code==409 and response.json()['code']=='ACCOUNT_ERROR'

def test_unresolved_warehouse_prevents_financial_generation(tmp_path,monkeypatch):
    with client(tmp_path,monkeypatch)as http:
        monkeypatch.setattr(http.app.state.warehouse_reports,'has_unresolved',lambda:True)
        assert http.post('/api/finance/reports',json={'kind':'mutual','month':'2026-10'}).status_code==409

@pytest.mark.parametrize('source',['mutations','seller_extensions'])
def test_other_unresolved_operation_prevents_prepared_warehouse_send(tmp_path,monkeypatch,source):
    with client(tmp_path,monkeypatch)as http:
        response=http.post('/api/warehouse/preview',json={'operation':'PRICE_UPDATE','request':WRITES['PRICE_UPDATE']})
        assert response.status_code==200
        plan=response.json()
        monkeypatch.setattr(getattr(http.app.state,source),'has_unresolved',lambda:True)
        sent=http.post('/api/warehouse/execute',json={'plan_id':plan['plan_id'],'plan_hash':plan['plan_hash'],'confirmed':True})
        assert sent.status_code==409
        assert not http.get('/api/warehouse/history').json()['items']

def test_integrated_shutdown_refuses_busy_account(tmp_path,monkeypatch):
    prepared=client(tmp_path,monkeypatch)
    app=prepared.app
    prepared.close()
    with TestClient(app,client=('127.0.0.1',50000),base_url='http://localhost')as http:
        callback=Mock()
        http.app.state.lifecycle_exit=callback
        http.app.state.accounts.busy=True
        response=http.post('/api/lifecycle/stop',headers={'origin':'http://localhost:8000','x-lifecycle-token':http.app.state.lifecycle_token})
        assert response.status_code==409
        callback.assert_not_called()
        assert not http.app.state.lifecycle_closing
