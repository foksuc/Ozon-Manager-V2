"""Dashboard acceptance from invented finance wire responses only."""
import asyncio
from copy import deepcopy
from app.finance import FinancialReader,FinanceQuery,chart_rows,chart_totals,service_label,SERVICE_LABELS
from app.finance_mock import FinanceMock,value
from app.ozon_read import ReadOperation as Op,ReadError


def load(client=None,start='2026-09-01',end='2026-09-03',view='analytics'):
    return asyncio.run(FinancialReader(client or FinanceMock(),'MOCK').load(FinanceQuery(view=view,date_from=start,date_to=end)))


def test_one_analytics_load_computes_all_views_and_exact_ratios():
    data=load()
    dashboard=data['dashboard']
    assert data['complete'] and data['mode']=='MOCK'
    assert {section['key'] for section in dashboard['sections']}=={'current','previous','expenses','daily'}
    assert len(dashboard['daily'])==3
    assert dashboard['previous_period']=={'from':'2026-08-29','to':'2026-08-31'}
    assert len(dashboard['comparison'])==4
    assert all(row['delta']=='0.00' or row['delta']=='0' for row in dashboard['comparison'])
    expenses={row['label']:row for row in dashboard['expenses']}
    assert expenses['Комиссия']['ratio']=='10.00'
    assert expenses['Логистика']['ratio']=='13.89'
    assert expenses['Услуги']['amount']=='600'
    assert data['read_calls']==6
    assert all(isinstance(row['amount'],str) for row in dashboard['expenses'])


def test_exact_decimal_comparison_and_zero_base():
    class Different(FinanceMock):
        async def read(self,op,body=None):
            result=deepcopy(await super().read(op,body))
            if op is Op.FINANCE_BALANCE:
                current=body['date_from']>='2026-09-01'
                result['total']['accrued']=value('0.3' if current else '0.1')
                result['total']['payments']=[value('10' if current else '0')]
            return result
    comparison=load(Different())['dashboard']['comparison']
    accrued=next(row for row in comparison if row['label']=='Начислено')
    assert accrued['delta']=='0.2' and accrued['change']=='200.00'
    paid=next(row for row in comparison if row['label']=='Выплачено')
    assert paid['delta']=='10' and paid['change'] is None


def test_partial_expense_response_preserves_confirmed_current_not_fake_expenses():
    class Partial(FinanceMock):
        async def read(self,op,body=None):
            if op is Op.FINANCE_CASHFLOW:raise ReadError('PERMISSION','Недостаточно прав')
            return await super().read(op,body)
    result=load(Partial())
    assert not result['complete']
    assert result['metrics'] and result['dashboard']['expenses']==[]
    assert any(not section['complete'] and section['key']=='expenses' for section in result['dashboard']['sections'])
    assert result['dashboard']['daily']


def test_long_period_omits_daily_with_explicit_limit_but_keeps_comparison():
    result=load(start='2026-08-01',end='2026-09-03')
    assert result['complete'] and result['dashboard']['comparison']
    assert result['dashboard']['daily']==[]
    assert any('31' in warning for section in result['dashboard']['sections'] if section['key']=='daily' for warning in section['warnings'])


def test_cashflows_aggregate_only_same_currency_and_keep_missing_amount():
    rows=[{'label':'Услуги','amount':'0.1','currency':'RUB'}, {'label':'Услуги','amount':'0.2','currency':'RUB'},
        {'label':'Услуги','amount':'3','currency':'CNY'}, {'label':'Логистика','amount':None,'currency':'RUB'}]
    result=chart_rows(rows)
    assert result[0]=={'label':'Услуги','amount':'0.3','currency':'RUB'}
    assert result[1]['currency']=='CNY' and result[1]['amount']=='3'
    assert result[2]['amount'] is None


def test_service_codes_translated_with_wire_provenance_and_unknown_visible():
    class Services(FinanceMock):
        async def read(self,op,body=None):
            result=deepcopy(await super().read(op,body))
            if op is Op.FINANCE_BALANCE:result['cashflows']['services']=[{'name':'packing_by_agents','amount':value('-20')}]
            return result
    result=load(Services(),view='overview')
    assert result['rows'][-1]['label']=='Упаковка агентами'
    assert result['rows'][-1]['service_code']=='packing_by_agents'
    assert service_label('brand_new_service')=='Услуга без перевода (brand_new_service)'
    assert service_label('Синтетическая логистика')=='Синтетическая логистика'
    assert service_label('MarketplaceServiceItemDirectFlowLogisticSum')=='Логистика'
    assert service_label('MarketplaceServiceItemReturnFlowLogistic')=='Обратная логистика'
    assert len(SERVICE_LABELS)>=45


def test_shared_read_budget_limits_all_analytics_subviews():
    class InfinitePages(FinanceMock):
        def __init__(self):self.calls=0
        async def read(self,op,body=None):
            self.calls+=1
            data=deepcopy(await super().read(op,body))
            if op is Op.FINANCE_CASHFLOW:
                data['result']['page_count']=500
                data['result']['cash_flows'][0]['period']['id']=body['page']
                data['result']['details'][0]['period']['id']=body['page']
            return data
    client=InfinitePages();result=load(client)
    assert client.calls<=150 and result['read_calls']<=150
    assert not result['complete'] and not result['dashboard']['expenses'] and not result['dashboard']['daily']


def test_chart_totals_exact_decimal_and_currencies_stay_separate():
    rows=[{'amount':'0.1','currency':'RUB'},{'amount':'0.2','currency':'RUB'},{'amount':'-2','currency':'CNY'}]
    assert chart_totals(rows)==[{'label':'Итог','amount':'0.3','currency':'RUB'},{'label':'Итог','amount':'-2','currency':'CNY'}]
    assert chart_totals(rows+[{'amount':None,'currency':'RUB'}])[0]['amount'] is None


def test_expense_total_excludes_returns_and_does_not_repeat_sales_denominator():
    rows=[{'label':'Комиссия','amount':'-0.1','currency':'RUB','sales':'2'}, {'label':'Логистика','amount':'-0.2','currency':'RUB','sales':'2'}, {'label':'Услуги','amount':'-0.3','currency':'RUB','sales':'2'}, {'label':'Возвраты','amount':'-900','currency':'RUB','sales':'2'}]
    assert chart_totals(rows,expense=True)==[{'label':'Итог расходов','amount':'-0.6','currency':'RUB','sales':'2','ratio':'30.00'}]
    rows[1]['sales']='3'
    assert chart_totals(rows,expense=True)[0]['ratio'] is None


def test_dashboard_contains_server_totals_and_partial_section_has_no_total():
    result=load()
    assert result['dashboard']['cashflow_totals'] and result['dashboard']['expense_totals']
    class Partial(FinanceMock):
        async def read(self,op,body=None):
            if op is Op.FINANCE_CASHFLOW:raise ReadError('PERMISSION','Нет доступа')
            return await super().read(op,body)
    assert load(Partial())['dashboard']['expense_totals']==[]
