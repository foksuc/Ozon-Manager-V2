"""Separate Performance credentials. Fixed OAuth/expense endpoints, memory only."""
import asyncio
import csv
import io
import json
import re
import time
import httpx
from .storage import ROOT, confined
from .finance_contracts import period
from .ozon_read import ReadError, MAX_BYTES, rejection_diagnostic


class PerformanceReader:
    def __init__(self,mode,*,transport=None):
        self.mode=mode;self.token=None;self.expires=0;self.lock=asyncio.Lock()
        self.http=httpx.AsyncClient(base_url='https://api-performance.ozon.ru',follow_redirects=False,trust_env=False,timeout=20,transport=transport)
    async def close(self):await self.http.aclose()
    def credentials(self):
        try:
            p=confined(ROOT/'backend/.performance.env')
            if not p.is_file() or p.stat().st_size>16384:raise ValueError()
            values={}
            for line in p.read_text(encoding='utf-8-sig').splitlines():
                if not line.strip() or line.lstrip().startswith('#'):continue
                k,v=line.split('=',1); k,v=k.strip(),v.strip()
                if k not in ('OZON_PERF_CLIENT_ID','OZON_PERF_CLIENT_SECRET') or k in values or not re.fullmatch(r'[!-~]{1,4096}',v):raise ValueError()
                values[k]=v
            return values['OZON_PERF_CLIENT_ID'],values['OZON_PERF_CLIENT_SECRET']
        except (OSError,UnicodeError,ValueError,KeyError):
            raise ReadError('PERFORMANCE_NOT_CONFIGURED','Для рекламы заполните локально backend/.performance.env по пустому образцу. Seller API-ключ для Performance не подходит.',503) from None
    async def fetch(self,method,path,**kwargs):
        try:
            async with self.http.stream(method,path,**kwargs) as response:
                if not 200<=response.status_code<300:
                    secrets=[self.token] if self.token else []
                    for key,value in (kwargs.get('headers') or {}).items():
                        if key.lower()=='authorization':secrets.extend((value,str(value).removeprefix('Bearer ')))
                    for key,value in (kwargs.get('json') or {}).items():
                        if key in ('client_id','client_secret'):secrets.append(value)
                    diagnostic=await rejection_diagnostic(response,secrets=secrets)
                    vendor_error=diagnostic.pop('vendor_error',None)
                    raise ReadError('PERFORMANCE_ACCESS' if response.status_code in (401,403) else 'PERFORMANCE_READ','Performance API отклонил чтение. Проверьте отдельное подключение, права и период.',403 if response.status_code in (401,403) else 502,diagnostic=diagnostic,vendor_error=vendor_error)
                chunks=[];size=0
                async for chunk in response.aiter_bytes():
                    size+=len(chunk)
                    if size>MAX_BYTES:raise ReadError('RESPONSE_LIMIT','Рекламный отчёт слишком большой; сузьте период')
                    chunks.append(chunk)
                return b''.join(chunks)
        except httpx.RequestError:raise ReadError('PERFORMANCE_NETWORK','Не удалось прочитать Performance API. Повторите загрузку позже.',503) from None
    async def expenses(self,start,end):
        period(start,end,366)
        if self.mode=='MOCK':return {'mode':'MOCK','complete':True,'columns':['Кампания','Дата','Расход'],'rows':[['Синтетическая реклама',start,'123.45']],'warnings':['Синтетические данные. Запросов Performance API нет.']}
        async with self.lock:
            if not self.token or time.monotonic()>=self.expires:
                client,secret=self.credentials()
                raw=await self.fetch('POST','/api/client/token',json={'client_id':client,'client_secret':secret,'grant_type':'client_credentials'})
                try:
                    response=json.loads(raw);token=response['access_token'];expiry=response['expires_in']
                    if not isinstance(token,str) or not re.fullmatch(r'[!-~]{1,8192}',token) or type(expiry) is not int or not 1<=expiry<=86400 or response.get('token_type','Bearer').lower()!='bearer':raise ValueError()
                    self.token=token;self.expires=time.monotonic()+max(0,expiry-30)
                except (ValueError,KeyError,TypeError):raise ReadError('PERFORMANCE_CONTRACT','Неизвестный ответ авторизации Performance') from None
            try:
                raw=await self.fetch('GET','/api/client/statistics/expense',params={'dateFrom':start,'dateTo':end},headers={'Authorization':'Bearer '+self.token})
            except ReadError as exc:
                if exc.code=='PERFORMANCE_ACCESS':self.token=None
                raise
        try:
            contents=raw.decode('utf-8-sig')
            if not contents.strip():return {'mode':self.mode,'complete':True,'columns':[],'rows':[],'warnings':[]}
            if contents.lstrip().startswith(('{','[')):raise ValueError()
            delimiter=';' if contents.splitlines()[0].count(';')>contents.splitlines()[0].count(',') else ','
            parsed=list(csv.reader(io.StringIO(contents),delimiter=delimiter))
            columns=parsed[0]
            if not 1<=len(columns)<=50 or len(parsed)>50001 or any(len(r)!=len(columns) for r in parsed[1:]):raise ValueError()
            return {'mode':self.mode,'complete':True,'columns':columns,'rows':parsed[1:],'warnings':['Поля CSV показаны как вернул Performance. Семантика расходов не угадывается; банковских переводов здесь нет.']}
        except (UnicodeError,ValueError,csv.Error):raise ReadError('PERFORMANCE_CONTRACT','Ответ рекламы не соответствует CSV-контракту. Данные не подменялись.') from None
