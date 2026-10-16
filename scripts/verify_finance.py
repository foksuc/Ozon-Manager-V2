"""Live financial acceptance. Only counts/statuses persist, never money or entities."""
import asyncio
import json
import re
import sys
from datetime import datetime,date,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).absolute().parent.parent
sys.path.insert(0,str(ROOT/'backend'))
from app.connection import load_connection
from app.ozon_read import ReadClient,ReadError
from app.finance import FinancialReader,FinanceQuery
from app.performance_read import PerformanceReader
from app.storage import confined


async def verify():
    settings=load_connection('LIVE_READ')
    report={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'checks':{},'shop_mutations':0,'report_jobs_created':0,'persisted_real_rows':0}
    if settings.credentials is None:return {**report,'status':'NOT_CONFIGURED'}
    client=ReadClient(settings.credentials);reader=FinancialReader(client)
    today=date.today();yesterday=today-timedelta(days=1);month_end=today.replace(day=1)-timedelta(days=1);month_start=month_end.replace(day=1)
    async def check(view,start,end,**kwargs):
        result=await reader.load(FinanceQuery(view=view,date_from=str(start),date_to=str(end),**kwargs))
        report['checks'][view]={'complete':result['complete'],'rows':len(result['rows']),'details':len(result['details']),
            'metric_count':len(result['metrics']),'read_calls':result['read_calls'],'error_code':result.get('error_code'),'warning_count':len(result['warnings']),
            'unknown_money_cells':sum(r.get(c['key']) is None for r in result['rows'] for c in result['columns'] if c['kind']=='money')}
        return result
    try:
        await check('overview',today-timedelta(days=6),today)
        await check('payouts',month_start,month_end)
        await check('daily-balance',yesterday,today)
        await check('types',yesterday,yesterday)
        accruals=await check('accruals',yesterday,yesterday)
        postings=list(dict.fromkeys(r['posting'] for r in accruals['rows'] if r.get('posting') and re.fullmatch(r'\d+-\d+-\d+',r['posting'])))[:10]
        if postings:await check('postings',yesterday,yesterday,postings=postings)
        else:report['checks']['postings']={'status':'NO_POSTING_IN_SELECTED_DAY'}
        await check('products',month_start,month_end)
        await check('daily-products',yesterday,yesterday)
        performance=PerformanceReader('LIVE_READ')
        try:
            try:
                data=await performance.expenses(str(yesterday),str(today))
                report['checks']['advertising']={'complete':data['complete'],'rows':len(data['rows']),'column_count':len(data['columns'])}
            except ReadError as error:report['checks']['advertising']={'status':error.code}
        finally:await performance.close()
        report['checks']['report_generation']={'status':'NOT_EXECUTED_LIVE','reason':'Explicit UI start only; synthetic tests cover jobs and no-retry'}
    finally:await client.close()
    report['status']='FINANCE_READ_CHECKED_WITH_LIMITS'
    return report


if __name__=='__main__':
    if len(sys.argv)!=1 or Path.cwd().resolve()!=ROOT.resolve():raise SystemExit('Closed project command required')
    try:result=asyncio.run(verify())
    except Exception:result={'status':'INTERNAL_ERROR','shop_mutations':0,'report_jobs_created':0,'persisted_real_rows':0}
    confined(ROOT/'docs/evidence/finance-live-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result['status']=='FINANCE_READ_CHECKED_WITH_LIMITS' else 1)
