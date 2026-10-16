"""Fixed-host advertising statistics; explicit asynchronous export, memory only."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
from decimal import Decimal
import re
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4
from pydantic import Field
from .models import StrictDTO
from .performance_read import PerformanceReader
from .ozon_read import ReadError, MAX_BYTES
from .vendor_errors import normalize_vendor_error
from .catalog import timestamp
from .storage import ROOT, confined

Operation = Literal['campaigns','daily','expense','media','product','sku','reports','external_reports','status','download','campaign_report','video','attribution','phrases','orders','products','all_orders','all_products','vendor_report','vendor_reports','vendor_status','vendor_tag']

class PerformanceStatisticsQuery(StrictDTO):
    operation: Operation
    date_from: str | None = None
    date_to: str | None = None
    campaign_ids: list[str] = Field(default_factory=list,max_length=10)
    uuid: str | None = None
    page: int = Field(default=1,ge=1,le=10000)
    page_size: int = Field(default=100,ge=1,le=100)
    group_by: Literal['NO_GROUP_BY','DATE','START_OF_WEEK','START_OF_MONTH'] = 'NO_GROUP_BY'
    confirmed: bool = False
    format: Literal['csv','json'] = 'csv'
    vendor_type: Literal['TRAFFIC_SOURCES','ORDERS'] = 'TRAFFIC_SOURCES'
    org_id: str | None = None
    reconcile: bool = False

GENERATE = {'campaign_report','video','attribution','phrases','orders','products','all_orders','all_products','vendor_report'}
LABELS = {'campaigns':'Кампании','daily':'По дням','expense':'Расходы','media':'Медийные кампании','product':'Кампании с оплатой за клик','sku':'Товары с оплатой за клик','reports':'Отчёты кабинета','external_reports':'Отчёты API','status':'Статус отчёта','download':'Скачать отчёт','campaign_report':'Отчёт по кампаниям','video':'Видеобаннеры','attribution':'Заказы на баннеры','phrases':'Поисковые запросы','orders':'Заказы — выбранные товары','products':'Товары — выбранные товары','all_orders':'Заказы — все товары','all_products':'Товары — все товары'}
METHODS = [{'id':k,'operation':k,'source':'performance','label':v,'kind':'generate' if k in GENERATE else 'read'} for k,v in LABELS.items()]
LABELS.update(vendor_report='Внешний трафик — создать отчёт',vendor_reports='Внешний трафик — отчёты',vendor_status='Внешний трафик — статус',vendor_tag='UTM-метка организации')
METHODS.extend({'id':k,'operation':k,'source':'performance','label':LABELS[k],'kind':'generate' if k in GENERATE else 'read'} for k in ('vendor_report','vendor_reports','vendor_status','vendor_tag'))
for entry in METHODS:
    op=entry['id'];fields=[]
    if op in ('status','download','vendor_status'):fields=[{'name':'uuid','label':'UUID отчёта','type':'text','required':True}]
    elif op=='vendor_tag':fields=[{'name':'org_id','label':'ID организации','type':'text','required':True}]
    elif op in ('reports','external_reports','vendor_reports'):fields=[{'name':'page','label':'Страница','type':'number','default':1},{'name':'page_size','label':'Строк на странице','type':'number','default':100}]
    elif op!='campaigns':
        fields=[{'name':'date_from','label':'С даты','type':'date','required':True},{'name':'date_to','label':'По дату','type':'date','required':True}]
        if op not in ('orders','products','all_orders','all_products','vendor_report'):fields.append({'name':'campaign_ids','label':'ID кампаний через запятую','type':'ids','required':op in ('campaign_report','video','attribution','phrases')})
        if op in ('campaign_report','video','attribution','phrases'):fields.append({'name':'group_by','label':'Группировка','type':'select','default':'NO_GROUP_BY','options':[{'value':v,'label':l} for v,l in [('NO_GROUP_BY','Без группировки'),('DATE','По дням'),('START_OF_WEEK','По неделям'),('START_OF_MONTH','По месяцам')]]})
    if op in GENERATE and op!='vendor_report':fields.append({'name':'format','label':'Формат отчёта','type':'select','default':'csv','options':[{'value':'csv','label':'CSV'},{'value':'json','label':'JSON'}]})
    if op=='vendor_report':fields.append({'name':'vendor_type','label':'Тип отчёта','type':'select','default':'TRAFFIC_SOURCES','options':[{'value':'TRAFFIC_SOURCES','label':'Источники трафика'},{'value':'ORDERS','label':'Заказы'}]})
    if op in ('status','vendor_status'):fields.append({'name':'reconcile','label':'Подтверждаю сверку этой завершённой попытки после перезапуска','type':'checkbox','default':False})
    entry['fields']=fields
PATHS = {'campaigns':('GET','/api/client/campaign'),'daily':('GET','/api/client/statistics/daily/json'),'expense':('GET','/api/client/statistics/expense/json'),'media':('GET','/api/client/statistics/campaign/media/json'),'product':('GET','/api/client/statistics/campaign/product/json'),'sku':('POST','/api/client/statistics/products/sku'),'reports':('GET','/api/client/statistics/list'),'external_reports':('GET','/api/client/statistics/externallist'),'campaign_report':('POST','/api/client/statistics'),'video':('POST','/api/client/statistics/video'),'attribution':('POST','/api/client/statistics/attribution'),'phrases':('POST','/api/client/statistics/phrases'),'orders':('POST','/api/client/statistic/orders/generate'),'products':('POST','/api/client/statistic/products/generate'),'all_orders':('GET','/api/client/statistics/all_sku_promo/orders/generate'),'all_products':('GET','/api/client/statistics/all_sku_promo/products/generate')}

def request(q):
    op=q.operation
    allowed={f['name'] for entry in METHODS if entry['id']==op for f in entry['fields']}|{'operation'}
    if op in GENERATE:allowed.add('confirmed')
    defaults=PerformanceStatisticsQuery(operation=op).model_dump()
    if any(k not in allowed and v!=defaults[k] for k,v in q.model_dump().items()):raise ValueError('Параметр неприменим к выбранному методу')
    if len(set(q.campaign_ids)) != len(q.campaign_ids) or any(not re.fullmatch(r'[1-9][0-9]{0,19}',i) for i in q.campaign_ids):raise ValueError('Некорректные ID кампаний')
    if op=='vendor_tag':
        if not q.org_id or not re.fullmatch(r'[1-9][0-9]{0,19}',q.org_id):raise ValueError('Укажите ID организации')
        return 'GET','/api/client/organisation/vendor_tag',{'orgId':q.org_id}
    if op in ('status','download','vendor_status'):
        if not q.uuid or str(UUID(q.uuid)) != q.uuid:raise ValueError('Укажите UUID отчёта')
        if op=='vendor_status':return 'GET','/api/client/vendors/statistics/'+q.uuid,{'vendor':True}
        return 'GET',('/api/client/statistics/'+q.uuid if op=='status' else '/api/client/statistics/report'),({'UUID':q.uuid} if op=='download' else {})
    if op=='vendor_reports':return 'GET','/api/client/vendors/statistics/list',{'page':q.page,'pageSize':q.page_size}
    method,path=PATHS.get(op,('POST','/api/client/vendors/statistics'))
    if op in GENERATE and q.format=='json':path+='/json'
    if op=='campaigns':return method,path,{}
    if op in ('reports','external_reports'):return method,path,{'page':q.page,'pageSize':q.page_size}
    if not q.date_from or not q.date_to:raise ValueError('Укажите начало и конец периода')
    start,end=date.fromisoformat(q.date_from),date.fromisoformat(q.date_to)
    if op=='vendor_report':
        month=(start.year*12+start.month-1)+3
        cap=date(month//12,month%12+1,1)
        if start<date(2022,1,1) or end<start or (end.year,end.month,end.day)>(cap.year,cap.month,start.day):raise ValueError('Внешний трафик: период до трёх месяцев, с 01.01.2022')
        return method,path,{'dateFrom':q.date_from,'dateTo':q.date_to,'type':q.vendor_type}
    if start.isoformat()!=q.date_from or end.isoformat()!=q.date_to or not 0<=(end-start).days<62:raise ValueError('Период должен содержать от 1 до 62 дней')
    if op=='sku' and start<datetime.now(timezone.utc).date()-timedelta(days=1):raise ValueError('Статистика товаров доступна не раньше предыдущего дня')
    if op=='phrases' and start<date(2025,2,1):raise ValueError('Поисковые запросы доступны с 01.02.2025')
    if op in ('campaign_report','video','attribution','phrases'):
        if not q.campaign_ids:raise ValueError('Выберите кампании')
        return method,path,{'campaigns':q.campaign_ids,'dateFrom':q.date_from,'dateTo':q.date_to,'groupBy':q.group_by}
    if op in ('orders','products','all_orders','all_products'):
        prefix='timeBounds.' if op.startswith('all_') else ''
        return method,path,{prefix+'from':q.date_from+'T00:00:00Z',prefix+'to':q.date_to+'T23:59:59Z'}
    return method,path,{'dateFrom':q.date_from,'dateTo':q.date_to,**({'campaignIds':q.campaign_ids} if q.campaign_ids else {})}

class PerformanceStatisticsReader(PerformanceReader):
    def __init__(self,mode,*,marker_path=None,root=ROOT,history_store=None,**kwargs):
        super().__init__(mode,**kwargs)
        self.attempts={}
        self.active_uuid=None
        self.root=root;self.marker_path=Path(marker_path) if marker_path else root/'.cache/performance-statistics-attempt.json'
        self.recovered=False
        self.history_store=None
        self._load_marker(mode,root)
        if history_store is not None:self.set_history_store(history_store)

    def _save_report_event(self,event):
        if self.history_store:self.history_store.upsert(event)

    def set_history_store(self,history_store):
        self.history_store=history_store
        unresolved=[event for event in self.history() if event.get('status') in ('UNKNOWN_REPORT_RESULT','PENDING')]
        if unresolved:
            self.active_uuid=unresolved[-1].get('report_uuid','UNKNOWN_REPORT_RESULT')
            self.recovered=True

    def history(self):
        return self.history_store.entries() if self.history_store else []

    def _report_outcome(self,data,operation):
        if isinstance(data,dict) and data.get('state') in ('ERROR','TIMEOUT','CANCEL'):
            raw_error=data.get('error',data.get('message',''))
            error=normalize_vendor_error(raw_error if isinstance(raw_error,dict) else {'reason':raw_error},secrets=(self.token,))
            data={**data,'error':error}
        if isinstance(data,dict) and data.get('UUID'):
            event=next((item for item in self.history() if item.get('report_uuid')==data['UUID']),None)
            if event:
                event.update(status=data.get('state','PENDING'),operation=event.get('operation',operation))
                if isinstance(data.get('error'),dict):event['error']=data['error']
                self._save_report_event(event)
        return data

    def _load_marker(self,mode,root):
        if mode!='MOCK':
            marker=confined(self.marker_path,root)
            if marker.exists():
                try:
                    if marker.stat().st_size>512:raise ValueError()
                    value=json.loads(marker.read_text(encoding='utf-8'))
                    if set(value)!={'UUID','status'} or str(UUID(value['UUID']))!=value['UUID'] or value['status'] not in ('UNKNOWN_REPORT_RESULT','PENDING','RESOLVED'):raise ValueError()
                    if value['status']!='RESOLVED':self.active_uuid=value['UUID'] if value['status']=='PENDING' else 'UNKNOWN_REPORT_RESULT';self.recovered=True
                except (ValueError,TypeError,OSError):raise ReadError('REPORT_STORAGE','Маркер отчёта повреждён; генерация запрещена',409) from None

    def marker(self,report,status):
        if self.mode=='MOCK':return
        destination=confined(self.marker_path,self.root);destination.parent.mkdir(parents=True,exist_ok=True)
        temporary=confined(destination.with_suffix('.'+uuid4().hex+'.tmp'),self.root)
        try:
            with temporary.open('x',encoding='utf-8') as stream:
                json.dump({'UUID':report,'status':status},stream);stream.flush();os.fsync(stream.fileno())
            confined(destination,self.root);os.replace(temporary,destination)
        except OSError:raise ReadError('REPORT_STORAGE','Не удалось сохранить маркер отчёта; отправка запрещена',409) from None

    async def authorize(self):
        if self.token and time.monotonic()<self.expires:return
        client,secret=self.credentials()
        raw=await self.fetch('POST','/api/client/token',json={'client_id':client,'client_secret':secret,'grant_type':'client_credentials'})
        try:
            data=json.loads(raw); token=data['access_token'];expiry=data['expires_in']
            if not isinstance(token,str) or not re.fullmatch(r'[!-~]{1,8192}',token) or type(expiry) is not int or not 1<=expiry<=86400 or data.get('token_type','Bearer').lower()!='bearer':raise ValueError()
            self.token=token;self.expires=time.monotonic()+max(0,expiry-30)
        except (ValueError,KeyError,TypeError):raise ReadError('PERFORMANCE_CONTRACT','Неизвестный ответ авторизации Performance') from None

    async def load(self,q):
        method,path,params=request(q)
        generating=q.operation in GENERATE
        if generating and not q.confirmed:raise ValueError('Подтвердите создание конкретного отчёта')
        warnings=[]
        if self.mode=='MOCK':
            if generating:
                key=hashlib.sha256(json.dumps([path,params],sort_keys=True).encode()).hexdigest()
                if self.active_uuid or key in self.attempts:raise ReadError('REPORT_RECONCILE','Сначала сверьте предыдущий запрос; повторная отправка запрещена.',409)
                self.active_uuid='00000000-0000-4000-8000-000000000001';self.attempts[key]=self.active_uuid
                self._save_report_event({'id':str(uuid4()),'operation':q.operation,'mode':self.mode,'status':'PENDING','report_uuid':self.active_uuid,'created_at':timestamp()})
            data={'UUID':'00000000-0000-4000-8000-000000000001'} if generating else {'rows':[{'campaignId':'101','date':q.date_from,'views':'100','clicks':'5','expense':'12.50'}]}
            if q.operation in ('status','vendor_status'):
                data={'UUID':q.uuid,'state':'OK','kind':'STATS'}
                if q.uuid==self.active_uuid:self.active_uuid=None
            if q.operation=='campaigns':data={'list':[{'id':'101','title':'Синтетическая кампания','state':'CAMPAIGN_STATE_RUNNING'}]}
            if q.operation in ('reports','external_reports','vendor_reports'):data={'items':[],'total':'0'}
            if q.operation=='vendor_tag':data={'tag':'vendor_org_synthetic'}
            if q.operation=='download':data={'format':'csv','base64':base64.b64encode(b'campaignId;expense\n101;12.50\n').decode()}
            data=self._report_outcome(data,q.operation)
            return {'mode':self.mode,'source':'performance','operation':q.operation,'data':data,'complete':not generating and q.operation not in ('campaigns','reports','external_reports','vendor_reports'),'warnings':['Синтетические данные. Запросов Performance API нет.']}
        async with self.lock:
            await self.authorize()
            key=hashlib.sha256(json.dumps([path,params],sort_keys=True).encode()).hexdigest()
            if generating:
                if self.active_uuid or key in self.attempts:raise ReadError('REPORT_RECONCILE','Сначала сверьте предыдущий запрос в списке отчётов; повторная отправка запрещена.',409)
                self.attempts[key]='UNKNOWN_REPORT_RESULT'
                self.marker(str(uuid4()),'UNKNOWN_REPORT_RESULT')
                self.active_uuid='UNKNOWN_REPORT_RESULT'
                event={'id':str(uuid4()),'operation':q.operation,'mode':self.mode,'status':'UNKNOWN_REPORT_RESULT','created_at':timestamp()}
                self._save_report_event(event)
            try:
                raw=await self.fetch(method,path,headers={'Authorization':'Bearer '+self.token},**({'json':params} if method=='POST' else {'params':params}))
                if raw.lstrip().startswith(b'{'):
                    envelope=json.loads(raw)
                    if isinstance(envelope,dict) and (('code' in envelope and 'message' in envelope) or (envelope.get('error') and q.operation not in ('status','vendor_status'))):
                        error=normalize_vendor_error(envelope.get('error') if isinstance(envelope.get('error'),dict) else envelope,secrets=(self.token,))
                        raise ReadError('PERFORMANCE_REJECTED',error['message'],vendor_error=error)
                data=self.decode(raw,q.operation)
                if q.operation in ('status','vendor_status') and data['UUID']!=q.uuid:raise ValueError('UUID ответа не совпал')
                if q.operation in ('reports','external_reports','vendor_reports') and len(data['items'])>q.page_size:raise ValueError('Размер страницы превышен')
                if generating:
                    report=data.get('UUID') if isinstance(data,dict) else None
                    if not isinstance(report,str) or str(UUID(report))!=report:raise ValueError()
                    self.active_uuid=report;self.attempts[key]=report
                    self.marker(report,'PENDING')
                    event.update(status='PENDING',report_uuid=report);self._save_report_event(event)
                data=self._report_outcome(data,q.operation)
                if q.operation in ('status','vendor_status') and isinstance(data,dict) and data.get('UUID')==q.uuid and data.get('state') in ('OK','ERROR','TIMEOUT','CANCEL'):
                    matches=self.active_uuid==q.uuid
                    explicit=q.reconcile and (matches or self.active_uuid=='UNKNOWN_REPORT_RESULT')
                    if (matches and not self.recovered) or explicit:
                        self.marker(q.uuid,'RESOLVED');self.active_uuid=None;self.recovered=False
            except (ReadError,ValueError,TypeError,zipfile.BadZipFile,RuntimeError,UnicodeError) as exc:
                if generating:
                    if isinstance(exc,ReadError) and exc.vendor_error:event['error']=exc.vendor_error
                    self._save_report_event(event)
                    raise ReadError('UNKNOWN_REPORT_RESULT','Результат создания отчёта неизвестен. Сверьте список отчётов API; автоматически запрос не повторяется.',409,vendor_error=getattr(exc,'vendor_error',None)) from None
                if isinstance(exc,ReadError):
                    if exc.code=='PERFORMANCE_ACCESS':self.token=None
                    raise
                raise ReadError('PERFORMANCE_CONTRACT','Ответ статистики не соответствует контракту') from None
        if q.operation in ('reports','external_reports','vendor_reports'):
            warnings.append('Показана одна страница списка; продолжайте по страницам.')
            ids=[i.get('meta',{}).get('UUID') for i in data['items'] if isinstance(i.get('meta'),dict)]
            ids=[i for i in ids if isinstance(i,str)]
            if len(ids)!=len(set(ids)):warnings.append('API вернул повторяющиеся UUID на странице; полнота не подтверждена.')
        if q.operation=='campaigns':warnings.append('Полнота списка кампаний не подтверждена; показан ответ API.')
        return {'mode':self.mode,'source':'performance','operation':q.operation,'data':data,'complete':not generating and q.operation not in ('campaigns','reports','external_reports','vendor_reports'),'warnings':warnings}

    @staticmethod
    def decode(raw,operation):
        if operation=='download':
            if raw.startswith(b'PK'):
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    items=archive.infolist()
                    if len(items)>10 or sum(i.file_size for i in items)>MAX_BYTES or any('/' in i.filename or '\\' in i.filename or not re.fullmatch(r'[0-9]+\.csv',i.filename) or i.flag_bits & 1 for i in items):raise ValueError('Небезопасный архив')
                    for item in items:archive.read(item)
                kind='zip'
            elif raw.lstrip().startswith((b'{',b'[')):
                def invalid_constant(value):raise ValueError('Нечисловая константа JSON')
                json.loads(raw,parse_constant=invalid_constant);kind='json'
            else:
                text=raw.decode('utf-8-sig')
                if text.lstrip().startswith(('{','<','[')):raise ValueError('Не CSV')
                kind='csv'
            return {'format':kind,'base64':base64.b64encode(raw).decode()}
        def integer(value):
            parsed=int(value)
            return value if abs(parsed)>9007199254740991 else parsed
        def invalid(value):raise ValueError('Нечисловая константа JSON')
        data=json.loads(raw,parse_float=lambda v:format(Decimal(v),'f'),parse_int=integer,parse_constant=invalid)
        if not isinstance(data,(dict,list)):raise ValueError()
        if isinstance(data,dict) and (('error' in data and data.get('error') and operation not in ('status','vendor_status')) or ('code' in data and 'message' in data)):raise ValueError('Ответ ошибки')
        if operation in GENERATE:
            if not isinstance(data,dict) or not isinstance(data.get('UUID'),str) or str(UUID(data['UUID']))!=data['UUID']:raise ValueError()
        elif operation in ('status','vendor_status'):
            if not isinstance(data,dict) or not isinstance(data.get('UUID'),str) or str(UUID(data['UUID']))!=data['UUID'] or data.get('state') not in ('NOT_STARTED','IN_PROGRESS','OK','ERROR','TIMEOUT','CANCEL'):raise ValueError()
        elif operation in ('reports','external_reports','vendor_reports'):
            if not isinstance(data,dict) or not isinstance(data.get('items'),list) or not all(isinstance(i,dict) for i in data['items']) or not re.fullmatch(r'[0-9]+',str(data.get('total',''))):raise ValueError()
        elif operation=='campaigns':
            if not isinstance(data,dict) or not isinstance(data.get('list'),list) or not all(isinstance(i,dict) for i in data['list']):raise ValueError()
        elif operation=='sku':
            if not isinstance(data,dict) or not isinstance(data.get('rows'),list) or not all(isinstance(i,dict) for i in data['rows']):raise ValueError()
        elif operation=='vendor_tag':
            if not isinstance(data,dict) or not isinstance(data.get('tag'),str) or not data['tag']:raise ValueError()
        elif isinstance(data,dict) and (not data or set(data)=={'contentType'}):raise ValueError('Отсутствуют данные отчёта')
        return data
