"""Synthetic financial contracts; not evidence of seller/bank values."""
import asyncio
from datetime import date
from decimal import Decimal
import json
from copy import deepcopy
from unittest.mock import patch
import httpx
import pytest
from fastapi.testclient import TestClient
from app.connection import Connection,Credentials
from app.finance import FinancialReader,FinanceQuery,ProfitQuery,profit,total
from app.finance_mock import FinanceMock
from app.finance_contracts import validate_finance
from app.ozon_read import ReadClient,ReadError,ReadOperation as Op,FinanceReport,validate_request
from app.performance_read import PerformanceReader
from app.main import create_app


def query(view='overview',**changes):
    values={'view':view,'date_from':'2026-09-01','date_to':'2026-09-03'};values.update(changes)
    return FinanceQuery(**values)


def load(client=None,view='overview',**params):
    return asyncio.run(FinancialReader(client or FinanceMock(),'MOCK').load(query(view,**params)))


VALID=[(Op.FINANCE_BALANCE,{'date_from':'2026-09-01','date_to':'2026-09-30'}),
    (Op.FINANCE_CASHFLOW,{'date':{'from':'2026-09-01T00:00:00Z','to':'2026-09-30T23:59:59Z'},'page':1,'page_size':100,'with_details':True}),
    (Op.FINANCE_DAY,{'date':'2026-09-01','last_id':''}),
    (Op.FINANCE_POSTINGS,{'posting_numbers':['SYNTHETIC-POSTING']}),(Op.FINANCE_TYPES,None),
    (Op.FINANCE_REALIZATION,{'month':9,'year':2026}),
    (Op.FINANCE_REALIZATION_DAY,{'year':date.today().year,'month':date.today().month,'day':date.today().day}),
    (Op.FINANCE_REPORT_INFO,{'code':'SYNTHETIC_REPORT'})]


@pytest.mark.parametrize('op,payload',VALID)
def test_finance_allowlist_and_request_contract(op,payload):validate_request(op,payload)


@pytest.mark.parametrize('op,payload',[(op,{**(body or {}),'url':'https://evil.invalid'}) for op,body in VALID])
def test_finance_overrides_rejected_before_transport(op,payload):
    with pytest.raises(ReadError):validate_request(op,payload)


@pytest.mark.parametrize('view',['overview','payouts','daily-balance','accruals','types','products','postings'])
def test_all_synthetic_views_have_table_and_string_amounts(view):
    result=load(view=view,postings=['SYNTHETIC-POSTING'])
    assert result['complete'] and result['columns'] and result['rows']
    assert not any(isinstance(v,(Decimal,float)) for row in result['rows'] for v in row.values())
    assert result['mode']=='MOCK'


def test_balance_chunking_no_overlap_closing_not_summed():
    class Spy(FinanceMock):
        def __init__(self):self.calls=[]
        async def read(self,op,body=None):self.calls.append((op,deepcopy(body)));return await super().read(op,body)
    spy=Spy();result=load(spy,date_from='2026-08-01',date_to='2026-08-31')
    assert [body for op,body in spy.calls]==[{'date_from':'2026-08-01','date_to':'2026-08-30'},{'date_from':'2026-08-31','date_to':'2026-08-31'}]
    metrics={m['label']:m['amount'] for m in result['metrics']}
    assert metrics['Начислено']=='24691.34' and metrics['Выплачено']=='18000'
    assert metrics['Баланс на конец']=='5345.67' and metrics['Баланс на начало']=='2000'


def test_currency_groups_never_added_as_one_balance():
    assert total([{'amount':'1.01','currency':'RUB'},{'amount':'2.02','currency':'USD'}])['amount'] is None
    assert total([{'amount':'1.01','currency':None}])['amount'] is None
    assert total([{'amount':'1.01','currency':'RUB'},{'amount':'2.02','currency':'RUB'}])=={'amount':'3.03','currency':'RUB'}


def test_accrual_details_not_counted_twice_large_sku_exact():
    result=load(view='accruals')
    assert result['metrics'][0]['amount']=='300'
    assert len(result['details'])==3 and result['details'][0]['amount']=='-10'
    assert result['details'][0]['sku']=='9007199254740993'


def test_duplicate_or_nonadvancing_cursor_is_partial_without_final_metrics():
    class Loop(FinanceMock):
        async def read(self,op,body=None):
            data=await super().read(op,body)
            if op is Op.FINANCE_DAY:data['last_id']='SYNTHETIC_CURSOR'
            return data
    result=load(Loop(),view='accruals')
    assert not result['complete'] and result['error_code']=='PAGINATION' and not result['metrics']
    assert result['read_calls']==3


def test_cashflow_page_repetition_partial_not_duplicate_payments():
    class Duplicate(FinanceMock):
        async def read(self,op,body=None):
            data=await super().read(op,body)
            if op is Op.FINANCE_CASHFLOW:data['result']['page_count']=2
            return data
    result=load(Duplicate(),view='payouts')
    assert not result['complete'] and len(result['rows'])==1
    assert result['rows'][0]['paid']=='9000' and result['rows'][0]['invoice']=='9500'


def test_realization_legal_header_never_returned():
    class Private(FinanceMock):
        async def read(self,op,body=None):
            data=await super().read(op,body)
            data['result']['header']['receiver_name']='SYNTHETIC_PRIVATE_HEADER'
            return data
    result=load(Private(),view='products')
    assert 'SYNTHETIC_PRIVATE_HEADER' not in json.dumps(result)
    assert result['rows'][0]['sku']=='9007199254740993'


def test_broken_structure_is_not_fake_empty_success():
    class Broken:
        async def read(self,*args):return {'result':'broken'}
    result=load(Broken(),view='payouts')
    assert not result['complete'] and not result['metrics']


def test_nullable_realization_return_block_keeps_other_rows_and_unknowns():
    class Nullable(FinanceMock):
        async def read(self,op,body=None):
            data=await super().read(op,body)
            data['result']['rows'][0]['return_commission']=None
            return data
    result=load(Nullable(),view='products')
    assert result['complete'] and result['rows'][0]['net']=='9000'
    assert result['rows'][0]['returned'] is None and result['rows'][0]['returns'] is None


def test_daily_realization_never_invents_currency():
    result=load(view='daily-products',date_from=str(date.today()),date_to=str(date.today()))
    assert result['complete'] and result['rows'][0]['currency'] is None


def test_posting_totals_and_mean_only_requested_nonduplicated_entities():
    result=load(view='postings',postings=['SYN-A','SYN-B'])
    assert result['complete'] and result['metrics'][0]['amount']=='-20'
    assert result['metrics'][1]['amount']=='-10.00'
    class Missing(FinanceMock):
        async def read(self,op,body=None):
            result=await super().read(op,body)
            result['posting_accruals']=result['posting_accruals'][:1]
            return result
    incomplete=load(Missing(),view='postings',postings=['SYN-A','SYN-B'])
    assert not incomplete['complete'] and incomplete['error_code']=='INCOMPLETE_POSTINGS' and not incomplete['metrics']


def test_commission_logistics_ratios_server_decimal_zero_sales_unknown():
    from app.finance import ratio
    result=load(view='payouts')
    assert result['rows'][0]['commission_ratio']=='10.00'
    assert result['rows'][0]['logistics_ratio']=='13.89'
    assert ratio('-1','0') is None and ratio(None,'100') is None
    assert ratio('0.1','0.3')=='33.33'


def test_posting_components_are_details_not_double_counted():
    class Components(FinanceMock):
        async def read(self,op,body=None):
            data=await super().read(op,body)
            if op is Op.FINANCE_DAY:
                data['accruals'][0]['posting']={'products':[{'sku':9007199254740993,'commission':{'sale_amount':{'amount':'123.45','currency':'RUB'}},'delivery':{'total_accrued':{'amount':'-5','currency':'RUB'},'services':[{'type_id':2,'accrued':{'amount':'-5','currency':'RUB'}}]}}]}
            return data
    result=load(Components(),view='accruals')
    assert result['complete'] and result['metrics'][0]['amount']=='300'
    assert len(result['details'])==12 and all(r['sku']=='9007199254740993' for r in result['details'])


@pytest.mark.parametrize('url',['https://evil.invalid/a.xlsx','https://ir.ozone.ru.evil.invalid/a.xlsx','https://ir.ozone.ru:bad/a.xlsx','https://user:secret@ir.ozone.ru/a.xlsx','http://ir.ozone.ru/a.xlsx'])
def test_report_file_rejects_untrusted_or_malformed_link(url):
    from app.finance import report_file
    assert report_file(url) is None


@pytest.mark.parametrize('mode',['network','500','bad_json'])
def test_report_creation_unknown_result_is_never_retried(mode):
    calls=[]
    def handler(request):
        calls.append(request)
        assert str(request.url)=='https://api-seller.ozon.ru/v1/finance/compensation'
        if mode=='network':raise httpx.ReadTimeout('SYNTHETIC_SECRET',request=request)
        if mode=='500':return httpx.Response(500,json={'message':'SYNTHETIC_SECRET'})
        return httpx.Response(200,content=b'not JSON')
    async def run():
        c=ReadClient(Credentials('10001','SYNTHETIC_KEY'),transport=httpx.MockTransport(handler))
        try:await c.create_finance_report(FinanceReport.COMPENSATION,'2026-09')
        finally:await c.close()
    with pytest.raises(ReadError) as exc:asyncio.run(run())
    assert len(calls)==1 and exc.value.code=='UNKNOWN_REPORT_RESULT' and 'SYNTHETIC_SECRET' not in str(exc.value)


def test_report_creation_cannot_be_called_as_read():
    with pytest.raises(ReadError):validate_request(FinanceReport.MUTUAL,{'date':'2026-09'})


def test_mock_all_routes_no_key_network_store():
    app=create_app(store=object(),connection=Connection('MOCK'))
    with patch.object(PerformanceReader,'credentials',side_effect=AssertionError('No key in MOCK')),patch.object(httpx.AsyncClient,'send',side_effect=AssertionError('No remote calls in MOCK')):
        with TestClient(app,base_url='http://127.0.0.1') as client:
            for view in ('overview','payouts','accruals','postings','types','products','daily-balance'):
                response=client.post('/api/finance',json=query(view,postings=['SYNTHETIC-POSTING']).model_dump())
                assert response.status_code==200 and response.json()['complete']
            report=client.post('/api/finance/reports',json={'kind':'compensation','month':'2026-09'})
            assert report.status_code==200
            repeated=client.post('/api/finance/reports',json={'kind':'compensation','month':'2026-09'})
            assert repeated.json()['reused'] and repeated.json()['code']==report.json()['code']
            assert client.post('/api/finance/advertising',json=query().model_dump()).json()['mode']=='MOCK'
            assert client.post('/api/finance',json={**query().model_dump(),'url':'https://evil.invalid'}).status_code==422


def test_profit_scenario_decimal_zero_revenue_not_infinite():
    result=profit(ProfitQuery(revenue='1999.05',cost='1000',expenses='99.05'))
    assert result['profit']=='900.00' and result['source']=='USER_SCENARIO'
    assert profit(ProfitQuery(revenue='0',cost='1',expenses='0'))['margin'] is None
    with pytest.raises(ValueError):profit(ProfitQuery(revenue='NaN',cost='1',expenses='0'))


def test_performance_fixed_host_token_separate_cached_csv_no_disk():
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.url.host=='api-performance.ozon.ru' and 'Api-Key' not in request.headers
        if request.url.path=='/api/client/token':return httpx.Response(200,json={'access_token':'SYNTHETIC_TOKEN','expires_in':1800,'token_type':'Bearer'})
        assert request.headers['Authorization']=='Bearer SYNTHETIC_TOKEN'
        return httpx.Response(200,content='Кампания;Расход\nСинтетическая;12.34'.encode())
    async def run():
        reader=PerformanceReader('LIVE_READ',transport=httpx.MockTransport(handler))
        try:
            with patch.object(reader,'credentials',return_value=('SYNTHETIC_CLIENT','SYNTHETIC_SECRET')):
                result=await reader.expenses('2026-09-01','2026-09-03')
                await reader.expenses('2026-09-01','2026-09-03')
            return result
        finally:await reader.close()
    result=asyncio.run(run())
    assert result['rows']==[['Синтетическая','12.34']] and len(calls)==3


def test_performance_redirect_never_forwards_token():
    async def run():
        reader=PerformanceReader('LIVE_READ',transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'Location':'https://evil.invalid'})))
        try:
            with patch.object(reader,'credentials',return_value=('SYNTHETIC','SYNTHETIC')):
                await reader.expenses('2026-09-01','2026-09-03')
        finally:await reader.close()
    with pytest.raises(ReadError):asyncio.run(run())
