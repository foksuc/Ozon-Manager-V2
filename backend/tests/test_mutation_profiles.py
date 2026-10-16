"""Dated promotion contracts: synthetic transport, no Seller keys or network."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from app import ozon_write
from app.ozon_write import WriteClient, WriteOperation as Op, WriteError, LEGACY_PROFILE, FUTURE_PROFILE, LEGACY_VALID_UNTIL as CUTOFF, validate_request
from app.mutations import MutationService
from test_mutations import Fixture
from test_action_currency_guards import isolated_api


@pytest.fixture(autouse=True)
def current_contract(monkeypatch):
    monkeypatch.setattr(ozon_write, '_utc_now', lambda: datetime(2026,10,9,tzinfo=timezone.utc))


@pytest.mark.parametrize('op,scope,after,path,body', [
    (Op.ACTION_UPDATE, {'action_id':'3'}, '12.01', '/v1/actions/products/activate', {'action_id':3,'products':[{'product_id':111,'action_price':Decimal('12.01')}]}),
    (Op.ACTION_REMOVE, {'action_id':'3'}, None, '/v1/actions/products/deactivate', {'action_id':3,'product_ids':[111]}),
    (Op.AUTO_UPDATE, {'action_id':'3','auto_add_date':'2026-10-13T21:00:00Z'}, '12.01', '/v1/actions/auto-add/products/update', {'action_id':3,'auto_add_date':'2026-10-13T21:00:00Z','to_update':[{'product_id':111,'action_price':Decimal('12.01'),'currency':'RUB'}]}),
])
def test_current_immutable_profile_body_execute_and_readback(tmp_path,op,scope,after,path,body):
    async def run():
        fixture=Fixture(tmp_path,live=True)
        calls=[]
        def handler(request):
            calls.append(request)
            assert request.url.path==path
            assert json.loads(request.content,parse_float=Decimal)==body
            fixture.items['a']['member']=op is not Op.ACTION_REMOVE
            if after is not None:
                fixture.items['a']['current']=after
            response={'updated_ids':['111']} if op is Op.AUTO_UPDATE else {'result':{'product_ids':[111],'rejected':[]}}
            return httpx.Response(200,json=response)
        fixture.service.writer_factory=lambda credentials:WriteClient(credentials,transport=httpx.MockTransport(handler))
        plan=await fixture.service.preview(op,scope,[{'id':'a','after':after}])
        assert plan['valid'] and plan['api_profile']==LEGACY_PROFILE and plan['api_path']==path
        document=json.loads(fixture.service._plans[plan['id']]['document'])
        assert document['api_profile']==LEGACY_PROFILE and document['api_path']==path
        if after is not None:
            key='to_update' if op is Op.AUTO_UPDATE else 'products'
            assert isinstance(document['body'][key][0]['action_price'],str)
        outcome=await fixture.service.execute(plan['id'],plan['hash'],confirmed=True)
        assert len(calls)==1 and outcome['status']=='VERIFIED_SUCCESS' and outcome['rows'][0]['accepted'] is True
        with pytest.raises(WriteError):
            await fixture.service.execute(plan['id'],plan['hash'],confirmed=True)
        assert len(calls)==1
    asyncio.run(run())


def test_decimal_wire_has_no_float_roundtrip_and_uint64_auto_selector():
    body={'action_id':2**64-1,'auto_add_date':'2026-10-13T21:00:00Z','to_update':[{'product_id':2**64-1,'action_price':'999999999999.99','currency':'RUB'}]}
    validate_request(Op.AUTO_UPDATE,body,profile=LEGACY_PROFILE)
    content=ozon_write.request_content(Op.AUTO_UPDATE,body,LEGACY_PROFILE)
    assert b'"action_price":999999999999.99' in content
    assert b'18446744073709551615' in content
    assert json.loads(content,parse_float=Decimal)['to_update'][0]['action_price']==Decimal('999999999999.99')


def test_legacy_double_response_ids_accept_only_exact_integral_values(tmp_path):
    row={'source':{'product_id':'111'}}
    assert MutationService._accepted(Op.ACTION_UPDATE,{'result':{'product_ids':[Decimal('111.0')]}},row) is True
    assert MutationService._accepted(Op.ACTION_REMOVE,{'result':{'product_ids':[Decimal('111.1')]}},row) is None
    assert MutationService._accepted(Op.ACTION_UPDATE,{'result':{'rejected':[{'product_id':Decimal('111.0'),'reason':'SYNTHETIC'}]}},row) is False
    fixture=Fixture(tmp_path,live=True)
    from app.vendor_errors import normalize_vendor_error
    assert fixture.service._response_errors(Op.ACTION_UPDATE,{'result':{'rejected':[{'product_id':Decimal('111.0'),'reason':'SYNTHETIC'}]}},row)==[normalize_vendor_error({'reason':'SYNTHETIC'})]


@pytest.mark.parametrize('op,body', [
    (Op.ACTION_UPDATE, {'action_id':3,'products':[{'product_id':2**53,'action_price':'12.00'}]}),
    (Op.ACTION_REMOVE, {'action_id':2**53,'product_ids':[111]}),
    (Op.ACTION_REMOVE, {'action_id':3,'product_ids':['111']}),
    (Op.AUTO_UPDATE, {'action_id':3,'auto_add_date':'2026-10-13T21:00:00Z','to_update':[{'product_id':111,'action_price':'12.00','currency':'USD'}]}),
    (Op.AUTO_UPDATE, {'action_id':3,'auto_add_date':'2026-10-13T21:00:00Z','to_update':[{'product_id':111,'action_price':'12.00'}]}),
    (Op.ACTION_UPDATE, {'action_id':3,'products':[{'product_id':111,'action_price':{'amount':'12.00','currency':'RUB'}}]}),
])
def test_invalid_legacy_body_zero_network(op,body):
    async def run():
        calls=[]
        client=WriteClient(None,transport=httpx.MockTransport(lambda request:calls.append(request)))
        try:
            with pytest.raises(WriteError):
                await client.send(op,body)
            assert not calls
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('op', [Op.ACTION_UPDATE,Op.ACTION_REMOVE,Op.AUTO_UPDATE,Op.AUTO_REMOVE])
@pytest.mark.parametrize('during_fresh', [False,True])
def test_every_action_plan_expires_before_send(tmp_path,monkeypatch,op,during_fresh):
    moment=[CUTOFF-timedelta(seconds=30)]
    monkeypatch.setattr(ozon_write,'_utc_now',lambda:moment[0])
    async def run():
        fixture=Fixture(tmp_path,live=True)
        scope={'action_id':'3'}
        if op in (Op.AUTO_UPDATE,Op.AUTO_REMOVE):
            scope['auto_add_date']='2026-10-13T21:00:00Z'
        plan=await fixture.service.preview(op,scope,[{'id':'a','after':None if op in (Op.ACTION_REMOVE,Op.AUTO_REMOVE) else '12'}])
        if during_fresh:
            fresh=fixture.service.fresh
            async def expire(*args):
                result=await fresh(*args)
                moment[0]=CUTOFF
                return result
            fixture.service.fresh=expire
        else:
            moment[0]=CUTOFF
        with pytest.raises(WriteError) as error:
            await fixture.service.execute(plan['id'],plan['hash'],confirmed=True)
        assert error.value.code=='CONTRACT_BLOCKED' and fixture.calls==0
        assert not fixture.service._plans[plan['id']]['attempted']
    asyncio.run(run())


@pytest.mark.parametrize('op', [Op.ACTION_UPDATE,Op.ACTION_REMOVE,Op.AUTO_UPDATE])
def test_legacy_transport_rechecks_cutoff_after_lock(op,monkeypatch):
    moment=[CUTOFF-timedelta(seconds=30)]
    monkeypatch.setattr(ozon_write,'_utc_now',lambda:moment[0])
    scope={'action_id':'3'}
    if op is Op.AUTO_UPDATE:
        scope['auto_add_date']='2026-10-13T21:00:00Z'
    body=MutationService._body(op,scope,[{'source':{'product_id':'111','currency':'RUB'},'after':None if op is Op.ACTION_REMOVE else '12.00'}],LEGACY_PROFILE)
    async def run():
        calls=[]
        client=WriteClient(None,transport=httpx.MockTransport(lambda request:calls.append(request)))
        from app.connection import Credentials
        client._credentials=Credentials('1','SYNTHETIC')
        await client._lock.acquire()
        pending=asyncio.create_task(client.send(op,body))
        await asyncio.sleep(0)
        moment[0]=CUTOFF
        client._lock.release()
        try:
            with pytest.raises(WriteError) as error:
                await pending
            assert error.value.code=='CONTRACT_BLOCKED' and not calls
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('auto', [False,True])
def test_confirmed_base_allows_legacy_calculation_with_assumed_action(tmp_path,monkeypatch,auto):
    app,payload,_=isolated_api(tmp_path,monkeypatch,auto=auto,assumed_fields=['action_price'])
    with TestClient(app) as api:
        response=api.post('/api/mutations/preview',json={'operation':'auto_add' if auto else 'action_add','request':payload})
        assert response.status_code==200,response.text
        plan=response.json()
        assert plan['valid'] and plan['api_profile']==LEGACY_PROFILE
        document=json.loads(app.state.mutations._plans[plan['id']]['document'])
        source=document['rows'][0]['source']
        assert source['currency_assumed'] and source['money_assumed_fields']==['action_price']
        if auto:
            assert document['body']['to_update'][0]['currency']=='RUB'
        else:
            assert document['body']['products'][0]['action_price']=='900.00'
            assert 'currency' not in document['body']['products'][0]


@pytest.mark.parametrize('field', ['below_min_price','extremely_low_price','failed_price'])
def test_auto_price_diagnostics_do_not_override_accepted_verified_state(tmp_path,field):
    async def run():
        fixture=Fixture(tmp_path,live=True)
        original=fixture.writer
        def factory(credentials):
            writer=original(credentials)
            send=writer.send
            async def reply(op,body):
                await send(op,body)
                return {'updated_ids':['111'],field:[{'key':111,'value':Decimal('9.00')}]}
            writer.send=reply
            return writer
        fixture.service.writer_factory=factory
        plan=await fixture.service.preview(Op.AUTO_UPDATE,{'action_id':'3','auto_add_date':'2026-10-13T21:00:00Z'},[{'id':'a','after':'12'}])
        outcome=await fixture.service.execute(plan['id'],plan['hash'],confirmed=True)
        assert outcome['status']=='VERIFIED_SUCCESS' and outcome['rows'][0]['accepted'] is True
        assert outcome['rows'][0]['errors'][0]['code']==field.upper() and fixture.calls==1
    asyncio.run(run())


def test_future_money_schema_permits_only_exact_candidate_return(tmp_path,monkeypatch):
    monkeypatch.setattr(ozon_write,'_utc_now',lambda:datetime(2026,10,13,tzinfo=timezone.utc))
    async def run():
        fixture=Fixture(tmp_path,live=True)
        fixture.items['a'].update(member=False,role='candidate',current=None,action_price='12.00',
            money_currencies={'price':'RUB','action_price':'RUB'})
        plan=await fixture.service.preview(Op.AUTO_UPDATE,{'action_id':'3','auto_add_date':'2026-10-13T21:00:00Z'},[{'id':'a','after':'12'}])
        assert plan['valid'] and plan['api_profile']==FUTURE_PROFILE
        document=json.loads(fixture.service._plans[plan['id']]['document'])
        assert document['body']['products']==[{'id':111,'action_price':{'amount':'12.00','currency':'RUB'}}]
        with pytest.raises(WriteError) as error:
            await fixture.service.preview(Op.AUTO_UPDATE,{'action_id':'3','auto_add_date':'2026-10-13T21:00:00Z'},[{'id':'a','after':'11'}])
        assert error.value.code=='AUTO_RETURN_ONLY' and fixture.calls==0
    asyncio.run(run())
