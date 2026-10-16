"""Synthetic rejection diagnostics across independent transport/history families."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
from app.connection import Credentials
from app.ozon_read import ReadClient, ReadOperation, ReadError
from app.ozon_write import WriteError
from app.seller_extensions import SellerExtensions, StrategyWriteClient, StrategyOperation, normalize_response
from app.warehouse_reports import WarehouseReports, WarehouseWriteClient, WarehouseWrite, normalize_warehouse_response
from app.performance_statistics import PerformanceStatisticsReader, PerformanceStatisticsQuery
from app.operation_history import EncryptedHistory

REASON='DiscountPercent must be greater or equal than 4.1622, but actual is 2.7027'

def test_documented_strategy_error_alias_and_unknown_reason_kept():
    result=normalize_response('STRATEGY_ADD_PRODUCTS',{'result':{'failed_product_count':1,'errors':[{'product_id':101,'code':'PRICE','error':REASON}]}})
    assert result['result']['errors'][0]['reason']==REASON
    result=normalize_warehouse_response('PRICE_UPDATE',{'result':[{'offer_id':'SYNTHETIC','updated':False,'errors':[{'code':'PRICE','reason':REASON,'private':'DROP'}]}]})
    assert result['result'][0]['errors'][0]['reason']==REASON
    assert 'private' not in str(result)

@pytest.mark.parametrize('family',['strategy','warehouse'])
def test_http200_top_error_envelope_not_silently_dropped(family):
    async def run():
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'code':'INVALID_ARGUMENT','message':REASON}))
        credentials=Credentials('synthetic-client','synthetic-key')
        client=StrategyWriteClient(credentials,transport=transport) if family=='strategy' else WarehouseWriteClient(credentials,transport=transport)
        try:
            with pytest.raises(WriteError) as caught:
                if family=='strategy':await client.send(StrategyOperation.STRATEGY_ADD_PRODUCTS,{'strategy_id':'synthetic-strategy','product_id':['101']})
                else:await client.send(WarehouseWrite.PRICE_UPDATE,{'prices':[{'offer_id':'SYNTHETIC','price':'100','currency_code':'RUB'}]})
            assert caught.value.code=='REJECTED'
            assert caught.value.vendor_error['reason']==REASON
        finally:await client.close()
    asyncio.run(run())

def accounts():
    return SimpleNamespace(mode='MOCK',active_id='synthetic-account',revision=1,busy=False,credentials=lambda:Credentials('synthetic-client','synthetic-key'))

async def no_network(*args):
    raise AssertionError('LIVE request forbidden')

@pytest.mark.parametrize('family',['strategy','warehouse','read','performance'])
@pytest.mark.parametrize('status',[400,503])
def test_http_reasons_translated_and_credentials_redacted(family,status,tmp_path):
    async def run():
        transport=httpx.MockTransport(lambda request:httpx.Response(status,headers={'retry-after':'120'},json={'code':'INVALID_ARGUMENT','message':REASON+' synthetic-key synthetic-client'}))
        if family=='strategy':
            client=StrategyWriteClient(Credentials('synthetic-client','synthetic-key'),transport=transport)
            call=lambda:client.send(StrategyOperation.STRATEGY_ADD_PRODUCTS,{'strategy_id':'synthetic-strategy','product_id':['101']})
        elif family=='warehouse':
            client=WarehouseWriteClient(Credentials('synthetic-client','synthetic-key'),transport=transport)
            call=lambda:client.send(WarehouseWrite.PRICE_UPDATE,{'prices':[{'offer_id':'SYNTHETIC','price':'100','currency_code':'RUB'}]})
        elif family=='read':
            client=ReadClient(Credentials('synthetic-client','synthetic-key'),transport=transport)
            call=lambda:client.read(ReadOperation.STRATEGY_LIST,{'page':1,'limit':50})
        else:
            client=PerformanceStatisticsReader('MOCK',root=tmp_path,transport=transport)
            call=lambda:client.fetch('GET','/api/client/statistics/daily/json',headers={'Authorization':'Bearer synthetic-key'},json={'client_id':'synthetic-client'})
        try:
            with pytest.raises((WriteError,ReadError)) as caught:await call()
            error=caught.value.vendor_error
            assert REASON in error['reason']
            assert '4.1622%' in error['reason_ru']
            assert 'synthetic-key' not in error['reason'] and 'synthetic-client' not in error['reason']
        finally:await client.close()
    asyncio.run(run())

def test_strategy_scoped_reason_survives_restart(tmp_path):
    async def run():
        subject=SellerExtensions(accounts(),no_network,root=tmp_path)
        subject._mock_write=lambda name,body:{'result':{'failed_product_count':1,'errors':[{'product_id':102,'reason':REASON},{'product_id':999,'reason':'UNRELATED PRIVATE'}]}}
        plan=await subject.preview('STRATEGY_ADD_PRODUCTS',{'strategy_id':'synthetic-strategy','product_id':['102']})
        result=await subject.execute(plan['plan_id'],plan['plan_hash'],True)
        assert result['items'][0]['errors'][0]['reason']==REASON
        assert result['status']=='REJECTED'
        restored=SellerExtensions(accounts(),no_network,root=tmp_path)
        history=restored.history()['items']
        assert history[0]['items'][0]['errors'][0]['reason_ru'].startswith('Скидка')
        assert 'UNRELATED' not in str(history)
        assert REASON.encode() not in restored.journal.path.read_bytes()
    asyncio.run(run())

def test_warehouse_price_reason_survives_restart(tmp_path):
    async def run():
        subject=WarehouseReports(accounts(),no_network,root=tmp_path)
        subject._mock_write=lambda name,body:{'result':[{'offer_id':'DEMO-1','updated':False,'errors':[{'code':'PRICE','message':REASON}]},{'offer_id':'OTHER','errors':[{'message':'UNRELATED'}]}]}
        plan=await subject.preview('PRICE_UPDATE',{'prices':[{'offer_id':'DEMO-1','price':'900','currency_code':'RUB'}]})
        result=await subject.execute(plan['plan_id'],plan['plan_hash'],True)
        assert result['status']=='REJECTED'
        assert result['items'][0]['errors'][0]['reason']==REASON
        restored=WarehouseReports(accounts(),no_network,root=tmp_path)
        assert restored.history()['items'][0]['items'][0]['errors'][0]['reason']==REASON
        assert 'UNRELATED' not in str(restored.history())
    asyncio.run(run())

@pytest.mark.parametrize('vendor_error',[REASON,{'code':'PRICE','message':REASON}])
def test_report_terminal_error_kept_encrypted(tmp_path,vendor_error):
    journal=EncryptedHistory(tmp_path,'mock-performance-tests')
    reader=PerformanceStatisticsReader('MOCK',root=tmp_path,history_store=journal)
    event={'id':str(uuid4()),'operation':'campaign_report','mode':'MOCK','status':'PENDING','report_uuid':'00000000-0000-4000-8000-000000000001'}
    journal.upsert(event)
    result=reader._report_outcome({'UUID':event['report_uuid'],'state':'ERROR','error':vendor_error},'status')
    assert result['error']['reason']==REASON
    restored=EncryptedHistory(tmp_path,'mock-performance-tests')
    assert restored.entries()[0]['error']['reason_ru'].startswith('Скидка')
    assert REASON.encode() not in journal.path.read_bytes()
    asyncio.run(reader.close())

@pytest.mark.parametrize('status',['UNKNOWN_REPORT_RESULT','PENDING'])
def test_attach_incomplete_journal_blocks_generation_without_marker(tmp_path,status):
    async def run():
        journal=EncryptedHistory(tmp_path,'mock-performance-incomplete')
        event={'id':str(uuid4()),'operation':'campaign_report','mode':'MOCK','status':status}
        if status=='PENDING':event['report_uuid']='00000000-0000-4000-8000-000000000001'
        journal.upsert(event)
        reader=PerformanceStatisticsReader('MOCK',root=tmp_path)
        reader.set_history_store(journal)
        try:
            with pytest.raises(ReadError,match='Сначала сверьте'):await reader.load(PerformanceStatisticsQuery(operation='campaign_report',date_from='2026-10-01',date_to='2026-10-02',campaign_ids=['101'],confirmed=True))
            assert reader.recovered
        finally:await reader.close()
    asyncio.run(run())

def test_strategy_incomplete_journal_blocks_after_restart(tmp_path):
    async def run():
        subject=SellerExtensions(accounts(),no_network,root=tmp_path)
        subject.journal.upsert({'id':str(uuid4()),'operation':'STRATEGY_STATUS','mode':'MOCK','account_id':'synthetic-account','status':'UNKNOWN_RESULT'})
        restored=SellerExtensions(accounts(),no_network,root=tmp_path)
        plan=await restored.preview('STRATEGY_STATUS',{'strategy_id':'synthetic-strategy','enabled':True})
        with pytest.raises(WriteError,match='Сначала сверка'):await restored.execute(plan['plan_id'],plan['plan_hash'],True)
    asyncio.run(run())
