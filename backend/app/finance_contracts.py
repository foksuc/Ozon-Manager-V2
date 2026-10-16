"""Closed financial request contracts derived from the 2026-10-03 snapshot."""
from datetime import date, timedelta
import re


def day(value):
    if not isinstance(value,str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",value):
        raise ValueError("Нужна дата YYYY-MM-DD")
    return date.fromisoformat(value)


def period(start,end,maximum=366):
    first,last=day(start),day(end)
    if last < first or (last-first).days >= maximum:
        raise ValueError(f"Период должен содержать от1 до{maximum} дней")
    return first,last


def validate_finance(name,body):
    if name == "FINANCE_TYPES":
        if body is not None: raise ValueError("Справочник не принимает тело")
        return
    allowed={"FINANCE_BALANCE":{"date_from","date_to"},"FINANCE_CASHFLOW":{"date","page","page_size","with_details"},
        "FINANCE_DAY":{"date","last_id"},"FINANCE_POSTINGS":{"posting_numbers"},
        "FINANCE_REALIZATION":{"month","year"},"FINANCE_REALIZATION_DAY":{"day","month","year"},"FINANCE_REPORT_INFO":{"code"}}
    if type(body) is not dict or set(body)!=allowed[name]: raise ValueError("Неизвестные/отсутствующие поля")
    if name == "FINANCE_BALANCE": period(body['date_from'],body['date_to'],30)
    elif name == "FINANCE_CASHFLOW":
        d=body['date']
        if type(d) is not dict or set(d)!={'from','to'}: raise ValueError("Некорректный период")
        from datetime import datetime
        dates=[datetime.fromisoformat(d[k].replace('Z','+00:00')) for k in ('from','to')]
        if any(v.tzinfo is None for v in dates) or dates[0]>dates[1] or (dates[1]-dates[0]).days>366:raise ValueError("Некорректные даты")
        if type(body['page']) is not int or not 1<=body['page']<=500 or type(body['page_size']) is not int or not 1<=body['page_size']<=100 or type(body['with_details']) is not bool: raise ValueError("Некорректная страница")
    elif name == "FINANCE_DAY":
        if day(body['date']) < date(2022,1,1) or not isinstance(body['last_id'],str) or len(body['last_id'])>8192: raise ValueError("Некорректный cursor/date")
    elif name == "FINANCE_POSTINGS":
        ids=body['posting_numbers']
        if not isinstance(ids,list) or not 1<=len(ids)<=50 or len(set(ids))!=len(ids) or any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',x) for x in ids): raise ValueError("Некорректные отправления")
    elif name in ('FINANCE_REALIZATION','FINANCE_REALIZATION_DAY'):
        if any(type(x) is not int for x in body.values()): raise ValueError("Ожидается целая дата")
        selected=date(body['year'],body['month'],body.get('day',1))
        if name=='FINANCE_REALIZATION' and selected<date(2023,8,1): raise ValueError("Отчёт доступен с08.2023")
        if name=='FINANCE_REALIZATION_DAY' and not date.today()-timedelta(days=31)<=selected<=date.today(): raise ValueError("Доступны последние32дня")
    elif name=='FINANCE_REPORT_INFO':
        if not isinstance(body['code'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,512}',body['code']):raise ValueError("Некорректный код отчёта")


def report_request(month):
    if not isinstance(month,str) or not re.fullmatch(r'\d{4}-\d{2}',month): raise ValueError("Нужен месяц YYYY-MM")
    day(month+'-01')
    return {'date':month,'language':'RU'}
