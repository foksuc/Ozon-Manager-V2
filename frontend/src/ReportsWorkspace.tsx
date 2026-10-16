import { useState } from 'react'
import { Button } from '@radix-ui/themes'
import type { RuntimeMode } from './api'
import { WarehouseTool } from './WarehouseProducts'
export function ReportsWorkspace({mode,scopeKey}:{mode:RuntimeMode;scopeKey:string}){
 const [tab,setTab]=useState('list')
 return <section className="warehouse-workspace"><nav className="subnav" aria-label="Отчёты"><Button variant="ghost" aria-pressed={tab==='list'} className={tab==='list'?'active':''} onClick={()=>setTab('list')}>Список отчётов</Button><Button variant="ghost" aria-pressed={tab==='info'} className={tab==='info'?'active':''} onClick={()=>setTab('info')}>Проверить готовность</Button><Button variant="ghost" aria-pressed={tab==='create'} className={tab==='create'?'active':''} onClick={()=>setTab('create')}>Создать отчёт</Button></nav><WarehouseTool key={tab} mode={mode} scopeKey={scopeKey} title={tab==='list'?'Список отчётов':tab==='info'?'Готовность и скачивание':'Создание отчёта'} operations={tab==='list'?['REPORT_LIST']:tab==='info'?['REPORT_INFO']:['REPORT_PRODUCTS','REPORT_RETURNS','REPORT_POSTINGS','REPORT_DISCOUNTED','REPORT_WAREHOUSE_STOCK','REPORT_PLACEMENT_BY_PRODUCTS','REPORT_PLACEMENT_BY_SUPPLIES','REPORT_MARKED_PRODUCTS_SALES']}/></section>
}
