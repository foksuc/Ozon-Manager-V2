"""Synthetic post-transition promotion reads; no credentials or real sends."""
from datetime import datetime, timezone
import pytest
from app.domain import discount_fields
from app.live_catalog import LiveCatalog, State, money_value
from app.ozon_read import ReadOperation as Op
from app.mutations import MutationService
from app.ozon_write import WriteError, WriteOperation
from test_live_catalog import BIG, product_fake, run


@pytest.fixture(autouse=True)
def post_transition(monkeypatch):
    class Current(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime(2026,10,13,12,tzinfo=timezone.utc)
    monkeypatch.setattr('app.live_catalog.datetime',Current)


@pytest.mark.parametrize('currency',[None,''])
def test_promotion_base_restored_and_unknown_bounds_shown(currency):
    unlabelled=lambda amount:{'amount':amount,'currency':currency}
    raw={'id':BIG,'price':unlabelled('1100'),'action_price':unlabelled('900'),
         'price_min_elastic':unlabelled('950'),'price_max_elastic':unlabelled('800')}
    result=run(LiveCatalog(product_fake(Op.ACTION_PRODUCTS,raw)),scope='products',action_id='123')
    row=result['products'][0]
    assert row['price']=='1000.00' and row['currency']=='RUB'
    assert row['money_currencies']['price']=='RUB'
    assert row['action_price']=='900.00' and row['money_currencies']['action_price'] is None
    assert row['min_price']=='800.00' and row['max_price']=='950.00'
    assert row['minimum_discount']=='5.00' and row['maximum_discount']=='20.00' and row['actual_discount']=='10.00'
    assert not row['currency_assumed'] and row['money_assumed_fields']==[]
    assert result['complete'] and len(result['warnings'])==1
    assert 'одной валюты' in result['warnings'][0]
    assert not any('базовая цена' in w for w in result['warnings'])


def test_unknown_base_keeps_amount_without_claiming_rub():
    raw={'id':BIG,'price':{'amount':'1100','currency':''},'action_price':{'amount':'990','currency':''}}
    fake=product_fake(Op.ACTION_PRODUCTS,raw)
    fake.responses[Op.PRICES]['items']=[]
    fake.responses[Op.PRODUCT_INFO]['items'][0].pop('price')
    row=run(LiveCatalog(fake),scope='products',action_id='123')['products'][0]
    assert row['price']=='1100.00' and row['currency'] is None
    assert row['actual_discount']=='10.00' and not row['currency_assumed']
    with pytest.raises(WriteError) as exc:
        MutationService._validate(WriteOperation.PRICE,dict(current=row['price'],currency=row['currency']), '1000.00')
    assert exc.value.code=='CURRENCY_UNKNOWN'


@pytest.mark.parametrize('value',['NaN','Infinity','-1','1.001','9999999999999',True])
def test_display_opt_in_does_not_relax_amount_validation(value):
    state=State()
    assert money_value({'amount':value,'currency':''},state,allow_unknown_currency=True)[0] is None
    assert not state.complete


def test_display_opt_in_is_not_global_currency_permission():
    assert money_value({'amount':'900','currency':''},State())==(None,'')
    assert money_value({'amount':'900','currency':''},State(),allow_unknown_currency=True)==('900.00',None)
    entry={'price':'1000','action_price':'900','role':'member','money_currencies':{'price':'RUB','action_price':None}}
    assert discount_fields(entry)['actual_discount'] is None
    assert discount_fields(entry,allow_unknown_currency=True)['actual_discount']=='10.00'
    entry['money_currencies']['action_price']='USD'
    assert discount_fields(entry,allow_unknown_currency=True)['actual_discount'] is None


def test_foreign_primary_and_zero_price_do_not_gain_discounts():
    raw={'id':BIG,'price':{'amount':'1100','currency':'USD'},'action_price':{'amount':'900','currency':''}}
    row=run(LiveCatalog(product_fake(Op.ACTION_PRODUCTS,raw)),scope='products',action_id='123')['products'][0]
    assert row['price'] is None and row['currency']=='USD' and row['actual_discount'] is None
    assert discount_fields({'price':'0','action_price':'0','role':'member'},allow_unknown_currency=True)['actual_discount'] is None
