import { useState } from 'react'
import { Button } from '@radix-ui/themes'
import type { RuntimeMode } from './api'
import { WarehouseTool } from './WarehouseProducts'
export function WarehousePrices({mode,scopeKey}:{mode:RuntimeMode;scopeKey:string}){
 const [tab,setTab]=useState('prices')
 return <section className="warehouse-workspace"><nav className="subnav" aria-label="Цены товаров"><Button variant="ghost" aria-pressed={tab==='prices'} className={tab==='prices'?'active':''} onClick={()=>setTab('prices')}>Цены и комиссии</Button><Button variant="ghost" aria-pressed={tab==='edit'} className={tab==='edit'?'active':''} onClick={()=>setTab('edit')}>Изменение цен</Button><Button variant="ghost" aria-pressed={tab==='timer'} className={tab==='timer'?'active':''} onClick={()=>setTab('timer')}>Минимальная цена и таймер</Button><Button variant="ghost" aria-pressed={tab==='discount'} className={tab==='discount'?'active':''} onClick={()=>setTab('discount')}>Уценённые товары</Button></nav><WarehouseTool key={tab} mode={mode} scopeKey={scopeKey} title={tab==='prices'?'Цены и комиссии':tab==='edit'?'Изменение цен':tab==='timer'?'Таймер минимальной цены':'Уценённые товары'} operations={tab==='prices'?['PRODUCT_PRICES']:tab==='edit'?['PRICE_UPDATE']:tab==='timer'?['PRICE_TIMER_STATUS','PRICE_TIMER_UPDATE']:['PRICE_DISCOUNTED','PRICE_DISCOUNT_UPDATE']}/></section>
}
