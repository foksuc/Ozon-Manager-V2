import asyncio
from copy import deepcopy
from decimal import Decimal
import json
from types import SimpleNamespace
import httpx
import pytest
from app.connection import Credentials
from app.ozon_read import ReadOperation, ReadError, validate_request
from app.ozon_write import WriteError
from app.seller_extensions import SellerExtensions, StrategyOperation, StrategyWriteClient, validate_extension_request, normalize_response


READ_REQUESTS = {
    'STRATEGY_COMPETITORS': {'page':1,'limit':50},
    'STRATEGY_LIST': {'page':1,'limit':50},
    'STRATEGY_INFO': {'strategy_id':'synthetic-strategy'},
    'STRATEGY_PRODUCTS': {'strategy_id':'synthetic-strategy'},
    'STRATEGY_PRODUCT_INFO': {'product_id':101},
    'STRATEGY_IDS': {'product_id':['101']},
    'ANALYTICS_WAREHOUSES': {'limit':100,'offset':0,'warehouse_type':'ALL'},
    'ANALYTICS_TURNOVER': {'limit':1000,'offset':0},
    'ANALYTICS_STOCKS': {'skus':['30001']},
    'RATING_SUMMARY': {},
}
COMPETITORS=[{'competitor_id':11,'coefficient':'0.95'}]
WRITE_REQUESTS = {
    'STRATEGY_CREATE': {'strategy_name':'Синтетическая новая','competitors':COMPETITORS},
    'STRATEGY_UPDATE': {'strategy_id':'synthetic-strategy','strategy_name':'Обновлённая','competitors':COMPETITORS},
    'STRATEGY_ADD_PRODUCTS': {'strategy_id':'synthetic-strategy','product_id':['102','103']},
    'STRATEGY_REMOVE_PRODUCTS': {'product_id':['101']},
    'STRATEGY_STATUS': {'strategy_id':'synthetic-strategy','enabled':True},
    'STRATEGY_DELETE': {'strategy_id':'synthetic-strategy'},
}


def fixture(tmp_path,mode='MOCK'):
    accounts=SimpleNamespace(mode=mode,active_id='synthetic-account',revision=1,busy=False,credentials=lambda:Credentials('1','synthetic-key'))
    async def no_live(*args): raise AssertionError('MOCK attempted network')
    service=SellerExtensions(accounts,no_live,root=tmp_path,path=tmp_path/'markers',verified_writes=StrategyOperation.__members__)
    return service


@pytest.mark.parametrize('name',READ_REQUESTS)
def test_all_ten_reads_strict_mock(tmp_path,name):
    service=fixture(tmp_path)
    validate_request(ReadOperation[name],READ_REQUESTS[name])
    result=asyncio.run(service.read(name,READ_REQUESTS[name]))
    assert result['operation']==name and isinstance(result['rows'],list)
    assert not (tmp_path/'markers').exists()
    with pytest.raises(ReadError): validate_request(ReadOperation[name],READ_REQUESTS[name]|{'url':'https://example.invalid'})


@pytest.mark.parametrize('name',WRITE_REQUESTS)
def test_all_six_writes_verify_and_refuse_repeat(tmp_path,name):
    async def run():
        service=fixture(tmp_path)
        plan=await service.preview(name,WRITE_REQUESTS[name])
        result=await service.execute(plan['plan_id'],plan['plan_hash'],True)
        assert result['status']=='SUCCESS'
        assert result['success_count']>0 and not result['error_count']
        with pytest.raises(WriteError,match='повтор'): await service.execute(plan['plan_id'],plan['plan_hash'],True)
    asyncio.run(run())


@pytest.mark.parametrize('verification_fails',[False,True])
def test_reconcile_reserves_account_until_verification_finishes(tmp_path,verification_fails):
    async def run():
        service=fixture(tmp_path)
        plan=await service.preview('STRATEGY_STATUS',WRITE_REQUESTS['STRATEGY_STATUS'])
        await service.execute(plan['plan_id'],plan['plan_hash'],True)
        other=fixture(tmp_path/'other')
        other.accounts=service.accounts
        other_plan=await other.preview('STRATEGY_STATUS',WRITE_REQUESTS['STRATEGY_STATUS'])
        entered,released=asyncio.Event(),asyncio.Event()
        verify=service._verify
        async def delayed_verify(stored):
            entered.set()
            await released.wait()
            if verification_fails:raise RuntimeError('synthetic verification failure')
            return await verify(stored)
        service._verify=delayed_verify
        service.accounts.busy=True
        with pytest.raises(WriteError) as error:
            await service.reconcile(plan['plan_id'],plan['plan_hash'])
        assert error.value.code=='BUSY' and not entered.is_set()
        service.accounts.busy=False
        task=asyncio.create_task(service.reconcile(plan['plan_id'],plan['plan_hash']))
        await entered.wait()
        try:
            assert service.accounts.busy
            with pytest.raises(WriteError) as error:
                await other.execute(other_plan['plan_id'],other_plan['plan_hash'],True)
            assert error.value.code=='BUSY'
            assert not other.plans[other_plan['plan_id']]['attempted']
        finally:
            released.set()
        if verification_fails:
            with pytest.raises(RuntimeError,match='synthetic verification failure'):await task
        else:
            assert (await task)['status']=='SUCCESS'
        assert not service.accounts.busy
    asyncio.run(run())


@pytest.mark.parametrize('name,body',[
    ('STRATEGY_COMPETITORS',{'page':0,'limit':50}),
    ('STRATEGY_LIST',{'page':1,'limit':51}),
    ('STRATEGY_STATUS',{'strategy_id':'a'}),
    ('STRATEGY_STATUS',{'strategy_id':'a','enabled':1}),
    ('STRATEGY_CREATE',{'strategy_name':'a','competitors':[{'competitor_id':1,'coefficient':'0.49'}]}),
    ('STRATEGY_CREATE',{'strategy_name':'a','competitors':[{'competitor_id':1,'coefficient':'1.21'}]}),
    ('STRATEGY_CREATE',{'strategy_name':'a','competitors':[{'competitor_id':1,'coefficient':'NaN'}]}),
    ('STRATEGY_CREATE',{'strategy_name':'','competitors':COMPETITORS}),
    ('STRATEGY_ADD_PRODUCTS',{'strategy_id':'a','product_id':[1]}),
    ('STRATEGY_REMOVE_PRODUCTS',{'product_id':['1']*51}),
    ('STRATEGY_REMOVE_PRODUCTS',{'product_id':['9223372036854775808']}),
    ('ANALYTICS_WAREHOUSES',{'limit':100,'warehouse_type':'FBO'}),
    ('ANALYTICS_TURNOVER',{}),
    ('ANALYTICS_TURNOVER',{'limit':1001}),
    ('ANALYTICS_STOCKS',{'skus':['1'],'cluster_ids':['1'],'macrolocal_cluster_ids':['2']}),
    ('ANALYTICS_STOCKS',{'skus':[str(i+1) for i in range(101)]}),
    ('ANALYTICS_STOCKS',{'skus':['1'],'item_tags':['INVALID']}),
    ('RATING_SUMMARY',{'store_id':'private'}),
])
def test_invalid_requests_zero_send(name,body):
    with pytest.raises(ReadError): validate_extension_request(name,body)


def test_precision_and_response_whitelist():
    value=normalize_response('ANALYTICS_TURNOVER',{'items':[{'sku':9223372036854775807,'ads':Decimal('2.123456789123456789'),'name':'Синтетический','unknown':'discard'}],'key':'discard'})
    assert value=={'items':[{'sku':'9223372036854775807','ads':'2.123456789123456789','name':'Синтетический'}]}


def test_plan_binding_hash_ttl_fresh_confirm(tmp_path):
    async def run():
        service=fixture(tmp_path);now=[0];service.clock=lambda:now[0]
        plan=await service.preview('STRATEGY_STATUS',WRITE_REQUESTS['STRATEGY_STATUS'])
        for confirmed,digest in [(False,plan['plan_hash']),(True,'0'*64)]:
            with pytest.raises(WriteError): await service.execute(plan['plan_id'],digest,confirmed)
        service.accounts.revision+=1
        with pytest.raises(WriteError): await service.execute(plan['plan_id'],plan['plan_hash'],True)
        service.accounts.revision-=1;now[0]=121
        with pytest.raises(WriteError): await service.execute(plan['plan_id'],plan['plan_hash'],True)
        now[0]=0;service.mock_strategies['synthetic-strategy']['enabled']=True
        with pytest.raises(WriteError): await service.execute(plan['plan_id'],plan['plan_hash'],True)
        assert not service.plans[plan['plan_id']]['attempted']
    asyncio.run(run())


def test_system_strategy_cannot_update_status_delete(tmp_path):
    async def run():
        service=fixture(tmp_path);service.mock_strategies['synthetic-strategy']['type']='MIN_EXT_PRICE'
        for name in ('STRATEGY_UPDATE','STRATEGY_STATUS','STRATEGY_DELETE'):
            with pytest.raises(WriteError): await service.preview(name,WRITE_REQUESTS[name])
    asyncio.run(run())


def test_turnover_cache_and_one_minute_limit(tmp_path):
    async def run():
        service=fixture(tmp_path);now=[0];service.clock=lambda:now[0]
        first=await service.read('ANALYTICS_TURNOVER',{'limit':1000,'offset':0})
        assert first==await service.read('ANALYTICS_TURNOVER',{'limit':1000,'offset':0})
        with pytest.raises(ReadError): await service.read('ANALYTICS_TURNOVER',{'limit':1000,'offset':1000})
        now[0]=60
        assert (await service.read('ANALYTICS_TURNOVER',{'limit':1000,'offset':1000}))['rows']==[]
    asyncio.run(run())


@pytest.mark.parametrize('status',[302,429,500])
def test_transport_single_send_no_redirect_or_retry(status):
    async def run():
        calls=[]
        def handler(request): calls.append(request);return httpx.Response(status,json={})
        client=StrategyWriteClient(Credentials('1','synthetic-key'),transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(WriteError): await client.send(StrategyOperation.STRATEGY_STATUS,WRITE_REQUESTS['STRATEGY_STATUS'])
        finally: await client.close()
        assert len(calls)==1 and str(calls[0].url)=='https://api-seller.ozon.ru/v1/pricing-strategy/status'
    asyncio.run(run())


def test_exact_coefficient_at_wire():
    async def run():
        def handler(request):
            assert b'"coefficient":0.950000000000000001' in request.content
            return httpx.Response(200,json={'result':{'strategy_id':'synthetic'}})
        client=StrategyWriteClient(Credentials('1','synthetic-key'),transport=httpx.MockTransport(handler))
        try: await client.send(StrategyOperation.STRATEGY_CREATE,{'strategy_name':'Synthetic','competitors':[{'competitor_id':11,'coefficient':'0.950000000000000001'}]})
        finally: await client.close()
    asyncio.run(run())


def test_live_synthetic_marker_before_send_and_unknown_recovery(tmp_path):
    async def run():
        service=fixture(tmp_path,'LIVE_READ'); synthetic=fixture(tmp_path)
        async def read(op,request): return synthetic._mock_read(op.name,request)
        service.reader=read
        class Writer:
            async def send(self,op,request):
                marker=next((tmp_path/'markers').glob('*.json'))
                document=json.loads(marker.read_text('utf8'))
                assert set(document)=={'id','status'} and document['status']=='UNKNOWN_RESULT'
                raise WriteError('UNKNOWN_RESULT','Synthetic lost response')
            async def close(self): pass
        service.writer_factory=lambda _:Writer()
        plan=await service.preview('STRATEGY_STATUS',WRITE_REQUESTS['STRATEGY_STATUS'])
        result=await service.execute(plan['plan_id'],plan['plan_hash'],True)
        assert result['status']=='UNKNOWN_RESULT'
        recovered=SellerExtensions(service.accounts,read,root=tmp_path,path=tmp_path/'markers')
        assert recovered.recovered==[{'id':plan['plan_id'],'status':'UNKNOWN_RESULT'}]
        with pytest.raises(WriteError): await service.execute(plan['plan_id'],plan['plan_hash'],True)
        synthetic.mock_strategies['synthetic-strategy']['enabled']=True
        assert (await service.reconcile(plan['plan_id'],plan['plan_hash']))['status']=='SUCCESS'
    asyncio.run(run())


def test_partial_per_item_requires_readback(tmp_path):
    async def run():
        service=fixture(tmp_path,'LIVE_READ');synthetic=fixture(tmp_path)
        async def read(op,request): return synthetic._mock_read(op.name,request)
        service.reader=read
        class Writer:
            async def send(self,op,request):
                synthetic.mock_members['102']='synthetic-strategy'
                return {'result':{'failed_product_count':1,'errors':[{'product_id':103,'code':'INVALID_PRODUCT','error':'Synthetic rejection'}]}}
            async def close(self): pass
        service.writer_factory=lambda _:Writer()
        plan=await service.preview('STRATEGY_ADD_PRODUCTS',WRITE_REQUESTS['STRATEGY_ADD_PRODUCTS'])
        result=await service.execute(plan['plan_id'],plan['plan_hash'],True)
        assert result['status']=='PARTIAL' and result['success_count']==1 and result['error_count']==1
    asyncio.run(run())


@pytest.mark.parametrize('value',[{'strategy_id':'synthetic-new'},{'result':{'id':'synthetic-new'}},{'result':{'strategy_id':'synthetic-new'}}])
def test_current_rendered_create_variants(value):
    assert normalize_response('STRATEGY_CREATE',value)=={'result':{'strategy_id':'synthetic-new'}}


def test_current_rendered_variants_fail_conflicting_identity():
    with pytest.raises(ReadError): normalize_response('STRATEGY_CREATE',{'strategy_id':'one','result':{'id':'two'}})
    assert normalize_response('STRATEGY_INFO',{'result':{'strategy_name':'Synthetic'}})=={'result':{'name':'Synthetic'}}
    assert normalize_response('STRATEGY_PRODUCT_INFO',{'result':{'strategy_competitor_id':'00000000-0000-0000-0000-000000000011'}})['result']['strategy_competitor_id']=='00000000-0000-0000-0000-000000000011'


def test_unknown_contract_fails_live_before_preview(tmp_path):
    async def run():
        service=fixture(tmp_path,'LIVE_READ');service.verified_writes=frozenset()
        with pytest.raises(WriteError): await service.preview('STRATEGY_STATUS',WRITE_REQUESTS['STRATEGY_STATUS'])
        assert not service.plans
    asyncio.run(run())


def test_double_confirmation_concurrent_single_send(tmp_path):
    async def run():
        service=fixture(tmp_path)
        plan=await service.preview('STRATEGY_STATUS',WRITE_REQUESTS['STRATEGY_STATUS'])
        results=await asyncio.gather(*[service.execute(plan['plan_id'],plan['plan_hash'],True) for _ in range(2)],return_exceptions=True)
        assert sum(isinstance(value,WriteError) for value in results)==1
        assert sum(isinstance(value,dict) and value['status']=='SUCCESS' for value in results)==1
    asyncio.run(run())


def test_failed_marker_no_send(tmp_path):
    async def run():
        service=fixture(tmp_path)
        plan=await service.preview('STRATEGY_STATUS',WRITE_REQUESTS['STRATEGY_STATUS'])
        def fail(*args): raise OSError('Synthetic marker failure')
        service._marker=fail
        with pytest.raises(OSError): await service.execute(plan['plan_id'],plan['plan_hash'],True)
        assert service.mock_strategies['synthetic-strategy']['enabled'] is False
        assert not service.plans[plan['plan_id']]['attempted']
    asyncio.run(run())


def test_tampered_saved_plan_no_send(tmp_path):
    async def run():
        service=fixture(tmp_path)
        plan=await service.preview('STRATEGY_STATUS',WRITE_REQUESTS['STRATEGY_STATUS'])
        service.plans[plan['plan_id']]['document']['request']['strategy_id']='foreign'
        with pytest.raises(WriteError): await service.execute(plan['plan_id'],plan['plan_hash'],True)
        assert not service.plans[plan['plan_id']]['attempted']
    asyncio.run(run())


def test_failed_turnover_consumes_rate_slot_and_read_no_retry(tmp_path):
    from app.ozon_read import ReadClient
    async def run():
        service=fixture(tmp_path,'LIVE_READ');calls=[]
        def handler(request): calls.append(request);return httpx.Response(500,json={})
        client=ReadClient(Credentials('1','synthetic-key'),transport=httpx.MockTransport(handler));service.reader=client.read
        try:
            with pytest.raises(ReadError): await service.read('ANALYTICS_TURNOVER',{'limit':1000})
            with pytest.raises(ReadError): await service.read('ANALYTICS_TURNOVER',{'limit':1000})
        finally: await client.close()
        assert len(calls)==1
    asyncio.run(run())


def test_response_diagnostic_contains_field_and_types_only():
    with pytest.raises(ReadError) as error:
        normalize_response('ANALYTICS_STOCKS',{'items':[{'sku':1,'item_tags':'SYNTHETIC-PRIVATE-VALUE'}]})
    assert error.value.diagnostic=={'field_path':'$.items[0].item_tags','expected':'array','actual_type':'string'}
    assert 'SYNTHETIC' not in json.dumps(error.value.diagnostic)


def test_rejection_diagnostic_never_returns_raw_messages_or_private_identifiers():
    from app.ozon_read import ReadClient
    async def run():
        def handler(request): return httpx.Response(400,json={'code':3,'message':'company_id is required for SYNTHETIC-PRIVATE-CLIENT-111 key SYNTHETIC-PRIVATE-KEY'})
        client=ReadClient(Credentials('1','synthetic-key'),transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(ReadError) as error: await client.read(ReadOperation.STRATEGY_LIST,{'page':1,'limit':50})
        finally: await client.close()
        assert error.value.diagnostic=={'category':'REQUIRED_FIELD','upstream_status':400,'ozon_code':3,'fields':['company_id']}
        assert 'SYNTHETIC' not in json.dumps(error.value.diagnostic)
    asyncio.run(run())


def test_live_inventory_placement_zone_addressed_enum_scalar_variant():
    response={'items':[{'sku':1,'placement_zone':'PRODUCTS'}]}
    assert normalize_response('ANALYTICS_STOCKS',response)=={'items':[{'sku':'1','placement_zone':['PRODUCTS']}]}
    with pytest.raises(ReadError) as error:
        normalize_response('ANALYTICS_STOCKS',{'items':[{'sku':1,'placement_zone':'SYNTHETIC-UNKNOWN'}]})
    assert error.value.diagnostic['field_path']=='$.items[0].placement_zone'
    with pytest.raises(ReadError): normalize_response('ANALYTICS_STOCKS',{'items':[{'sku':1,'item_tags':'SUPER'}]})


def test_fresh_complete_pagination_uses_live_accepted_twenty(tmp_path):
    async def run():
        service=fixture(tmp_path,'LIVE_READ');calls=[]
        async def read(op,request):
            calls.append(request.copy())
            assert request['limit']==20
            start=(request['page']-1)*20
            return {'competitor':[{'id':index+1,'name':'Synthetic competitor'} for index in range(start,min(start+20,30))],'total':30}
        service.reader=read
        rows=await service._all('STRATEGY_COMPETITORS')
        assert len(rows)==30 and calls==[{'page':1,'limit':20},{'page':2,'limit':20}]
    asyncio.run(run())


@pytest.mark.parametrize('complete',[True,False])
def test_missing_bulk_membership_add_requires_explicit_product_absence(tmp_path,complete):
    async def run():
        service=fixture(tmp_path,'LIVE_READ');synthetic=fixture(tmp_path)
        async def read(op,request):
            if op is ReadOperation.STRATEGY_IDS: return {'result':{'products_info':[]}}
            if op is ReadOperation.STRATEGY_PRODUCT_INFO: return {'result':{'strategy_id':'','is_enabled':False}} if complete else {'result':{'strategy_id':''}}
            return synthetic._mock_read(op.name,request)
        service.reader=read
        if complete:
            plan=await service.preview('STRATEGY_ADD_PRODUCTS',WRITE_REQUESTS['STRATEGY_ADD_PRODUCTS'])
            snapshot=plan['before']['memberships']
            assert snapshot['result']['products_info']==[{'product_id':'102','strategy_id':''},{'product_id':'103','strategy_id':''}]
            assert set(snapshot['product_checks'])=={'102','103'}
        else:
            with pytest.raises(WriteError): await service.preview('STRATEGY_ADD_PRODUCTS',WRITE_REQUESTS['STRATEGY_ADD_PRODUCTS'])
            assert not service.plans
    asyncio.run(run())


@pytest.mark.parametrize('complete',[True,False])
def test_removed_membership_omitted_bulk_requires_individual_readback(tmp_path,complete):
    async def run():
        service=fixture(tmp_path,'LIVE_READ');synthetic=fixture(tmp_path);applied=[False];calls=[]
        async def read(op,request):
            if op is ReadOperation.STRATEGY_IDS and applied[0]: return {'result':{'products_info':[]}}
            if op is ReadOperation.STRATEGY_PRODUCT_INFO and applied[0]: return {'result':{'strategy_id':'','is_enabled':False}} if complete else {'result':{'strategy_id':''}}
            return synthetic._mock_read(op.name,request)
        service.reader=read
        class Writer:
            async def send(self,op,request):
                calls.append(op);applied[0]=True;return {'result':{'failed_product_count':0}}
            async def close(self): pass
        service.writer_factory=lambda _:Writer()
        plan=await service.preview('STRATEGY_REMOVE_PRODUCTS',WRITE_REQUESTS['STRATEGY_REMOVE_PRODUCTS'])
        result=await service.execute(plan['plan_id'],plan['plan_hash'],True)
        assert result['status']==('SUCCESS' if complete else 'UNKNOWN_RESULT')
        assert len(calls)==1
    asyncio.run(run())


@pytest.mark.parametrize('rows',[
    [{'product_id':101,'strategy_id':'a'},{'product_id':101,'strategy_id':'a'}],
    [{'product_id':999,'strategy_id':'a'}],
    [{'product_id':101}],
])
def test_membership_duplicate_unexpected_or_missing_identity_blocks(tmp_path,rows):
    async def run():
        service=fixture(tmp_path,'LIVE_READ')
        async def read(op,request): return {'result':{'products_info':rows}}
        service.reader=read
        with pytest.raises(WriteError): await service._memberships(['101'])
    asyncio.run(run())
