"""Working V1 stock contract, synthetic LIVE transport only; no Seller secrets."""
import asyncio
import json
import httpx
import pytest
from app.ozon_write import WriteOperation as Op, WriteClient, WriteError, blocked_reason, validate_request
from test_mutations import Fixture


def stock_fixture(tmp_path, *, offer=True):
    fixture = Fixture(tmp_path, live=True)
    fixture.items = {'a': {'id':'a','product_id':'111','warehouse_id':'9',
        'current':10,'reserved':2,'present':12, 'offer_id':'001-SYN' if offer else ''}}
    return fixture


@pytest.mark.parametrize('offer',[False,True])
@pytest.mark.parametrize('after',[0,7])
def test_legacy_wire_single_send_readback_and_inverse(tmp_path,offer,after):
    async def run():
        fixture=stock_fixture(tmp_path,offer=offer)
        requests=[]
        def handler(request):
            requests.append(request)
            assert request.url.host=='api-seller.ozon.ru' and request.url.path=='/v2/products/stocks'
            body=json.loads(request.content)
            requested=after if len(requests)==1 else 10
            selector={'offer_id':'001-SYN'} if offer else {'product_id':111}
            assert body=={'stocks':[{**selector,'warehouse_id':9,'stock':requested}]}
            assert 'quant_size' not in request.content.decode()
            fixture.items['a'].update(current=requested,present=requested+2)
            return httpx.Response(200,json={'result':[{'product_id':111,'warehouse_id':9,'updated':True,'errors':[]}]})
        fixture.service.writer_factory=lambda credentials:WriteClient(credentials,transport=httpx.MockTransport(handler))
        plan=await fixture.service.preview(Op.STOCK,{},[{'id':'a','after':after}])
        assert plan['valid'] and plan['api_profile']=='STABLE' and blocked_reason(Op.STOCK) is None
        result=await fixture.service.execute(plan['id'],plan['hash'],confirmed=True)
        assert result['status']=='VERIFIED_SUCCESS' and result['rows'][0]['actual']==after
        assert fixture.items['a']['reserved']==2 and len(requests)==1
        with pytest.raises(WriteError) as error:
            await fixture.service.execute(plan['id'],plan['hash'],confirmed=True)
        assert error.value.code=='ALREADY_ATTEMPTED' and len(requests)==1
        fixture.now=31
        inverse=await fixture.service.rollback_preview(plan['id'])
        result=await fixture.service.execute(inverse['id'],inverse['hash'],confirmed=True)
        assert result['status']=='VERIFIED_SUCCESS' and fixture.items['a']['current']==10 and len(requests)==2
    asyncio.run(run())


@pytest.mark.parametrize('field',['current','reserved','present','offer_id'])
def test_changed_stock_snapshot_never_sends(tmp_path,field):
    async def run():
        fixture=stock_fixture(tmp_path)
        plan=await fixture.service.preview(Op.STOCK,{},[{'id':'a','after':7}])
        fixture.items['a'][field]='CHANGED-SYN' if field=='offer_id' else fixture.items['a'][field]+1
        with pytest.raises(WriteError) as error:
            await fixture.service.execute(plan['id'],plan['hash'],confirmed=True)
        assert error.value.code=='FRESH_CHANGED' and fixture.calls==0
    asyncio.run(run())


def test_free_quantity_is_authoritative_not_present_minus_reserve(tmp_path):
    async def run():
        fixture=stock_fixture(tmp_path)
        fixture.items['a'].update(present=50,reserved=3,current=10)
        plan=await fixture.service.preview(Op.STOCK,{},[{'id':'a','after':7}])
        assert plan['valid'] and plan['rows'][0]['before']==10
        document=json.loads(fixture.service._plans[plan['id']]['document'])
        assert document['body']['stocks'][0]['stock']==7
        assert document['rows'][0]['source']['reserved']==3
    asyncio.run(run())


@pytest.mark.parametrize('apply',[False,True])
def test_stock_timeout_never_retries_and_requires_readback(tmp_path,apply):
    async def run():
        fixture=stock_fixture(tmp_path)
        fixture.apply,fixture.send_timeout=apply,True
        plan=await fixture.service.preview(Op.STOCK,{},[{'id':'a','after':7}])
        result=await fixture.service.execute(plan['id'],plan['hash'],confirmed=True)
        assert result['status']==('VERIFIED_SUCCESS' if apply else 'UNKNOWN_RESULT') and fixture.calls==1
        with pytest.raises(WriteError):
            await fixture.service.execute(plan['id'],plan['hash'],confirmed=True)
        assert fixture.calls==1
    asyncio.run(run())


def test_stock_account_request_limit_and_expiry_before_send(tmp_path):
    async def run():
        fixture=stock_fixture(tmp_path)
        plan=await fixture.service.preview(Op.STOCK,{},[{'id':'a','after':7}])
        fixture.service._rate[(fixture.accounts.active_id,'STOCK_REQUEST')]=[0]*80
        with pytest.raises(WriteError) as error:
            await fixture.service.execute(plan['id'],plan['hash'],confirmed=True)
        assert error.value.code=='LOCAL_RATE_LIMIT' and fixture.calls==0
        fixture.now=60
        result=await fixture.service.execute(plan['id'],plan['hash'],confirmed=True)
        assert result['status']=='VERIFIED_SUCCESS' and fixture.calls==1
    asyncio.run(run())


@pytest.mark.parametrize('row',[
    {'product_id':111,'offer_id':'SYN','warehouse_id':9,'stock':7},
    {'offer_id':'','warehouse_id':9,'stock':7},
    {'offer_id':True,'warehouse_id':9,'stock':7},
    {'offer_id':'SYN','warehouse_id':True,'stock':7},
    {'offer_id':'SYN','warehouse_id':9,'stock':True},
    {'offer_id':'SYN','warehouse_id':9,'stock':-1},
    {'product_id':111,'warehouse_id':9,'stock':7,'quant_size':1},
])
def test_invalid_legacy_stock_wire_is_rejected(row):
    with pytest.raises(WriteError):validate_request(Op.STOCK,{'stocks':[row]})


def test_same_offer_different_warehouse_allowed_duplicate_pair_rejected():
    row={'offer_id':'001-SYN','warehouse_id':9,'stock':7}
    validate_request(Op.STOCK,{'stocks':[row,{**row,'warehouse_id':10}]})
    with pytest.raises(WriteError):validate_request(Op.STOCK,{'stocks':[row,row]})
    validate_request(Op.STOCK,{'stocks':[{'product_id':i+1,'warehouse_id':9,'stock':0} for i in range(100)]})
    with pytest.raises(WriteError):validate_request(Op.STOCK,{'stocks':[{'product_id':i+1,'warehouse_id':9,'stock':0} for i in range(101)]})
