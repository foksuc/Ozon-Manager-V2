"""LIVE mode uses only synthetic catalogue/accounts. No network or secret files."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.connection import Connection, Credentials
from app.operation_routes import install_operations


def isolated_api(tmp_path, monkeypatch, *, auto=False, assumed=True, role='member', assumed_fields=None, candidate_action_price=None, overrides=None):
    date='2026-10-13T21:00:00Z' if auto else None
    row=dict(id='synthetic-row',action_id='10',offer_id='SYNTHETIC',name='Synthetic',
        product_id='101',sku='201',role=role,price='1000',action_price='950' if role=='member' else candidate_action_price,
        min_price=None,max_price=None,elastic=False,auto_add_at=date,currency='RUB' if assumed else None,
        money_currencies={'price':'RUB' if assumed else None,'action_price':'RUB' if assumed else None},currency_assumed=assumed)
    if assumed_fields is not None:
        row['money_assumed_fields']=assumed_fields
    row.update(overrides or {})
    calls=[]
    class Catalogue:
        async def catalog(self, **scope):
            calls.append(scope)
            return dict(mode='LIVE_READ',fetched_at='2026-10-09T00:00:00Z',complete=True,
                warnings=[],actions=[],products=[deepcopy(row)] if scope.get('role')==role else [],stocks=[])
    def forbidden_writer(*args, **kwargs):
        raise AssertionError('No writer or network may be created')
    monkeypatch.setattr('app.operation_routes.WriteClient',forbidden_writer)
    accounts=SimpleNamespace(mode='LIVE_READ',active_id='synthetic-account',revision=1,
        path=tmp_path/'synthetic-accounts',root=tmp_path,credentials=lambda:Credentials('1','SYNTHETIC-NOT-A-KEY'))
    from test_accounts import cipher
    accounts.cipher=cipher
    app=FastAPI()
    app.state.live_catalog=Catalogue()
    app.state.catalog_factory=lambda scenario: {}
    async def activate(*args):
        raise AssertionError('Account activation is outside this test')
    install_operations(app,Connection('LIVE_READ'),activate,account_store=accounts)
    payload=dict(kind='prices',ids=['synthetic-row'],action_id='10',role=role,auto_add=auto,
        auto_add_date=date,mode='manual',value='900',rounding='1',scenario='ready')
    return app,payload,calls


@pytest.mark.parametrize('auto',[False,True])
@pytest.mark.parametrize('assumed',[False,True])
def test_current_id_only_removal_requires_verified_voucher_or_auto(tmp_path,monkeypatch,auto,assumed):
    monkeypatch.setattr('app.ozon_write._utc_now',lambda:datetime(2026,10,13,tzinfo=timezone.utc))
    app,payload,reads=isolated_api(tmp_path,monkeypatch,auto=auto,assumed=assumed,overrides={'is_voucher_action':True})
    operation='auto_remove' if auto else 'action_remove'
    with TestClient(app) as api:
        response=api.post('/api/mutations/preview',json={'operation':operation,'request':payload})
        assert response.status_code==200,response.text
        plan=response.json()
        assert plan['valid'] and plan['blocked_reason'] is None
        assert plan['rows'][0]['after'] is None
        assert plan['rows'][0]['sku']=='201'
        document=json.loads(app.state.mutations._plans[plan['id']]['document'])
        assert document['body']['product_ids']==['101']
        assert 'currency' not in document['body'] and 'action_price' not in document['body']
        assert len(reads)==3


@pytest.mark.parametrize('operation,auto,role',[
    ('prices',False,'member'),('action_add',False,'member'),('action_add',False,'candidate'),
    ('auto_add',True,'member'),('auto_add',True,'candidate')])
def test_monetary_changes_still_reject_assumed_currency(tmp_path,monkeypatch,operation,auto,role):
    app,payload,_=isolated_api(tmp_path,monkeypatch,auto=auto,assumed=True,role=role)
    with TestClient(app) as api:
        response=api.post('/api/mutations/preview',json={'operation':operation,'request':payload})
        assert response.status_code==409
        assert 'CURRENCY_ASSUMED' in response.text
        assert not app.state.mutations._plans


def test_confirmed_catalog_currency_is_independent_from_assumed_action_currency(tmp_path,monkeypatch):
    monkeypatch.setattr('app.ozon_write._utc_now',lambda:datetime(2026,10,9,tzinfo=timezone.utc))
    app,payload,_=isolated_api(tmp_path,monkeypatch,assumed_fields=['action_price'])
    with TestClient(app) as api:
        response=api.post('/api/mutations/preview',json={'operation':'prices','request':payload})
        assert response.status_code==200,response.text
        plan=response.json()
        assert plan['valid'] and plan['rows'][0]['after']=='900.00'
        document=json.loads(app.state.mutations._plans[plan['id']]['document'])
        assert document['body']['prices'][0]['currency_code']=='RUB'
        response=api.post('/api/mutations/preview',json={'operation':'action_add','request':payload})
        assert response.status_code==200 and response.json()['valid'],response.text
        document=json.loads(app.state.mutations._plans[response.json()['id']]['document'])
        assert document['body']['products']==[{'product_id':101,'action_price':'900.00'}]


@pytest.mark.parametrize('auto',[False,True])
@pytest.mark.parametrize('candidate_price',['0.00','950.00',None])
def test_candidate_price_is_not_existing_participation(tmp_path,monkeypatch,auto,candidate_price):
    monkeypatch.setattr('app.ozon_write._utc_now',lambda:datetime(2026,10,9,tzinfo=timezone.utc))
    app,payload,reads=isolated_api(tmp_path,monkeypatch,auto=auto,role='candidate',
        assumed_fields=['action_price'],candidate_action_price=candidate_price)
    with TestClient(app) as api:
        response=api.post('/api/mutations/preview',json={'operation':'auto_add' if auto else 'action_add','request':payload})
        assert response.status_code==200,response.text
        plan=response.json()
        assert plan['valid'] and plan['rows'][0]['before'] is None and plan['rows'][0]['after']=='900.00'
        document=json.loads(app.state.mutations._plans[plan['id']]['document'])
        original=document['rows'][0]['source']
        assert original['member'] is False and original['current'] is None
        assert original['action_price']==candidate_price and original['price']=='1000'
        assert original['money_assumed_fields']==['action_price']
        assert len(reads)==3
