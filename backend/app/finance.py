"""Financial reads and exact projections. No database, files, or bank operations."""
import asyncio
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import re
from urllib.parse import urlsplit
from pydantic import Field
from .models import StrictDTO
from .catalog import timestamp
from .finance_contracts import period, day, report_request
from .ozon_read import ReadOperation as Op, ReadError, FinanceReport
from .vendor_errors import normalize_vendor_error


class FinanceQuery(StrictDTO):
    view: str
    date_from: str
    date_to: str
    postings: list[str] = Field(default_factory=list,max_length=50)
    report_code: str | None = None


class ReportQuery(StrictDTO):
    kind: str
    month: str


class ProfitQuery(StrictDTO):
    revenue: str
    cost: str
    expenses: str


def decimal(raw):
    if isinstance(raw,bool) or not isinstance(raw,(str,int,Decimal)) or len(str(raw))>40:
        raise ValueError('Неподтверждённая сумма')
    result=Decimal(str(raw))
    if not result.is_finite() or abs(result)>Decimal('1e20') or result.as_tuple().exponent < -6:
        raise ValueError('Сумма вне диапазона')
    return result


def amount(raw):
    if raw is None:return None
    try:return format(decimal(raw),'f')
    except (ValueError,InvalidOperation):return None


def text(raw):
    return str(raw) if isinstance(raw,(str,int)) and not isinstance(raw,bool) else None


def money(raw):
    if not isinstance(raw,dict):return {'amount':None,'currency':None}
    return {'amount':amount(raw.get('value',raw.get('amount'))),'currency':text(raw.get('currency_code',raw.get('currency')))}


def total(values):
    if not values or any(v['amount'] is None or not v['currency'] for v in values):return {'amount':None,'currency':None}
    currencies={v['currency'] for v in values}
    if len(currencies)!=1:return {'amount':None,'currency':None}
    with localcontext() as ctx:
        ctx.prec=50
        return {'amount':format(sum((Decimal(v['amount']) for v in values),Decimal(0)),'f'),'currency':next(iter(currencies))}


def grouped_money(values):
    groups={}
    for v in values: groups.setdefault(v.get('currency'),[]).append(v)
    return [total(rows) for rows in groups.values()]


def column(key,label,kind='text'):return {'key':key,'label':label,'kind':kind}


# UI labels only; the original wire code is retained in service_code.
SERVICE_LABELS={
    # Labels from the three CashFlowStatement service schemas, snapshot 2026-10-03.
    'MarketplaceServiceItemElectronicServiceStencil':'Услуга «Трафареты»',
    'MarketplaceServiceItemElectronicServicesPromotionInSearch':'Услуга «Продвижение в поиске»',
    'MarketplaceServiceItemElectronicServicesBrandShelf':'Услуга «Брендовая полка»',
    'MarketplaceServiceBrandPromotion':'Услуга «Продвижение бренда»',
    'MarketplaceServiceBrandCommission':'Услуга «Продвижение бренда»',
    'MarketplaceServiceItemMarketingServices':'Маркетинговые услуги',
    'MarketplaceServiceItemTechnicalServicesAndOtherServices':'Технические и иные услуги',
    'MarketplaceServiceItemOtherElectronicServices':'Иные электронные услуги',
    'ItemAgentServiceStarsMembership':'Звёздные товары',
    'MarketplaceReturnStorageServiceAtThePickupPointFbsItem':'Краткосрочное размещение возврата FBS',
    'MarketplaceSaleReviewsItem':'Приобретение отзывов на платформе',
    'MarketplaceServicePremiumCashbackIndividualPoints':'Услуга продвижения «Бонусы продавца»',
    'OperationMarketplaceServiceStorage':'Услуга размещения товаров',
    'MarketplaceServiceStockDisposal':'Утилизация со стока',
    'MarketplaceReturnDisposalServiceFbsItem':'Утилизация FBS',
    'MarketplaceServiceItemFlexiblePaymentSchedule':'Услуга «Гибкий график выплат»',
    'MarketplaceServiceProcessingSpoilage':'Обработка брака',
    'MarketplaceServiceProcessingIdentifiedSurplus':'Обработка опознанных излишков',
    'MarketplaceServiceProcessingIdentifiedDiscrepancies':'Бронирование места для размещения на складе',
    'MarketplaceServiceItemInternetSiteAdvertising':'Реклама на сайте Ozon',
    'MarketplaceServiceItemSubscribtionPremium':'Премиум-подписка',
    'MarketplaceAgencyFeeAggregator3PLGlobalItem':'Агентское вознаграждение Ozon',
    'MarketplaceServiceItemDirectFlowLogisticSum':'Логистика',
    'MarketplaceServiceItemDirectFlowLogisticDC':'Логистика РЦ',
    'MarketplaceServiceItemDropoff':'Обработка отправления Drop-off',
    'MarketplaceServiceItemDirectFlowTrans':'Магистраль',
    'MarketplaceServiceDCFlowTrans':'Магистраль РЦ',
    'MarketplaceServiceItemFulfillment':'Сборка заказа',
    'MarketplaceServiceItemDelivToCustomer':'Последняя миля',
    'MarketplaceServiceItemReturnAfterDelivToCustomer':'Обработка возвратов',
    'MarketplaceServiceItemReturnPartGoodsCustomer':'Обработка частичного невыкупа',
    'MarketplaceServiceItemReturnNotDelivToCustomer':'Обработка отменённых и невостребованных товаров',
    'MarketplaceServiceItemReturnFlowLogistic':'Обратная логистика',
    'packing_by_agents':'Упаковка агентами',
    'promotion_with_cost_per_order':'Продвижение с оплатой за заказ',
    'defect_fine_shipment_delay_rated':'Штраф за просрочку отгрузки',
    'delivery_to_handover_place_by_ozon':'Доставка Ozon до места передачи',
    'logistics':'Логистика', 'acquiring':'Эквайринг', 'early_payment':'Досрочная выплата',
    'storage':'Хранение', 'delivery':'Доставка', 'returns':'Возвраты',
    'commission':'Комиссия', 'advertising':'Реклама',
}


def service_label(code):
    value=text(code)
    if not value:return 'Услуга без названия'
    return SERVICE_LABELS.get(value,value if not re.fullmatch(r'[a-z][a-z0-9_]*',value) else f'Услуга без перевода ({value})')


def chart_rows(rows,key='amount',label_key='label'):
    """Exact aggregation per label/currency, never accept partial amounts as zero."""
    groups={}
    for row in rows:
        identity=(row.get(label_key),row.get('currency'))
        groups.setdefault(identity,[]).append({'amount':row.get(key),'currency':row.get('currency')})
    return [{'label':label,**total(values)} for (label,_),values in groups.items()]


def chart_totals(rows, *, expense=False):
    """Signed totals from exact server amounts, separate per currency.

    Expense totals exclude returns: returns are a reversal of sales, rather
    than an Ozon service charge. Missing amounts never become zero.
    """
    groups={}
    for row in rows:
        if expense and row.get('label')=='Возвраты':continue
        groups.setdefault(row.get('currency'),[]).append(row)
    result=[]
    for currency,group in groups.items():
        value=total(group)
        row={'label':'Итог расходов' if expense else 'Итог',**value}
        if expense:
            sales=next((item.get('sales') for item in group),None)
            # One shared sales denominator per currency; never sum its repeats.
            if any(item.get('sales')!=sales for item in group):sales=None
            row.update(sales=sales,ratio=ratio(value['amount'],sales))
        result.append(row)
    return result


def ratio(numerator,denominator):
    if numerator is None or denominator is None:return None
    with localcontext() as ctx:
        ctx.prec=50
        base=Decimal(denominator)
        return format((abs(Decimal(numerator))/base*100).quantize(Decimal('0.01')),'f') if base>0 else None


def report_file(file):
    if not isinstance(file,str) or len(file)>8192 or any(ord(c)<32 for c in file):return None
    try:
        parsed=urlsplit(file)
        safe=bool(parsed.scheme=='https' and parsed.hostname and (parsed.hostname.endswith('.ozone.ru') or parsed.hostname=='cdn.ozon.ru') and not parsed.username and not parsed.password and parsed.port in (None,443))
        return file if safe else None
    except ValueError:return None


class FinancialReader:
    def __init__(self,client,mode='LIVE_READ'):
        self.client,self.mode=client,mode

    async def load(self,query:FinanceQuery):
        start,end=period(query.date_from,query.date_to)
        if query.view=='analytics':return await self.analytics(query,start,end)
        views={'overview','payouts','daily-balance','accruals','postings','types','products','daily-products','report'}
        if query.view not in views:raise ValueError('Неизвестный финансовый раздел')
        if query.view in ('accruals','daily-balance'):period(query.date_from,query.date_to,31)
        result={'mode':self.mode,'fetched_at':timestamp(),'complete':True,'warnings':[], 'metrics':[], 'columns':[],'rows':[], 'details':[],'source':[]}
        calls=0
        async def read(op,payload=None):
            nonlocal calls
            calls+=1
            if calls>150:raise ReadError('FINANCE_LIMIT','Достигнут лимит150чтений; результат неполный')
            if op.value[1] not in result['source']:result['source'].append(op.value[1])
            return await self.client.read(op,payload)
        def append(row):
            if len(result['rows'])>=50000:raise ReadError('FINANCE_LIMIT','Достигнут лимит50тысяч строк')
            result['rows'].append(row)
        def metric(label,value,unit=None):result['metrics'].append({'label':label,**value,**({'unit':unit} if unit else {})})
        try:
            async with asyncio.timeout(180):
                if query.view=='overview':
                    result['columns']=[column('period','Период'),column('label','Доход / расход'),column('amount','Сумма','money'),column('currency','Валюта')]
                    cursor=start; segments=[]
                    while cursor<=end:
                        last=min(end,cursor+timedelta(days=29))
                        raw=await read(Op.FINANCE_BALANCE,{'date_from':cursor.isoformat(),'date_to':last.isoformat()})
                        if not isinstance(raw.get('total'),dict) or not isinstance(raw.get('cashflows'),dict):raise ReadError('RESPONSE_CONTRACT','Неизвестная структура баланса')
                        summary=raw['total']; cash=raw['cashflows']; segments.append(summary)
                        if not isinstance(summary.get('payments'),list):raise ReadError('RESPONSE_CONTRACT','Выплаты должны быть массивом денежных значений')
                        for section,label in [('sales','Реализация'),('returns','Возвраты')]:
                            for field,suffix in [('amount','сумма'),('fee','комиссия')]:
                                value=money((cash.get(section) or {}).get(field))
                                append({'period':f'{cursor} — {last}','label':f'{label}: {suffix}',**value})
                        for service in cash.get('services',[]):
                            append({'period':f'{cursor} — {last}','label':service_label(service.get('name')),'service_code':text(service.get('name')),**money(service.get('amount'))})
                        cursor=last+timedelta(days=1)
                    for label,key in [('Начислено','accrued')]:
                        for value in grouped_money([money(s.get(key)) for s in segments]):metric(label,value)
                    for value in grouped_money([money(v) for s in segments for v in s.get('payments',[])]):metric('Выплачено',value)
                    metric('Баланс на начало',money(segments[0].get('opening_balance')))
                    metric('Баланс на конец',money(segments[-1].get('closing_balance')))
                elif query.view=='daily-balance':
                    result['columns']=[column('date','День учёта'),column('accrued','Начислено','money'),column('paid','Выплачено','money'),column('currency','Валюта')]
                    cursor=start
                    while cursor<=end:
                        raw=await read(Op.FINANCE_BALANCE,{'date_from':str(cursor),'date_to':str(cursor)})
                        summary=raw.get('total',{})
                        payments=summary.get('payments')
                        if not isinstance(payments,list):raise ReadError('RESPONSE_CONTRACT','Неизвестный дневной баланс')
                        accrued=money(summary.get('accrued')); paid=total([money(v) for v in payments])
                        if paid['currency'] not in (None,accrued['currency']):raise ReadError('RESPONSE_CONTRACT','Разные валюты дневного баланса')
                        append({'date':str(cursor),'accrued':accrued['amount'],'paid':paid['amount'],'currency':accrued['currency']})
                        cursor+=timedelta(days=1)
                    result['warnings'].append('День учёта в балансе не подтверждает дату банковского перевода. Сверьте отдельно с Seller и банком.')
                elif query.view=='payouts':
                    result['columns']=[column('begin','Начало периода'),column('end','Конец периода'),column('invoice','К выплате','money'),column('paid','Выплачено','money'),column('sales','Реализовано','money'),column('commission','Комиссия','money'),column('commission_ratio','Комиссия, % продаж','percent'),column('logistics','Логистика','money'),column('logistics_ratio','Логистика, % продаж','percent'),column('services','Услуги','money'),column('returns','Возвраты','money'),column('currency','Валюта')]
                    page=1; seen=set(); pages=None
                    while page<=500:
                        response=await read(Op.FINANCE_CASHFLOW,{'date':{'from':str(start)+'T00:00:00Z','to':str(end)+'T23:59:59Z'},'page':page,'page_size':100,'with_details':True})
                        raw=response.get('result',{})
                        flows=raw.get('cash_flows'); details=raw.get('details',[]); count=raw.get('page_count')
                        if isinstance(flows,dict):flows=[flows]
                        if isinstance(details,dict):details=[details]
                        if not isinstance(flows,list) or not isinstance(details,list) or type(count) is not int or count<0 or count>500 or (pages is not None and pages!=count):raise ReadError('RESPONSE_CONTRACT','Неизвестная/нестабильная структура финансовых периодов')
                        pages=count
                        for flow in flows:
                            p=flow.get('period',{}); identity=(str(p.get('id')),str(p.get('begin')),str(p.get('end')))
                            if identity in seen:raise ReadError('PAGINATION','Повтор финансового периода')
                            seen.add(identity)
                            detail=next((d for d in details if d.get('period',{}).get('id')==p.get('id') and p.get('id') is not None),{})
                            payments=detail.get('payments',[])
                            if isinstance(payments,dict):payments=[payments]
                            if not isinstance(payments,list):raise ReadError('RESPONSE_CONTRACT','Неизвестные выплаты')
                            currency=text(flow.get('currency_code'))
                            paid=total([{'amount':amount(x.get('payment')),'currency':text(x.get('currency_code'))} for x in payments])
                            if paid['currency'] not in (None,currency):raise ReadError('RESPONSE_CONTRACT','Разные валюты выплат и отчёта')
                            append({'begin':text(p.get('begin')),'end':text(p.get('end')),'invoice':amount(detail.get('invoice_transfer')),'paid':paid['amount'],
                                'sales':amount(flow.get('orders_amount')),'returns':amount(flow.get('returns_amount')),'commission':amount(flow.get('commission_amount')),
                                'logistics':amount(flow.get('item_delivery_and_return_amount')),'services':amount(flow.get('services_amount')),'currency':currency,
                                'commission_ratio':ratio(amount(flow.get('commission_amount')),amount(flow.get('orders_amount'))),
                                'logistics_ratio':ratio(amount(flow.get('item_delivery_and_return_amount')),amount(flow.get('orders_amount')))})
                        if page>=max(count,1):break
                        if not flows:raise ReadError('PAGINATION','Пустая промежуточная страница отчётов')
                        page+=1
                    for key,label in [('invoice','К выплате по отчётам'),('paid','Выплачено по отчётам')]:
                        for v in grouped_money([{'amount':r[key],'currency':r['currency']} for r in result['rows']]):metric(label,v)
                    result['warnings'].append('Период отчёта — не дата банковского перевода. Суммы к выплате и выплачено не складываются с выручкой. Проценты = абсолютный расход / продажи этого отчёта; при нулевых продажах процент не определён.')
                elif query.view=='types':
                    data=await read(Op.FINANCE_TYPES)
                    if not isinstance(data.get('accrual_types'),list):raise ReadError('RESPONSE_CONTRACT','Неизвестный справочник начислений')
                    result['columns']=[column('id','Код'),column('name','Начисление'),column('description','Описание')]
                    for row in data['accrual_types']:append({key:text(row.get(key)) for key in ('id','name','description')})
                elif query.view in ('accruals','postings'):
                    result['columns']=[column('date','Дата начисления'),column('posting','Отправление / услуга'),column('sku','SKU'),column('type','Начисление'),column('amount','Сумма','money'),column('currency','Валюта')]
                    if query.view=='postings':
                        data=await read(Op.FINANCE_POSTINGS,{'posting_numbers':query.postings})
                        if not isinstance(data.get('posting_accruals'),list):raise ReadError('RESPONSE_CONTRACT','Неизвестные начисления отправлений')
                        seen_postings=set()
                        for posting in data['posting_accruals']:
                            identity=text(posting.get('posting_number'))
                            if identity not in query.postings or identity in seen_postings:raise ReadError('RESPONSE_CONTRACT','Повтор или постороннее отправление в ответе')
                            seen_postings.add(identity)
                            for a in posting.get('accruals',[]):append({'date':text(a.get('accrual_date')),'posting':text(posting.get('posting_number')),'sku':text(a.get('sku')),'type':text(a.get('type_id')),**money(a.get('accrued'))})
                        if seen_postings!=set(query.postings):raise ReadError('INCOMPLETE_POSTINGS','Не все переданные отправления представлены в ответе. Пропуски не считаются нулевыми начислениями.')
                        for currency in dict.fromkeys(r['currency'] for r in result['rows']):
                            group=[r for r in result['rows'] if r['currency']==currency]
                            value=total([{'amount':r['amount'],'currency':r['currency']} for r in group])
                            metric('Начислено по переданным отправлениям',value)
                            with localcontext() as ctx:
                                ctx.prec=50
                                average=format((Decimal(value['amount'])/len({r['posting'] for r in group})).quantize(Decimal('0.01')),'f') if value['amount'] is not None else None
                            metric('Среднее начисление на представленное отправление',{'amount':average,'currency':value['currency']})
                        result['warnings'].append('Номера типов раскрыты в отдельном справочнике. Среднее относится только к переданным отправлениям и включает доходы и расходы; это не расход на все заказы и не прибыль.')
                    else:
                        types=await read(Op.FINANCE_TYPES)
                        names={str(r.get('id')):text(r.get('name')) for r in types.get('accrual_types',[])}
                        cursor=start
                        while cursor<=end:
                            token=''; tokens=set(); seen=set()
                            for _ in range(500):
                                raw=await read(Op.FINANCE_DAY,{'date':str(cursor),'last_id':token})
                                entries=raw.get('accruals'); nxt=raw.get('last_id')
                                if not isinstance(entries,list) or not isinstance(nxt,str):raise ReadError('RESPONSE_CONTRACT','Неизвестная страница начислений')
                                for row in entries:
                                    identity=text(row.get('accrual_id'))
                                    if not identity or identity in seen:raise ReadError('PAGINATION','Повтор/отсутствие ID начисления')
                                    seen.add(identity)
                                    append({'date':text(row.get('date')) or str(cursor),'posting':text(row.get('unit_number')),'sku':None,'type':'Итого по начислению',**money(row.get('total_amount'))})
                                    components=[]
                                    components.extend((None,f) for f in (row.get('container_fees') or {}).get('fees',[]))
                                    for item in (row.get('item_fees') or {}).get('fees',[]):components.extend((text(item.get('sku')),f) for f in item.get('fees',[]))
                                    if row.get('non_item_fee'):components.append((None,row['non_item_fee']))
                                    for product in (row.get('posting') or {}).get('products',[]):
                                        sku=text(product.get('sku'))
                                        commission=product.get('commission') or {}
                                        labels={'sale_amount':'Стоимость реализации','commission':'Комиссия','sale_commission':'Вознаграждение за продажу','bonus':'Баллы за скидки','coinvestment':'Софинансирование','sale_price':'Цена продажи за единицу','seller_price':'Цена продавца за единицу'}
                                        for key,label in labels.items():
                                            if key in commission:components.append((sku,{'label':label,'accrued':commission[key]}))
                                        delivery=product.get('delivery') or {}
                                        if 'total_accrued' in delivery:components.append((sku,{'label':'Итого доставки','accrued':delivery['total_accrued']}))
                                        components.extend((sku,f) for f in delivery.get('services',[]))
                                    for sku,fee in components:
                                        result['details'].append({'date':text(row.get('date')) or str(cursor),'posting':text(row.get('unit_number')),'sku':sku,'type':fee.get('label') or names.get(str(fee.get('type_id'))) or text(fee.get('type_id')),**money(fee.get('accrued'))})
                                    if len(result['details'])>50000:raise ReadError('FINANCE_LIMIT','Превышен лимит деталей начислений')
                                if not nxt:break
                                if not entries or nxt in tokens or nxt==token:raise ReadError('PAGINATION','Cursor начислений не продвигается')
                                tokens.add(nxt);token=nxt
                            else:raise ReadError('FINANCE_LIMIT','Превышен лимит страниц начислений')
                            cursor+=timedelta(days=1)
                        for value in grouped_money([{'amount':r['amount'],'currency':r['currency']} for r in result['rows']]):metric('Начислено по выбранным дням',value)
                        result['warnings'].append('Детализация содержит итоговые, составные суммы и цены за единицу; её строки нельзя складывать. Итог отправления не распределяется между SKU.')
                elif query.view in ('products','daily-products'):
                    selected=start
                    if query.view=='products':
                        data=await read(Op.FINANCE_REALIZATION,{'year':selected.year,'month':selected.month})
                        raw=data.get('result',{}); rows=raw.get('rows'); currency=text((raw.get('header') or {}).get('currency_sys_name'))
                    else:
                        data=await read(Op.FINANCE_REALIZATION_DAY,{'year':selected.year,'month':selected.month,'day':selected.day})
                        rows=data.get('rows'); currency=None
                    if not isinstance(rows,list):raise ReadError('RESPONSE_CONTRACT','Неизвестная таблица реализации')
                    result['columns']=[column('sku','SKU'),column('offer_id','Артикул'),column('name','Товар'),column('quantity','Продано, шт.','integer'),column('sales','Реализация','money'),column('fee','Вознаграждение','money'),column('net','Начислено за доставленные','money'),column('returned','Возвращено, шт.','integer'),column('returns','Реализация возвратов','money'),column('currency','Валюта')]
                    for row in rows:
                        item=row.get('item') or {}; delivered=row.get('delivery_commission') or {}; returned=row.get('return_commission') or {}
                        append({'sku':text(item.get('sku')),'offer_id':text(item.get('offer_id')),'name':text(item.get('name')),'quantity':text(delivered.get('quantity')),
                            'sales':amount(delivered.get('amount')),'fee':amount(delivered.get('standard_fee')),'net':amount(delivered.get('total')),'returned':text(returned.get('quantity')),'returns':amount(returned.get('amount')),'currency':currency})
                    for key,label in [('sales','Реализация доставленных'),('net','Начислено за доставленные')]:
                        for v in grouped_money([{'amount':r[key],'currency':r['currency']} for r in result['rows']]):metric(label,v)
                    result['warnings'].append(('Отчёт относится к месяцу начала периода.' if query.view=='products' else 'Отчёт относится к выбранному дню; валюта в этом контракте не предоставлена.')+' Доставленные товары и возвраты показаны отдельно; отмены и невыкупы не включены. Отсутствующий блок возвратов не подменяется нулём.')
                elif query.view=='report':
                    raw=(await read(Op.FINANCE_REPORT_INFO,{'code':query.report_code})).get('result',{})
                    status=raw.get('status')
                    if status not in ('waiting','processing','success','failed'):raise ReadError('RESPONSE_CONTRACT','Неизвестный статус отчёта')
                    file=report_file(raw.get('file')) if status=='success' else None
                    if status=='success' and not file:result['warnings'].append('Файл отчёта готов, но безопасная официальная ссылка не подтверждена. Откройте документ в Seller.')
                    result['report']={'code':query.report_code,'status':status,'file':file,'created_at':text(raw.get('created_at')),'expires_at':text(raw.get('expires_at'))}
                    if status=='failed':result['report']['error']=normalize_vendor_error(raw['error'] if isinstance(raw.get('error'),dict) else {'reason':raw.get('error','')})
        except (ReadError,TimeoutError) as exc:
            result['complete']=False
            result['warnings'].append(exc.message if isinstance(exc,ReadError) else 'Превышено время чтения финансов; результат неполный')
            result['error_code']=exc.code if isinstance(exc,ReadError) else 'TIMEOUT'
            if isinstance(exc,ReadError) and exc.vendor_error:result['error']=exc.vendor_error
        except (TypeError,AttributeError,KeyError):
            result['complete']=False;result['warnings'].append('Получена неподтверждённая структура финансов. Итоги не приняты.');result['error_code']='RESPONSE_CONTRACT'
        if not result['complete']:result['metrics']=[]
        if query.view=='overview':
            cashflows=chart_rows(result['rows']) if result['complete'] else []
            result['dashboard']={'cashflows':cashflows,'cashflow_totals':chart_totals(cashflows)}
        result['read_calls']=calls
        return result

    async def analytics(self,query,start,end):
        """All current analyses from the existing validated reads, one explicit load."""
        length=(end-start).days+1
        calls=0
        outer=self
        class BudgetClient:
            async def read(self,op,payload=None):
                nonlocal calls
                if calls>=150:raise ReadError('FINANCE_LIMIT','Достигнут общий лимит150чтений аналитики')
                calls+=1
                return await outer.client.read(op,payload)
        reader=FinancialReader(BudgetClient(),self.mode)
        requests=[('current',query.model_copy(update={'view':'overview'})),
            ('previous',FinanceQuery(view='overview',date_from=str(start-timedelta(days=length)),date_to=str(start-timedelta(days=1)))),
            ('expenses',query.model_copy(update={'view':'payouts'}))]
        if length<=31:requests.append(('daily',query.model_copy(update={'view':'daily-balance'})))
        sections={}
        try:
            async with asyncio.timeout(180):
                for name,request in requests:sections[name]=await reader.load(request)
        except TimeoutError:pass
        current=sections.get('current',{'mode':self.mode,'fetched_at':timestamp(),'metrics':[],'columns':[],'rows':[],'details':[],'source':[],'warnings':[],'complete':False})
        result={**current,'warnings':list(current['warnings']),'source':list(current['source'])}
        dashboard={'cashflows':current.get('dashboard',{}).get('cashflows',[]),'cashflow_totals':current.get('dashboard',{}).get('cashflow_totals',[]),'comparison':[],'expenses':[],'expense_totals':[],'daily':[],
            'previous_period':{'from':str(start-timedelta(days=length)),'to':str(start-timedelta(days=1))},'sections':[]}
        for name,_ in requests:
            section=sections.get(name)
            complete=bool(section and section['complete'])
            dashboard['sections'].append({'key':name,'complete':complete,'warnings':section['warnings'] if section else ['Чтение не завершено: превышено время загрузки']})
            if section:
                result['source'].extend(x for x in section['source'] if x not in result['source'])
            if not complete:result['warnings'].append({'current':'Текущий период','previous':'Предыдущий период','expenses':'Расходы','daily':'Дневная динамика'}[name]+': данные неполные; соответствующие итоги не рассчитаны.')
        previous=sections.get('previous')
        if current['complete'] and previous and previous['complete']:
            for metric in current['metrics']:
                candidates=[m for m in previous['metrics'] if m['label']==metric['label'] and m['currency']==metric['currency']]
                previous_amount=candidates[0]['amount'] if len(candidates)==1 else None
                delta=change=None
                if metric['amount'] is not None and previous_amount is not None and metric['currency']:
                    with localcontext() as ctx:
                        ctx.prec=50
                        delta=format(Decimal(metric['amount'])-Decimal(previous_amount),'f')
                        base=Decimal(previous_amount)
                        if base!=0:change=format(((Decimal(metric['amount'])-base)/abs(base)*100).quantize(Decimal('0.01')),'f')
                dashboard['comparison'].append({'label':metric['label'],'currency':metric['currency'],'previous':previous_amount,'delta':delta,'change':change})
        expenses=sections.get('expenses')
        if expenses and expenses['complete']:
            for currency in dict.fromkeys(row['currency'] for row in expenses['rows']):
                rows=[row for row in expenses['rows'] if row['currency']==currency]
                sales=total([{'amount':row['sales'],'currency':currency} for row in rows])
                for key,label in [('commission','Комиссия'),('logistics','Логистика'),('services','Услуги'),('returns','Возвраты')]:
                    value=total([{'amount':row[key],'currency':currency} for row in rows])
                    dashboard['expenses'].append({'label':label,**value,'sales':sales['amount'],'ratio':ratio(value['amount'],sales['amount'])})
            dashboard['expense_totals']=chart_totals(dashboard['expenses'],expense=True)
        daily=sections.get('daily')
        if daily and daily['complete']:dashboard['daily']=daily['rows']
        if length>31:dashboard['sections'].append({'key':'daily','complete':False,'warnings':['Дневная динамика доступна для периода до31 дня. Для длинного периода сравнение и расходы рассчитаны полностью.']})
        result['complete']=all(section['complete'] for section in dashboard['sections'] if section['key']!='daily' or length<=31)
        result['dashboard']=dashboard
        result['read_calls']=calls
        result['warnings'].append('Сравнение с предыдущим периодом той же длины. Изменение = разница / абсолютная сумма прошлого периода; при нуле процент не определён. Балансы и выплаты не являются выручкой или прибылью.')
        return result


def profit(query):
    values=[decimal(v.replace(',','.')) for v in (query.revenue,query.cost,query.expenses)]
    if any(v<0 for v in values):raise ValueError('Для сценария вводятся неотрицательные суммы')
    with localcontext() as ctx:
        ctx.prec=50
        p=values[0]-values[1]-values[2]
        return {'profit':format(p,'f'),'margin':format(p/values[0]*100,'.2f') if values[0]>0 else None,'source':'USER_SCENARIO','currency':'RUB'}
