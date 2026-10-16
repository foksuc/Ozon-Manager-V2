"""Monthly response shape only: schema-approved names/types, no entity values."""
import asyncio,json,sys
from datetime import date,timedelta
from pathlib import Path
ROOT=Path(__file__).absolute().parent.parent
sys.path.insert(0,str(ROOT/'backend'))
from app.connection import load_connection
from app.ozon_read import ReadClient,ReadOperation as Op,ReadError
from app.storage import confined

def shape(value,depth=0):
    allowed={'result','rows','header','item','sku','offer_id','name','delivery_commission','return_commission','quantity','amount','standard_fee','total','currency_sys_name','currency','commission_ratio','seller_price_per_instance','rowNumber','report','error','code','message'}
    if depth>5:return type(value).__name__
    if isinstance(value,dict):return {k if k in allowed else '<other-field-'+str(i)+'>':shape(v,depth+1) for i,(k,v) in enumerate(value.items())}
    if isinstance(value,list):return {'type':'array','count':len(value),'first_shape':shape(value[0],depth+1) if value else None}
    return type(value).__name__

async def check():
    settings=load_connection('LIVE_READ');client=ReadClient(settings.credentials)
    end=date.today().replace(day=1)-timedelta(days=1)
    try:return {'source':Op.FINANCE_REALIZATION.value[1],'shape':shape(await client.read(Op.FINANCE_REALIZATION,{'year':end.year,'month':end.month})),'values_persisted':False}
    except ReadError as exc:return {'error_code':exc.code,'values_persisted':False}
    finally:await client.close()

if __name__=='__main__':
    if len(sys.argv)!=1 or Path.cwd().resolve()!=ROOT.resolve():raise SystemExit('Closed command only')
    result=asyncio.run(check())
    confined(ROOT/'docs/evidence/finance-contract-shape.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
