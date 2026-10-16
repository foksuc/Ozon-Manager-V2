"""Approved missing-unit binding through real routes; synthetic reads only."""
import json
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from test_action_currency_guards import isolated_api
from test_live_catalog import product_fake, rub, run, BIG
from app.live_catalog import LiveCatalog
from app.ozon_read import ReadOperation as ReadOp


@pytest.fixture(autouse=True)
def current(monkeypatch):
    monkeypatch.setattr('app.ozon_write._utc_now',lambda:datetime(2026,10,13,tzinfo=timezone.utc))


def metadata():
    return dict(is_voucher_action=False,elastic=True,card_price='1000.00',card_currency='RUB',
        currency='RUB',currency_assumed=False,min_price='800',max_price='950',
        price_min_elastic='950',price_max_elastic='800',max_action_price='950',
        money_currencies={'price':'RUB','action_price':None,'min_price':None,'max_price':None,
            'price_min_elastic':None,'price_max_elastic':None,'max_action_price':None})


def test_plan_binds_only_missing_units_and_keeps_original_snapshot(tmp_path,monkeypatch):
    app,payload,_=isolated_api(tmp_path,monkeypatch,assumed=False,overrides=metadata())
    with TestClient(app) as api:
        response=api.post('/api/mutations/preview',json={'operation':'action_add','request':payload})
        assert response.status_code==200,response.text
        plan=response.json()
        assert plan['valid']
        effect=plan['effects'][0]
        assert effect['card_price']=={'before':'1000.00','after':'950.00','currency':'RUB','application':'possible','accepted_values':['1000.00','950.00']}
        assert set(effect['currency_binding_fields'])=={'action_price','min_price','max_price','max_action_price','price_min_elastic','price_max_elastic'}
        doc=json.loads(app.state.mutations._plans[plan['id']]['document'])
        source=doc['rows'][0]['source']
        assert source['original_money_currencies']['action_price'] is None
        assert source['money_currencies']['action_price']=='RUB'
        assert doc['body']['products'][0]['action_price']=={'amount':'950.00','currency':'RUB'}


@pytest.mark.parametrize('change', ['foreign_bound','unknown_base','assumed_base','unknown_type','inverted_bounds'])
def test_unapproved_or_unsafe_binding_never_creates_plan(tmp_path,monkeypatch,change):
    values=metadata()
    if change=='foreign_bound': values['money_currencies']['max_price']='USD'
    if change=='unknown_base': values['card_currency']=None
    if change=='assumed_base': values['money_assumed_fields']=['price']
    if change=='unknown_type': values['is_voucher_action']=None
    if change=='inverted_bounds': values.update(min_price='960',price_max_elastic='960')
    app,payload,_=isolated_api(tmp_path,monkeypatch,assumed=False,overrides=values)
    with TestClient(app) as api:
        response=api.post('/api/mutations/preview',json={'operation':'action_add','request':payload})
        assert response.status_code in (409,422),response.text
        assert not app.state.mutations._plans


@pytest.mark.parametrize('voucher,freeze,complete',[(False,None,True),(True,'2026-11-01T00:00:00Z',True),('false',None,False),(False,'invalid',False)])
def test_fresh_mapper_keeps_authoritative_type_card_and_freeze(voucher,freeze,complete):
    fake=product_fake(ReadOp.ACTION_PRODUCTS,{'id':BIG,'price':rub('1100'),'action_price':{'amount':'900','currency':''}})
    fake.responses[ReadOp.ACTIONS]['result'][0].update(is_voucher_action=voucher,freeze_date=freeze)
    result=run(LiveCatalog(fake),scope='products',action_id='123',role='member',auto_add=False)
    row=result['products'][0]
    assert result['complete'] is complete
    assert row['is_voucher_action']==(voucher if type(voucher) is bool else None)
    assert bool(row['freeze_date']) is (freeze=='2026-11-01T00:00:00Z')
    assert row['card_price']=='1000.00' and row['card_currency']=='RUB'
    assert row['action_price']=='900.00' and row['money_currencies']['action_price'] is None
