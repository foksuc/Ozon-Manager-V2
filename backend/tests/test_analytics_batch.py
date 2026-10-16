"""All-catalogue selection and 100-SKU reads; invented wire fixtures only."""
import asyncio
from types import SimpleNamespace
import pytest
from app.analytics_batch import AnalyticsBatch
from app.ozon_read import ReadOperation as Op,ReadError


class Service:
    def __init__(self):self.accounts=SimpleNamespace(mode='LIVE_READ');self.revision=1;self.calls=[]
    def binding(self):return ('synthetic-account',self.revision)
    async def read(self,name,request):
        self.calls.append((name,request))
        return {'rows':[{'sku':sku,'available_stock_count':1} for sku in request['skus']],'complete':True,'warnings':[]}


def run(awaitable):return asyncio.run(awaitable)


def test_all_251_selected_skus_are_read_in_three_requests_without_truncation():
    service=Service();result=run(AnalyticsBatch(service,None).stocks([str(i) for i in range(1,252)],{'turnover_grades':['DEFICIT']}))
    assert result['complete'] and result['processed_skus']==251 and len(result['rows'])==251
    assert [len(body['skus']) for _,body in service.calls]==[100,100,51]
    assert all(body['turnover_grades']==['DEFICIT'] for _,body in service.calls)
    assert result['remaining_skus']==[] and not result['continue_automatically']


def test_budget_has_explicit_automatic_continuation_and_remaining_selectors():
    service=Service();ids=[str(i) for i in range(1,10102)]
    result=run(AnalyticsBatch(service,None).stocks(ids,{}))
    assert len(service.calls)==100 and result['processed_skus']==10000
    assert result['remaining_skus']==ids[10000:] and result['continue_automatically'] and not result['complete']


def test_read_error_preserves_previous_batch_and_is_not_retried():
    class Failure(Service):
        async def read(self,name,request):
            if self.calls:raise ReadError('UPSTREAM','Синтетическая ошибка чтения')
            return await super().read(name,request)
    service=Failure();ids=[str(i) for i in range(1,202)]
    result=run(AnalyticsBatch(service,None).stocks(ids,{}))
    assert len(service.calls)==1 and result['processed_skus']==100
    assert result['remaining_skus']==ids[100:] and not result['continue_automatically']
    assert not result['complete'] and result['warnings']==['Синтетическая ошибка чтения']


@pytest.mark.parametrize('skus,filters', [(['1','1'],{}),(['1'],{'skus':['2']}),(['1'],{'cluster_ids':['1'],'macrolocal_cluster_ids':['2']}),(['1'],{'arbitrary':'invalid'}),(['1','9223372036854775808'],{})])
def test_bad_selection_filters_are_validated_before_first_request(skus,filters):
    service=Service()
    with pytest.raises((ValueError,ReadError)):run(AnalyticsBatch(service,None).stocks(skus,filters))
    assert not service.calls


def test_account_changed_during_read_is_never_returned_as_old_account_rows():
    class Changed(Service):
        async def read(self,name,request):
            result=await super().read(name,request);self.revision+=1;return result
    service=Changed()
    with pytest.raises(ReadError,match='Магазин'):run(AnalyticsBatch(service,None).stocks(['1'],{}))


def reader_fixture(listing,cards):
    calls=[]
    async def reader(op,body):
        calls.append((op,body))
        return {'result':listing} if op is Op.PRODUCT_LIST else {'items':cards}
    return reader,calls


def test_catalogue_uses_product_ids_for_info_and_skus_for_selection_no_id_alias():
    reader,calls=reader_fixture({'items':[{'product_id':101}],'last_id':'','total_items':1},[{'id':101,'name':'Товар MOCK','offer_id':'SYNTHETIC','sku':9007199254740993,'sources':[{'sku':9007199254740994}]}])
    result=run(AnalyticsBatch(Service(),reader).catalogue_page())
    assert result['complete'] and result['next_cursor'] is None
    assert [row['sku'] for row in result['rows']]==['9007199254740993','9007199254740994']
    assert calls[1]==(Op.PRODUCT_INFO,{'product_id':['101']})


def test_missing_card_and_no_sku_are_explicit_incomplete_not_zero():
    reader,_=reader_fixture({'items':[{'product_id':101},{'product_id':102}],'last_id':'','total':2},[{'id':101,'name':'Товар MOCK'}])
    result=run(AnalyticsBatch(Service(),reader).catalogue_page())
    assert not result['complete'] and result['rows']==[] and len(result['warnings'])==2


def test_repeating_catalogue_cursor_rejected_before_info():
    reader,calls=reader_fixture({'items':[{'product_id':101}],'last_id':'synthetic-cursor','total_items':2},[])
    with pytest.raises(ReadError,match='Указатель'):run(AnalyticsBatch(Service(),reader).catalogue_page('synthetic-cursor'))
    assert len(calls)==1


def test_mock_catalogue_never_reads_live_client():
    service=Service();service.accounts.mode='MOCK'
    async def forbidden(*args):raise AssertionError('No network')
    result=run(AnalyticsBatch(service,forbidden).catalogue_page())
    assert result['complete'] and len(result['rows'])==4


@pytest.mark.parametrize('listing',[{'items':[],'total':0,'total_items':1},{'items':[],'total':True},{'items':[],'total_items':1.2}])
def test_total_migration_conflict_or_noninteger_is_not_silently_accepted(listing):
    reader,_=reader_fixture(listing,[])
    with pytest.raises(ReadError):run(AnalyticsBatch(Service(),reader).catalogue_page())


def test_missing_next_cursor_does_not_claim_full_catalogue():
    reader,_=reader_fixture({'items':[{'product_id':101}],'total_items':2},[{'id':101,'name':'MOCK','sku':30001}])
    result=run(AnalyticsBatch(Service(),reader).catalogue_page())
    assert not result['complete'] and result['warnings'] and result['next_cursor'] is None


def test_response_with_unselected_sku_is_not_added_to_selected_report():
    class Unexpected(Service):
        async def read(self,name,request):return {'rows':[{'sku':'999'}],'complete':True,'warnings':[]}
    result=run(AnalyticsBatch(Unexpected(),None).stocks(['1'],{}))
    assert not result['complete'] and result['rows']==[] and result['processed_skus']==0
    assert result['remaining_skus']==['1'] and not result['continue_automatically']
