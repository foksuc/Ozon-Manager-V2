import {Heading,Text} from '@radix-ui/themes'
import {useState} from 'react'
import {useTableColumns} from './uiPreferences'
import {formatMoney,formatShortDate,type FinanceResult} from './api'
import './finance-dashboard.css'

type Amount={label:string;amount:string|null;currency:string|null}
type Comparison={label:string;currency:string|null;previous:string|null;delta:string|null;change:string|null}
type Expenses=Amount&{sales:string|null;ratio:string|null}
type Daily={date:string|null;accrued:string|null;paid:string|null;currency:string|null}
type Dashboard={cashflows?:Amount[];cashflow_totals?:Amount[];comparison?:Comparison[];expenses?:Expenses[];expense_totals?:Expenses[];daily?:Daily[];previous_period?:{from:string;to:string};sections?:{key:string;complete:boolean;warnings:string[]}[]}
export type DashboardResult=FinanceResult&{dashboard?:Dashboard}

function ChartTotals({rows}:{rows:Amount[]}){return <dl className="finance-chart-totals">{rows.map((row,i)=><div key={i}><dt>{row.label}{row.currency&&row.currency!=='RUB'?` · ${row.currency}`:''}</dt><dd>{formatMoney(row.amount,row.currency)}</dd></div>)}</dl>}
function ExpenseMetric({row,total=false}:{row:Expenses;total?:boolean}){
  return <div className="finance-expense" data-total={total||undefined}>
    <span>{row.label}{row.currency&&row.currency!=='RUB'?` · ${row.currency}`:''}</span>
    <strong>{formatMoney(row.amount,row.currency)}</strong>
    <span>{row.ratio===null?(total?'Доля итоговых расходов не определена':'Доля не определена'):`${row.ratio.replace('.',',')} % реализации`}</span>
    <div className="finance-bar-track" aria-hidden="true"><div className="finance-bar is-expense" style={{width:row.ratio===null?'0%':`${Math.min(100,Number(row.ratio))}%`}}/></div>
  </div>
}
function CashflowChart({rows,totals}:{rows:Amount[];totals:Amount[]}){
  const currencies=[...new Set(rows.map(row=>row.currency))]
  return <section className="finance-dashboard-panel"><Heading as="h2" size="4">Из чего складываются начисления</Heading><Text as="p" size="2" color="gray">Состав баланса за выбранный период. Поступления и удержания показаны отдельно.</Text>
    {currencies.map(currency=>{const group=rows.filter(row=>row.currency===currency);const scale=Math.max(...group.map(row=>Math.abs(Number(row.amount))),1)
      return <ul className="finance-breakdown" key={currency??'unknown'}>{group.map((row,i)=><li key={i}><div className="finance-breakdown-label"><span>{row.label}</span><strong>{formatMoney(row.amount,row.currency)}</strong></div><div className="finance-bar-track" aria-hidden="true"><div className={Number(row.amount)<0?'finance-bar is-expense':'finance-bar'} style={{width:row.amount!==null&&row.currency?`${Math.min(100,Math.abs(Number(row.amount))/scale*100)}%`:'0%'}}/></div></li>)}</ul>
    })}
    <ChartTotals rows={totals}/>{!rows.length&&<Text as="p" color="gray">Состав начислений пока не подтверждён.</Text>}
  </section>
}

function DailyCurrencyChart({group,currency}:{group:Daily[];currency:string}){
  const [active,setActive]=useState<number|null>(null)
  const tableColumns=useTableColumns('finance-daily-chart','Финансы — точные суммы дневного графика',[{key:'date',label:'День'},{key:'accrued',label:'Начислено'},{key:'paid',label:'Выплачено'}])
  const visible=(key:string)=>!tableColumns.hidden.includes(key)
  const values=group.flatMap(row=>[row.accrued,row.paid]).filter((v):v is string=>v!==null).map(Number)
  if(!values.length)return <Text as="p" color="gray">Дневные суммы не предоставлены · {currency}</Text>
  const low=Math.min(0,...values),high=Math.max(0,...values),span=high-low||1
  const left=94,right=612,top=28,bottom=178
  const x=(index:number)=>left+index*(right-left)/Math.max(1,group.length-1),y=(value:number)=>bottom-(value-low)/span*(bottom-top)
  const path=(key:'accrued'|'paid')=>group.map((row,i)=>row[key]===null?'':`${i===0||group[i-1]?.[key]===null?'M':'L'}${x(i)},${y(Number(row[key]))}`).join(' ')
  const selected=active===null?null:group[active]
  // Floating point is only chart geometry and approximate axis ticks. Tooltip
  // and accessible labels always use untouched server Decimal strings.
  return <div className="finance-trend-wrap"><div className="finance-chart-legend"><span>Начислено</span><span className="paid">Выплачено</span></div>
    <svg className="finance-trend" viewBox="0 0 644 218" role="group" aria-label={`Дневная динамика начислений и выплат, ${currency}. Наведите или выберите день клавишей Tab.`} onMouseLeave={event=>{if(!event.currentTarget.contains(document.activeElement))setActive(null)}}>
      <text x="4" y="15">Сумма, {currency==='RUB'?'₽':currency}</text>
      {[0,1,2,3,4].map(i=>{const amount=low+span*i/4;return <g key={i}><line x1={left} x2={right} y1={y(amount)} y2={y(amount)} className="finance-chart-baseline"/><text x={left-10} y={y(amount)+4} textAnchor="end">{new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(amount)}</text></g>})}
      <line x1={left} x2={left} y1={top} y2={bottom} className="finance-chart-baseline"/><path d={path('accrued')} className="finance-line"/><path d={path('paid')} className="finance-line is-paid"/>
      {group.map((row,i)=>{const start=i===0?left-9:(x(i-1)+x(i))/2,end=i===group.length-1?right+9:(x(i)+x(i+1))/2;return <g key={i}>{row.accrued!==null&&<circle cx={x(i)} cy={y(Number(row.accrued))} r="3" className="finance-point"/>}{row.paid!==null&&<circle cx={x(i)} cy={y(Number(row.paid))} r="3" className="finance-point is-paid"/>}<rect className="finance-day-hit" x={start} y={top} width={end-start} height={bottom-top} tabIndex={0} role="button" aria-label={`${formatShortDate(row.date)}: начислено ${formatMoney(row.accrued,currency)}, выплачено ${formatMoney(row.paid,currency)}`} onMouseEnter={()=>setActive(i)} onFocus={()=>setActive(i)} onBlur={()=>setActive(null)} onClick={()=>setActive(i)} onKeyDown={event=>{if(event.key==='Escape')setActive(null);if(event.key==='Enter'||event.key===' '){event.preventDefault();setActive(i)}}}/></g>})}
      {active!==null&&<line x1={x(active)} x2={x(active)} y1={top} y2={bottom} className="finance-chart-crosshair"/>}<text x={left} y="208">{formatShortDate(group[0]?.date)}</text><text x={right} y="208" textAnchor="end">{formatShortDate(group.at(-1)?.date)}</text>
    </svg>
    {selected&&<div className="finance-chart-tooltip" role="status"><strong>{formatShortDate(selected.date)}</strong><span>Начислено <b>{formatMoney(selected.accrued,currency)}</b></span><span>Выплачено <b>{formatMoney(selected.paid,currency)}</b></span></div>}
    <details className="finance-chart-values"><summary>Точные суммы по дням</summary><table><thead><tr>{visible('date')&&<th>День</th>}{visible('accrued')&&<th>Начислено</th>}{visible('paid')&&<th>Выплачено</th>}</tr></thead><tbody>{group.map((row,i)=><tr key={i}>{visible('date')&&<td>{formatShortDate(row.date)}</td>}{visible('accrued')&&<td>{formatMoney(row.accrued,currency)}</td>}{visible('paid')&&<td>{formatMoney(row.paid,currency)}</td>}</tr>)}</tbody></table></details>
  </div>
}
function DailyChart({rows}:{rows:Daily[]}){
  return <section className="finance-dashboard-panel"><Heading as="h2" size="4">Дневная динамика</Heading><Text as="p" size="2" color="gray">Дни учёта Ozon. Выплаты показаны отдельно от начислений; это не даты банковских переводов.</Text>
    {[...new Set(rows.map(row=>row.currency))].map(currency=>{
      const group=rows.filter(row=>row.currency===currency)
      if(!currency)return <Text as="p" color="gray" key="unknown">График недоступен: валюта дневных сумм не подтверждена.</Text>
      return <DailyCurrencyChart key={currency} group={group} currency={currency}/>
    })}
    {!rows.length&&<Text as="p" color="gray">Дневные суммы недоступны для выбранного периода.</Text>}
  </section>
}

export function FinanceDashboard({data,analytics=false}:{data:DashboardResult;analytics?:boolean}){
  const dashboard=data.dashboard
  if(!dashboard)return null
  const statusNames:Record<string,string>={current:'Текущий период',previous:'Предыдущий период',expenses:'Расходы',daily:'Дневная динамика'}
  return <section className="finance-dashboard" aria-label={analytics?'Финансовые дашборды':'Обзор начислений'}>
    {!!data.metrics.length&&<dl className="finance-dashboard-metrics">{data.metrics.map((metric,i)=>{const comparison=dashboard.comparison?.find(row=>row.label===metric.label&&row.currency===metric.currency);return <div key={i}><dt>{metric.label}</dt><dd>{formatMoney(metric.amount,metric.currency)}</dd>{comparison&&<><span className="finance-metric-previous">Ранее {formatMoney(comparison.previous,comparison.currency)}</span><span className="finance-metric-change">Изменение {formatMoney(comparison.delta,comparison.currency)}{comparison.change!==null?` (${comparison.change.replace('.',',')} %)`:''}</span></>}</div>})}</dl>}
    {analytics&&dashboard.previous_period&&<Text as="p" size="2" color="gray">Сравнение с {formatShortDate(dashboard.previous_period.from)} — {formatShortDate(dashboard.previous_period.to)} · период той же длины</Text>}
    {dashboard.sections?.filter(section=>!section.complete).map(section=><div key={section.key} className="finance-dashboard-unavailable"><strong>{statusNames[section.key]}</strong>{section.warnings.map((warning,i)=><Text as="p" size="2" key={i}>{warning}</Text>)}</div>)}
    <div className="finance-dashboard-grid"><CashflowChart rows={dashboard.cashflows??[]} totals={dashboard.cashflow_totals??[]}/>{analytics&&<DailyChart rows={dashboard.daily??[]}/>}</div>
    {analytics&&<section className="finance-dashboard-panel"><Heading as="h2" size="4">Расходы относительно продаж</Heading><Text as="p" size="2" color="gray">По расчётным отчётам Ozon. Доля = абсолютная сумма / реализация за эти периоды. Возвраты показаны отдельно и не включены в итог расходов; прибыль не рассчитывается.</Text><div className="finance-expense-grid">{dashboard.expenses?.map((row,i)=><ExpenseMetric row={row} key={`expense-${i}`}/>)}{dashboard.expense_totals?.map((row,i)=><ExpenseMetric row={row} total key={`total-${i}`}/>)}</div>{!dashboard.expenses?.length&&<Text as="p" color="gray">Подтверждённых расчётных отчётов за этот период нет.</Text>}</section>}
  </section>
}
