"""Invented financial wire responses; no Seller/Performance keys or network."""
from .ozon_read import ReadOperation as Op


def rub(amount):return {'amount':str(amount),'currency':'RUB'}
def value(amount):return {'value':str(amount),'currency_code':'RUB'}


class FinanceMock:
    async def read(self,op,body=None):
        if op is Op.FINANCE_BALANCE:return {'total':{'accrued':value('12345.67'),'payments':[value('9000')],'opening_balance':value('2000'),'closing_balance':value('5345.67')},'cashflows':{'sales':{'amount':value('18000'),'fee':value('-1800')},'returns':{'amount':value('-1000'),'fee':value('100')},'services':[{'name':'Синтетическая логистика','amount':value('-2500')} ]}}
        if op is Op.FINANCE_CASHFLOW:
            period={'id':1,'begin':body['date']['from'],'end':body['date']['to']}
            return {'result':{'page_count':1,'cash_flows':[{'period':period,'orders_amount':'18000','returns_amount':'1000','commission_amount':'1800','services_amount':'600','item_delivery_and_return_amount':'2500','currency_code':'RUB'}],'details':[{'period':period,'invoice_transfer':'9500','payments':[{'payment':'9000','currency_code':'RUB'}]}]}}
        if op is Op.FINANCE_TYPES:return {'accrual_types':[{'id':1,'name':'Синтетическая комиссия','description':'Выдуманный справочник'},{'id':2,'name':'Синтетическая доставка','description':'Выдуманный справочник'}]}
        if op is Op.FINANCE_DAY:return {'accruals':[{'accrual_id':1,'date':body['date'],'unit_number':'SYNTHETIC-POSTING','total_amount':rub('100'),'item_fees':{'fees':[{'sku':9007199254740993,'fees':[{'type_id':1,'accrued':rub('-10')}]}]}}],'last_id':''}
        if op is Op.FINANCE_POSTINGS:return {'posting_accruals':[{'posting_number':p,'accruals':[{'sku':9007199254740993,'type_id':1,'accrual_date':'2026-10-01','quantity':1,'accrued':rub('-10')}]} for p in body['posting_numbers']]}
        if op in (Op.FINANCE_REALIZATION,Op.FINANCE_REALIZATION_DAY):
            rows=[{'item':{'sku':9007199254740993,'offer_id':'SYN-1','name':'Синтетическая кружка'},'delivery_commission':{'quantity':10,'amount':'10000','standard_fee':'1000','total':'9000'},'return_commission':{'quantity':1,'amount':'1000'}}]
            return {'result':{'header':{'currency_sys_name':'RUB'},'rows':rows}} if op is Op.FINANCE_REALIZATION else {'rows':rows}
        if op is Op.FINANCE_REPORT_INFO:return {'result':{'status':'success','file':'https://ir.ozone.ru/synthetic-not-a-real-file.xlsx','created_at':'2026-10-09T00:00:00Z','expires_at':'2026-10-12T00:00:00Z'}}
        raise AssertionError('No remote operation allowed in financial MOCK')
    async def create_finance_report(self,kind,month):return {'result':{'code':'SYNTHETIC_REPORT_'+kind.name}}
