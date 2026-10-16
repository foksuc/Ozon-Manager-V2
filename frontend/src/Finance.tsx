import {useRef,useState} from 'react'
import {Badge,Button,Callout,Heading,Select,Text,TextField} from '@radix-ui/themes'
import {useMutation,useQuery} from '@tanstack/react-query'
import {createFinanceReport,formatDateTime,formatMoney,formatTime,getFinance,type FinanceQuery,type FinanceResult,type RuntimeMode} from './api'
import {compareDecimalStrings} from './numbers'
import {financeCsv} from './csv'
import {ScrollTable,TableFilterChips,useTableFilters,type TableFilterColumn} from './TableTools'
import {LoadingOverlay} from './LoadingOverlay'
import {Icon,type IconName} from './Icon'
import {financeDisplayColumns} from './financeColumns'
import {FinanceDashboard,type DashboardResult} from './FinanceDashboard'
import {VendorErrorNotice} from './VendorErrorNotice'
import {financeTableNames,usePreferences,type Preferences} from './uiPreferences'

const iso=(d:Date)=>d.toISOString().slice(0,10)
const today=new Date()
const month=iso(new Date(Date.UTC(today.getUTCFullYear(),today.getUTCMonth()-1,1))).slice(0,7)
const defaults={from:iso(new Date(today.getTime()-6*86400000)),to:iso(today)}
const tabs=[['overview','Обзор'],['payouts','Выплаты'],['accruals','Начисления'],['products','Товары'],['reports','Документы'],['analytics','Аналитика']] as const
type FinanceTab=typeof tabs[number][0]
const tabIcons:Record<typeof tabs[number][0],IconName>={overview:'overview',payouts:'payouts',accruals:'accruals',products:'stocks',reports:'documents',analytics:'analytics'}
const isNameColumn=(label:string)=>/название|наименование|^товар$|^name$|^campaign name$/i.test(label)

function exportTable(columns:string[],rows:(string|null)[][],settings:Pick<Preferences,'exportDelimiter'|'exportBom'>){
  const delimiter=settings.exportDelimiter==='comma'?',':settings.exportDelimiter==='tab'?'\t':';'
  const blob=new Blob([financeCsv(columns,rows,{delimiter,bom:settings.exportBom})],{type:`text/${settings.exportDelimiter==='tab'?'tab-separated-values':'csv'};charset=utf-8`})
  const href=URL.createObjectURL(blob);const a=document.createElement('a');a.href=href;a.download=`ozon-manager-finance.${settings.exportDelimiter==='tab'?'tsv':'csv'}`;a.click();setTimeout(()=>URL.revokeObjectURL(href),1000)
}

function FinancialTable({data,details=false,view='overview'}:{data:FinanceResult;details?:boolean;view?:string}){
  const {value:preferences}=usePreferences()
  const [query,setQuery]=useState('')
  const [sort,setSort]=useState<{key:string;reverse:boolean}|null>(null)
  const source=details?data.details:data.rows
  const columns=financeDisplayColumns(data.columns,source)
  const filterColumns:TableFilterColumn[]=columns.map(c=>({key:c.key,label:c.label,kind:c.kind==='money'||c.kind==='percent'?'decimal':'text'}))
  const tableFilters=useTableFilters<typeof source[number]>(filterColumns)
  const rows=tableFilters.apply(source.filter(row=>Object.values(row).some(v=>v?.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')))),(row,key)=>row[key])
  if(sort) rows.sort((a,b)=>{const kind=columns.find(c=>c.key===sort.key)?.kind;const comparison=kind==='money'||kind==='percent'?((a.currency??'').localeCompare(b.currency??'')||compareDecimalStrings(a[sort.key]??'',b[sort.key]??'')):(a[sort.key]??'').localeCompare(b[sort.key]??'','ru',{numeric:true});return comparison*(sort.reverse?-1:1)})
  return <section className="financial-results">
    <div className="finance-table-tools"><TextField.Root autoComplete="off" type="search" placeholder="Найти в таблице…" aria-label="Поиск в финансовой таблице" value={query} onChange={e=>setQuery(e.target.value)}/><Badge color="gray">{rows.length} / {source.length} строк</Badge><Button variant="outline" disabled={!(preferences.exportFilteredOnly?rows:source).length} onClick={()=>{const exportRows=preferences.exportFilteredOnly?rows:source;exportTable(columns.map(c=>c.label),exportRows.map(r=>columns.map(c=>r[c.key]??null)),preferences)}}>Экспорт</Button></div>
    <TableFilterChips filters={tableFilters.activeFilters} onClear={tableFilters.clear} onClearAll={tableFilters.clearAll}/><ScrollTable {...tableFilters.surfaceProps} overlay={tableFilters.menu} className="finance-table" tableId={`finance-${view}${details?'-details':''}`} tabIndex={0} aria-label={`${financeTableNames[view]??'Финансовая таблица'}${details?' · Детализация':''}`}><table><thead><tr>{columns.map(c=><th {...tableFilters.headerProps(c.key,true)} className={c.key==='name'||isNameColumn(c.label)?'column-name':undefined} key={c.key} scope="col" aria-sort={sort?.key===c.key?(sort.reverse?'descending':'ascending'):'none'}><button className="sort-button" onClick={()=>setSort({key:c.key,reverse:sort?.key===c.key?!sort.reverse:false})}>{c.label}{sort?.key===c.key?(sort.reverse?' ↓':' ↑'):''}</button></th>)}</tr></thead><tbody>{rows.length?rows.map((r,i)=><tr key={i}>{columns.map(c=><td {...tableFilters.cellProps(c.key,r[c.key])} className={c.key==='name'||isNameColumn(c.label)?'column-name':c.kind!=='text'?'numeric':undefined} title={r[c.key]??undefined} key={c.key}>{c.key==='currency'&&r[c.key]==='RUB'?(preferences.currencyDisplay==='code'?'RUB':'руб.'):c.kind==='money'?formatMoney(r[c.key],r.currency):c.kind==='percent'?(r[c.key]===null?'Нет данных':r[c.key]+' %'):r[c.key]??'Нет данных'}</td>)}</tr>):<tr><td colSpan={Math.max(1,columns.length)}>Данных за выбранный период нет</td></tr>}</tbody></table></ScrollTable>
  </section>
}

export function Finance({mode,scopeKey=mode}:{mode:RuntimeMode;scopeKey?:string}){
  const [tab,setTab]=useState<FinanceTab>('overview')
  return <div className="finance-workspace">
    <div className="page-heading"><Heading size="7" className="page-title"><Icon name="finance"/>Финансы</Heading></div>
    <nav className="subnav finance-tabs" aria-label="Разделы финансов">{tabs.map(([id,label])=><Button key={id} variant={tab===id?'solid':'outline'} aria-pressed={tab===id} onClick={()=>setTab(id)}><Icon name={tabIcons[id]}/>{label}</Button>)}</nav>
    {tabs.map(([id])=><div key={id} hidden={id!==tab} data-finance-tab={id}><FinanceWorkspace mode={mode} scopeKey={scopeKey} tab={id}/></div>)}
  </div>
}

function FinanceWorkspace({mode,scopeKey,tab}:{mode:RuntimeMode;scopeKey:string;tab:FinanceTab}){
  const [from,setFrom]=useState(defaults.from),[to,setTo]=useState(defaults.to)
  const [selectedMonth,setMonth]=useState(month),[subview,setSubview]=useState('accruals')
  const [postings,setPostings]=useState(''),[daily,setDaily]=useState(false)
  const [reportKind,setReportKind]=useState('mutual'),[code,setCode]=useState('')
  const [request,setRequest]=useState<FinanceQuery|null>(null)
  const [detail,setDetail]=useState(false),[reportError,setReportError]=useState<string|null>(null)
  const reportGeneration=useRef(0)
  const [paidDaily,setPaidDaily]=useState(false)
  const [revision,setRevision]=useState(0)
  const finance=useQuery({queryKey:['finance',scopeKey,mode,tab,request,revision],queryFn:({signal})=>getFinance(request!,signal),enabled:request!==null,retry:false,refetchOnWindowFocus:false,refetchOnMount:false,staleTime:Infinity})
  const report=useMutation({mutationFn:()=>createFinanceReport(reportKind,selectedMonth),retry:false})
  const reset=()=>{setRequest(null);setReportError(null);reportGeneration.current++;report.reset()}
  const load=()=>{
    setRevision(v=>v+1)
    let view=tab==='analytics'?'analytics':tab==='reports'?'report':tab==='accruals'?subview:tab==='products'?(daily?'daily-products':'products'):tab
    if(tab==='payouts'&&paidDaily)view='daily-balance'
    let start=from,end=to
    if(tab==='products'&&!daily){start=selectedMonth+'-01';end=start}
    const payload={view,date_from:start,date_to:end,...(view==='postings'?{postings:postings.split(/[\s,;]+/).filter(Boolean)}:{}),...(view==='report'?{report_code:code}:{})}
    setDetail(false);setRequest(payload)

  }
  const loading=finance.isFetching
  return <>
    <section className="finance-controls" aria-label="Параметры финансового отчёта">
      {(tab==='products'&&!daily)||tab==='reports'?<label>Месяц<input autoComplete="off" type="month" value={selectedMonth} onChange={e=>{setMonth(e.target.value);reset()}}/></label>:tab==='accruals'&&subview!=='accruals'?null:<><label>С<input autoComplete="off" type="date" value={from} onChange={e=>{setFrom(e.target.value);reset()}}/></label><label>По<input autoComplete="off" type="date" value={to} onChange={e=>{setTo(e.target.value);reset()}}/></label></>}
      {tab==='accruals'&&<label>Детализация<Select.Root value={subview} onValueChange={v=>{setSubview(v);reset()}}><Select.Trigger aria-label="Вид начислений"/><Select.Content><Select.Item value="accruals">По дням</Select.Item><Select.Item value="postings">По отправлениям</Select.Item><Select.Item value="types">Справочник</Select.Item></Select.Content></Select.Root></label>}
      {tab==='products'&&<label>Отчёт<Select.Root value={daily?'daily':'monthly'} onValueChange={v=>{setDaily(v==='daily');reset()}}><Select.Trigger aria-label="Период реализации"/><Select.Content><Select.Item value="monthly">За месяц</Select.Item><Select.Item value="daily">За день · Premium</Select.Item></Select.Content></Select.Root></label>}
      {tab==='payouts'&&<label>Детализация<Select.Root value={paidDaily?'daily':'period'} onValueChange={v=>{setPaidDaily(v==='daily');reset()}}><Select.Trigger aria-label="Детализация выплат"/><Select.Content><Select.Item value="period">Расчётные периоды</Select.Item><Select.Item value="daily">Дневной баланс</Select.Item></Select.Content></Select.Root></label>}
      {tab==='reports'&&<label>Документ<Select.Root value={reportKind} onValueChange={v=>{setReportKind(v);reset()}}><Select.Trigger aria-label="Финансовый документ"/><Select.Content><Select.Item value="mutual">Взаиморасчёты</Select.Item><Select.Item value="compensation">Компенсации</Select.Item><Select.Item value="decompensation">Декомпенсации</Select.Item></Select.Content></Select.Root></label>}
      {tab!=='reports'&&<div className="refresh-control"><Button disabled={loading||!from||!to} onClick={load}>{loading?'Загружаем…':finance.data?'Обновить':'Загрузить данные'}</Button>{finance.data&&<span>Обновлено: {formatTime(finance.data.fetched_at)}</span>}</div>}
    </section>
    {tab==='accruals'&&subview==='postings'&&<label className="finance-postings">Номера отправлений · до 50<textarea autoComplete="off" value={postings} onChange={e=>{setPostings(e.target.value);reset()}} placeholder="По одному в строке"/></label>}
    {tab==='products'&&<Text as="p" size="2" color="gray" className="context-note">Месячный отчёт обычно готов к 5 числу следующего месяца. Дневной требует Premium Plus/Pro и доступен за последние 32 дня; используется дата «С».</Text>}
    {tab==='payouts'&&<Text as="p" size="2" color="gray" className="context-note">Суммы за бухгалтерские периоды. Дневной баланс доступен за период до 31 дня; даты поступления денег в банк не предоставляются.</Text>}
    {tab==='reports'&&<section className="report-controls"><Heading as="h2" size="4">Финансовый документ</Heading><Text as="p" size="2" color="gray" className="context-note">Создаёт документ в Ozon. Один запуск на тип и месяц за сеанс; при потерянном ответе повтор отключён.</Text>
      <Button disabled={report.isPending||!!reportError} onClick={()=>{const generation=++reportGeneration.current;report.mutate(undefined,{onSuccess:r=>{if(generation!==reportGeneration.current)return;setCode(r.code);setRevision(v=>v+1);setRequest({view:'report',date_from:selectedMonth+'-01',date_to:selectedMonth+'-01',report_code:r.code})},onError:e=>{if(generation===reportGeneration.current)setReportError(e.message)}})}}>{report.isPending?'Создаём…':'Создать финансовый отчёт'}</Button>
      {reportError&&<Text as="p" color="red" role="alert">{reportError}</Text>}
      <label>Код существующего отчёта<TextField.Root autoComplete="off" value={code} onChange={e=>{setCode(e.target.value);setRequest(null)}} aria-label="Код отчёта"/></label><Button variant="outline" disabled={!code||loading} onClick={load}>Проверить готовность</Button>
    </section>}
    <LoadingOverlay active={loading||report.isPending} label={report.isPending?'Создаём финансовый документ…':'Загружаем финансовые данные…'}/>
    {finance.isError&&<Callout.Root color="red"><Callout.Text>{finance.error?.message}</Callout.Text></Callout.Root>}
    {finance.data&&<section className="finance-result-section" aria-busy={finance.isFetching}>
      <VendorErrorNotice error={finance.data.error}/>
      <VendorErrorNotice error={finance.data.report?.error}/>
      <div className="result-meta"><Badge color={finance.data.complete?'green':'amber'}>{finance.data.complete?'Данные загружены':'Неполные данные'}</Badge>{finance.data.mode==='MOCK'&&<Badge color="gray">Демонстрация</Badge>}</div>
      {finance.data.warnings.map((w,i)=><Callout.Root color={finance.data.complete?'blue':'amber'} key={i}><Callout.Text>{w}</Callout.Text></Callout.Root>)}
      {(tab==='overview'||tab==='analytics')&&<FinanceDashboard data={finance.data as DashboardResult} analytics={tab==='analytics'}/>}
      {tab!=='overview'&&tab!=='analytics'&&!!finance.data.metrics.length&&<dl className="finance-summary">{finance.data.metrics.map((m,i)=><div key={i}><dt>{m.label}</dt><dd>{formatMoney(m.amount,m.currency)}</dd></div>)}</dl>}
      {finance.data.report&&<section className="report-status"><Badge color={finance.data.report.status==='success'?'teal':'amber'}>{({waiting:'В очереди',processing:'Готовится',success:'Готов',failed:'Ошибка'} as Record<string,string>)[finance.data.report.status]}</Badge><Text as="p" size="2">Создан: {finance.data.report.created_at?formatDateTime(finance.data.report.created_at):'Нет данных'} · Доступен до: {finance.data.report.expires_at?formatDateTime(finance.data.report.expires_at):'Нет данных'}</Text>{finance.data.report.file&&mode!=='MOCK'&&<a href={finance.data.report.file} target="_blank" rel="noreferrer">Открыть файл отчёта Ozon</a>}{mode==='MOCK'&&<Text as="p">Синтетический отчёт; ссылки на реальный файл нет.</Text>}</section>}
      {!!finance.data.columns.length&&<><div className="finance-detail-toggle">{finance.data.details.length>0&&<Button variant="outline" aria-pressed={detail} onClick={()=>setDetail(!detail)}>{detail?'Показать итоги':'Показать детализацию начислений'}</Button>}</div><FinancialTable data={finance.data} details={detail} view={request?.view??'overview'}/></>}
    </section>}
    {!finance.data&&!loading&&tab!=='reports'&&<div className="empty-state finance-empty"><svg width="48" height="48" viewBox="0 0 48 48" fill="none" aria-hidden="true"><path d="M9 9v30h30M17 31V22m9 9V16m9 15V10" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/></svg><Heading as="h2" size="4">Выберите период</Heading><Text color="gray">Нажмите «Загрузить данные», чтобы увидеть показатели и таблицу.</Text></div>}
  </>
}
