"""Exact Ozon return amount; synthetic API and in-memory transport only."""
from copy import deepcopy
from datetime import datetime,timezone

import pytest
from fastapi.testclient import TestClient
from app.accounts import AccountStore
from app.connection import Connection
from app.main import create_app
from app.storage import Store
from test_accounts import cipher


@pytest.fixture
def return_api(tmp_path,monkeypatch):
    monkeypatch.setattr('app.ozon_write._utc_now',lambda:datetime(2026,10,13,tzinfo=timezone.utc))
    accounts=AccountStore('LIVE_READ',root=tmp_path,path=tmp_path/'accounts.bin',cipher=cipher)
    accounts.add('Synthetic','1','synthetic-only')
    monkeypatch.setattr('app.main.AccountStore',lambda *args,**kwargs:accounts)
    row=dict(id='candidate',action_id='10',offer_id='SYN',name='Synthetic',product_id='101',sku='201',
             role='candidate',price='1999.00',action_price='1899.05',min_price=None,max_price=None,
             elastic=False,auto_add_at='2026-10-20T00:00:00Z',currency='RUB',currency_assumed=False,
             money_currencies={'price':'RUB','action_price':'RUB'},is_voucher_action=False,
             freeze_date=None,card_price='1999.00',card_currency='RUB')
    state={'sends':[]}
    class Reader:
        async def read(self,*args,**kwargs):raise AssertionError('No network')
        async def close(self):pass
    class Catalogue:
        async def catalog(self,**scope):
            return {'mode':'LIVE_READ','fetched_at':'2026-10-13T00:00:00Z','complete':True,
                    'warnings':[],'actions':[],'stocks':[],
                    'products':[deepcopy(row)] if scope.get('role')==row['role'] else []}
    class Writer:
        def __init__(self,credentials):pass
        async def close(self):pass
        async def send(self,operation,body):
            state['sends'].append(deepcopy(body))
            row.update(role='member',action_price=body['products'][0]['action_price']['amount'])
            return {'product_ids':['101']}
    monkeypatch.setattr('app.operation_routes.WriteClient',Writer)
    app=create_app(Store(tmp_path/'mock.sqlite3',root=tmp_path),connection=Connection('LIVE_READ'),read_client=Reader())
    app.state.live_catalog=Catalogue()
    with TestClient(app,base_url='http://localhost') as api:
        yield api,row,state


def request():
    return {'operation':'auto_add','request':{'kind':'prices','ids':['candidate'],'action_id':'10',
        'role':'candidate','auto_add':True,'auto_add_date':'2026-10-20T00:00:00Z',
        'mode':'percent','value':'99','rounding':'100','floor':'1','scenario':'ready'}}


def test_return_uses_exact_ozon_amount_not_client_percent_or_rounding(return_api):
    api,row,state=return_api
    response=api.post('/api/mutations/preview',json=request())
    assert response.status_code==200,response.text
    plan=response.json()
    assert plan['valid'] and plan['rows'][0]['after']=='1899.05' and state['sends']==[]
    response=api.post('/api/mutations/execute',json={'plan_id':plan['id'],'plan_hash':plan['hash'],'confirmed':True})
    assert response.status_code==200,response.text
    assert response.json()['status']=='VERIFIED_SUCCESS'
    assert state['sends']==[{'action_id':10,'auto_add_date':'2026-10-20T00:00:00Z',
        'products':[{'id':101,'action_price':{'amount':'1899.05','currency':'RUB'}}]}]
    assert row['card_price']=='1999.00'  # Future scheduled effect is not immediate.
    assert response.json()['effects'][0]['scheduled_card_price']=={
        'after':'1899.05','currency':'RUB','at':'2026-10-20T00:00:00Z','verification':'schedule_only'}


@pytest.mark.parametrize('change',[{'action_price':None},{'money_currencies':{'price':'RUB','action_price':'USD'}}])
def test_return_missing_amount_or_explicit_other_currency_never_sends(return_api,change):
    api,row,state=return_api
    row.update(change)
    response=api.post('/api/mutations/preview',json=request())
    assert response.status_code in (409,422) and not state['sends']


def test_return_changed_ozon_price_invalidates_saved_confirmation(return_api):
    api,row,state=return_api
    plan=api.post('/api/mutations/preview',json=request()).json()
    row['action_price']='1800.00'
    response=api.post('/api/mutations/execute',json={'plan_id':plan['id'],'plan_hash':plan['hash'],'confirmed':True})
    assert response.status_code==409 and response.json()['code']=='FRESH_CHANGED' and not state['sends']


def test_ordinary_add_uses_minimum_vendor_discount_not_manual_percent(return_api):
    api,row,state=return_api
    row.update(auto_add_at=None,price='3700.00',action_price=None,card_price='3700.00',
        elastic=True,min_price='3000.00',max_price='3546.00',price_max_elastic='3000.00',price_min_elastic='3546.00')
    query=request()
    query['operation']='action_add'
    query['request'].update(auto_add=False,auto_add_date=None,value='5',rounding='100')
    prepared=api.post('/api/mutations/preview',json=query)
    assert prepared.status_code==200,prepared.text
    plan=prepared.json()
    assert plan['rows'][0]['after']=='3546.00' and state['sends']==[]
    result=api.post('/api/mutations/execute',json={'plan_id':plan['id'],'plan_hash':plan['hash'],'confirmed':True})
    assert result.status_code==200 and result.json()['status']=='VERIFIED_SUCCESS',result.text
    assert state['sends'][0]['products'][0]=={'product_id':101,'action_price':{'amount':'3546.00','currency':'RUB'}}


def test_ordinary_add_preserves_fractional_limit_exactly(return_api):
    api,row,state=return_api
    row.update(auto_add_at=None,action_price=None,elastic=True,min_price='1000.00',max_price='1899.05',
        price_max_elastic='1000.00',price_min_elastic='1899.05')
    query=request()
    query['operation']='action_add'
    query['request'].update(auto_add=False,auto_add_date=None,value='99',rounding='100')
    result=api.post('/api/mutations/preview',json=query)
    assert result.status_code==200,result.text
    assert result.json()['rows'][0]['after']=='1899.05' and not state['sends']
