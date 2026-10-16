"""Read-only catalogue pages and 100-SKU analytics batches; rows stay in RAM."""
import asyncio
from copy import deepcopy
from pydantic import Field
from .models import StrictDTO
from .ozon_read import ReadOperation as Op, ReadError
from .seller_extensions import validate_extension_request


def exact_id(value):
    if isinstance(value,bool) or not isinstance(value,(str,int)):
        raise ReadError('RESPONSE_CONTRACT','Неподтверждённый идентификатор товара')
    raw=str(value)
    if not raw.isascii() or not raw.isdigit() or not 0<int(raw)<=2**63-1:
        raise ReadError('RESPONSE_CONTRACT','Неподтверждённый идентификатор товара')
    return str(int(raw))


class CatalogPageQuery(StrictDTO):
    last_id:str=Field(default='',max_length=4096)


class StocksBatchQuery(StrictDTO):
    skus:list[str]=Field(min_length=1,max_length=50000)
    filters:dict=Field(default_factory=dict)


class AnalyticsBatch:
    def __init__(self,service,reader):
        self.service,self.reader=service,reader

    def check_binding(self,binding):
        if self.service.binding()!=binding:
            raise ReadError('ACCOUNT_CHANGED','Магазин изменился во время загрузки',409)

    async def catalogue_page(self,last_id=''):
        binding=self.service.binding()
        if self.service.accounts.mode=='MOCK':
            return {'rows':[{'sku':str(30000+i),'name':name,'offer_id':f'SYNTHETIC-{i}'} for i,name in enumerate(['Термокружка MOCK','Лампа MOCK','Блокнот MOCK','Сумка MOCK'],1)] if not last_id else [],'complete':True,'next_cursor':None,'warnings':[]}
        raw=await self.reader(Op.PRODUCT_LIST,{'filter':{'visibility':'ALL'},'limit':1000,'last_id':last_id})
        self.check_binding(binding)
        page=raw.get('result')
        if not isinstance(page,dict) or not isinstance(page.get('items'),list):
            raise ReadError('RESPONSE_CONTRACT','Не подтверждён список товаров')
        products=page['items']
        if len(products)>1000:raise ReadError('RESPONSE_CONTRACT','Список товаров превышает размер страницы')
        ids=[exact_id(item.get('product_id')) for item in products if isinstance(item,dict)]
        if len(ids)!=len(products) or len(set(ids))!=len(ids):
            raise ReadError('RESPONSE_CONTRACT','Список товаров содержит некорректные или повторные ID')
        cursor=page.get('last_id')
        if cursor is not None and not isinstance(cursor,str):raise ReadError('RESPONSE_CONTRACT','Не подтверждён указатель страницы')
        total=page.get('total_items',page.get('total'))
        if total is not None and (type(total) is not int or total<0):raise ReadError('RESPONSE_CONTRACT','Не подтверждено количество товаров')
        if page.get('total') is not None and page.get('total_items') is not None and page['total']!=page['total_items']:
            raise ReadError('RESPONSE_CONTRACT','Количество товаров в total и total_items различается')
        if total is not None and total<len(products):raise ReadError('RESPONSE_CONTRACT','Количество товаров меньше размера страницы')
        more=bool(products) and bool(cursor) and (len(products)==1000 or isinstance(total,int) and total>len(products))
        if more and cursor==last_id:raise ReadError('RESPONSE_CONTRACT','Указатель каталога не продвинулся')
        rows=[];warnings=[];complete=True
        if (len(products)==1000 and not cursor) or (total is not None and total>len(products) and products and not cursor):
            complete=False;warnings.append('Ozon не вернул указатель следующей страницы; каталог неполный.')
        if products:
            response=await self.reader(Op.PRODUCT_INFO,{'product_id':ids})
            self.check_binding(binding)
            cards=response.get('items')
            if not isinstance(cards,list):raise ReadError('RESPONSE_CONTRACT','Не подтверждены карточки товаров')
            seen=set();sku_seen=set()
            for card in cards:
                if not isinstance(card,dict):raise ReadError('RESPONSE_CONTRACT','Не подтверждена карточка товара')
                pid=exact_id(card.get('id',card.get('product_id')))
                if pid not in ids or pid in seen:raise ReadError('RESPONSE_CONTRACT','Ответ карточек не соответствует странице')
                seen.add(pid)
                candidates=[card.get('sku')] if card.get('sku') is not None else []
                sources=card.get('sources',[])
                if isinstance(sources,list):candidates.extend(source['sku'] for source in sources if isinstance(source,dict) and source.get('sku') is not None)
                skus=list(dict.fromkeys(exact_id(value) for value in candidates))
                if not skus:
                    complete=False;warnings.append('Часть карточек ещё не получила SKU Ozon и недоступна для этого отчёта.')
                for sku in skus:
                    if sku in sku_seen:raise ReadError('RESPONSE_CONTRACT','SKU повторяется в разных карточках страницы')
                    sku_seen.add(sku)
                    rows.append({'sku':sku,'name':card.get('name') if isinstance(card.get('name'),str) else 'Название не предоставлено','offer_id':card.get('offer_id') if isinstance(card.get('offer_id'),str) else ''})
            if seen!=set(ids):
                complete=False;warnings.append('Ozon не вернул часть карточек; каталог неполный.')
        return {'rows':rows,'complete':complete,'next_cursor':cursor if more else None,'warnings':list(dict.fromkeys(warnings))}

    async def stocks(self,skus,filters):
        # Validate every selector/filter before the first upstream request.
        ids=[exact_id(value) for value in skus]
        if len(ids)!=len(set(ids)):raise ValueError('Повторные SKU в выборке')
        if 'skus' in filters:raise ValueError('SKU задаются отдельным списком')
        for start in range(0,len(ids),100):validate_extension_request('ANALYTICS_STOCKS',{**filters,'skus':ids[start:start+100]})
        binding=self.service.binding();rows=[];warnings=[];processed=0;budget_exhausted=False
        try:
            async with asyncio.timeout(120):
                for start in range(0,len(ids),100):
                    if start>=10000:
                        budget_exhausted=True;break
                    self.check_binding(binding)
                    selected=ids[start:start+100]
                    response=await self.service.read('ANALYTICS_STOCKS',{**deepcopy(filters),'skus':selected})
                    self.check_binding(binding)
                    if any(exact_id(row.get('sku')) not in selected for row in response['rows']):
                        raise ReadError('RESPONSE_CONTRACT','Ответ аналитики содержит SKU вне выбранной части')
                    rows.extend(response['rows']);warnings.extend(response['warnings']);processed+=len(ids[start:start+100])
                    if not response['complete']:
                        warnings.append('Ozon вернул неполную часть выбранных товаров.');break
        except (ReadError,TimeoutError) as error:
            if isinstance(error,ReadError) and error.code=='ACCOUNT_CHANGED':raise
            warnings.append(error.message if isinstance(error,ReadError) else 'Истекло время загрузки; подтверждённая часть сохранена.')
        remaining=ids[processed:]
        # Only budget continuation is automatic. A network/schema error is
        # visible and never silently retried in the next request.
        return {'operation':'ANALYTICS_STOCKS','data':{},'rows':rows,'complete':not remaining and not warnings,'next_request':None,'warnings':list(dict.fromkeys(warnings)),'processed_skus':processed,'remaining_skus':remaining,'continue_automatically':budget_exhausted}


def install_analytics_batch(app,service,reader):
    batch=AnalyticsBatch(service,reader)
    app.state.analytics_batch=batch
    @app.post('/api/analytics/catalog')
    async def catalogue(query:CatalogPageQuery):
        return await batch.catalogue_page(query.last_id)
    @app.post('/api/analytics/stocks-batch')
    async def stocks(query:StocksBatchQuery):
        return await batch.stocks(query.skus,query.filters)
