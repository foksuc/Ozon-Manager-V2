import { useEffect, useRef, useState } from 'react'
import { Button, Callout } from '@radix-ui/themes'
import {formatDateTime,type RuntimeMode} from './api'
import {sellerRowNeedsAttention} from './sellerAnalytics'
import {ScrollTable} from './TableTools'

import {analyticsCatalogPage,analyticsStocksBatch,waitForAnalyticsPage,type AnalyticsProduct} from './analyticsApi'
import { SellerDataTable } from './StrategyWorkspace'
import { sellerIds, sellerRead, sellerText } from './sellerApi'
import type { SellerReadOperation, SellerReadResult, SellerRecord } from './sellerApi'
import './seller-workspaces.css'

type Report = 'ANALYTICS_STOCKS' | 'ANALYTICS_TURNOVER' | 'ANALYTICS_WAREHOUSES'
const titles: Record<Report, string> = { ANALYTICS_STOCKS: 'Остатки и ликвидность', ANALYTICS_TURNOVER: 'Оборачиваемость', ANALYTICS_WAREHOUSES: 'Остатки по складам' }
const grades = ['TURNOVER_GRADE_NONE', 'DEFICIT', 'POPULAR', 'ACTUAL', 'SURPLUS', 'NO_SALES', 'WAS_NO_SALES', 'RESTRICTED_NO_SALES', 'COLLECTING_DATA', 'WAITING_FOR_SUPPLY', 'WAS_DEFICIT', 'WAS_POPULAR', 'WAS_ACTUAL', 'WAS_SURPLUS']
const tags = ['ITEM_ATTRIBUTE_NONE', 'ECONOM', 'NOVEL', 'DISCOUNT', 'FBS_RETURN', 'SUPER', 'MARKABLE']
const zones = ['PLACEMENT_ZONE_NONE', 'CLOSED_ZONE', 'DANGEROUS_GOOD', 'PRODUCTS_PLUS_17', 'SORT', 'NON_SORT_MEZ', 'OVERSIZE', 'JEWELRY', 'UNRESOLVED']

function MultiFilter({ label, values, selected, onChange }: { label: string; values: string[]; selected: string[]; onChange: (values: string[]) => void }) {
  return <label>{label}<select multiple size={4} value={selected} onChange={event => onChange([...event.currentTarget.selectedOptions].map(option => option.value))}>{values.map(value => <option key={value} value={value}>{sellerText(value)}</option>)}</select><span className="context-note">Без выбора — все. Ctrl / Cmd — несколько значений.</span></label>
}

export function SellerAnalyticsWorkspace({ mode, scopeKey }: { mode: RuntimeMode; scopeKey: string }) {
  const [report, setReport] = useState<Report>('ANALYTICS_STOCKS')
  const [catalogue,setCatalogue]=useState<AnalyticsProduct[]>([])
  const [catalogueBusy,setCatalogueBusy]=useState(false)
  const [catalogueComplete,setCatalogueComplete]=useState(false)
  const [catalogueError,setCatalogueError]=useState('')
  const [catalogueGeneration,setCatalogueGeneration]=useState(0)
  const [selectedSkus,setSelectedSkus]=useState<Set<string>>(new Set())
  const [productSearch,setProductSearch]=useState('')
  const [productPage,setProductPage]=useState(0)
  const [warehouses, setWarehouses] = useState('')
  const [clusters, setClusters] = useState('')
  const [clusterType, setClusterType] = useState('cluster_ids')
  const [warehouseType, setWarehouseType] = useState('ALL')
  const [gradeFilter, setGradeFilter] = useState<string[]>([])
  const [tagFilter, setTagFilter] = useState<string[]>([])
  const [zoneFilter, setZoneFilter] = useState<string[]>([])
  const [unmarked, setUnmarked] = useState(false)
  const [result, setResult] = useState<SellerReadResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [loadedAt, setLoadedAt] = useState('')
  const [search, setSearch] = useState('')
  const [turnoverAvailable, setTurnoverAvailable] = useState(0)
  const [clock, setClock] = useState(Date.now())
  const context = useRef(0)
  const loadingController=useRef<AbortController|null>(null)
  const [progress,setProgress]=useState('')
  useEffect(() => { const timer = window.setInterval(() => setClock(Date.now()), 1000); return () => window.clearInterval(timer) }, [])
  useEffect(() => {
    const revision=++context.current,controller=new AbortController()
    loadingController.current?.abort();setResult(null);setError('');setBusy(false);setWarehouses('');setClusters('');setLoadedAt('');setTurnoverAvailable(0);setCatalogue([]);setSelectedSkus(new Set());setCatalogueComplete(false);setCatalogueError('');setCatalogueBusy(true)
    void (async()=>{const products:AnalyticsProduct[]=[];const seen=new Set<string>();const cursors=new Set<string>();const warnings:string[]=[];let cursor='',complete=true
      try{for(let page=0;page<500;page++){
        const response=await analyticsCatalogPage(cursor,controller.signal);if(revision!==context.current)return
        complete=complete&&response.complete;warnings.push(...response.warnings)
        for(const row of response.rows){if(seen.has(row.sku))throw new Error('Каталог повторяет SKU на разных страницах; загрузка остановлена.');seen.add(row.sku);products.push(row)}
        setCatalogue([...products]);setSelectedSkus(new Set(products.map(row=>row.sku)))
        if(!response.next_cursor){setCatalogueComplete(complete);setCatalogueError([...new Set(warnings)].join(' '));return}
        if(cursors.has(response.next_cursor))throw new Error('Каталог повторяет страницы; загрузка остановлена.');cursors.add(response.next_cursor);cursor=response.next_cursor
      }throw new Error('Достигнут защитный предел 500 страниц каталога; показана загруженная часть.')
      }catch(err){if(revision===context.current&&!controller.signal.aborted)setCatalogueError(err instanceof Error?err.message:'Не удалось загрузить каталог товаров.')}
      finally{if(revision===context.current)setCatalogueBusy(false)}
    })()
    return()=>{controller.abort();loadingController.current?.abort();context.current++}
  }, [mode, scopeKey,catalogueGeneration])
  const delayed = report === 'ANALYTICS_TURNOVER' && clock < turnoverAvailable
  function parameters(): SellerRecord {
    if (report === 'ANALYTICS_STOCKS') {
      const request: SellerRecord = {}
      if (warehouses.trim()) request.warehouse_ids = sellerIds(warehouses, 1000)
      if (clusters.trim()) request[clusterType] = sellerIds(clusters, 1000)
      if (gradeFilter.length) request.turnover_grades = gradeFilter
      if (tagFilter.length) request.item_tags = tagFilter
      if (zoneFilter.length) request.placement_zone = zoneFilter
      if (unmarked) request.unmarked_stocks_only = true
      return request
    }
    return report === 'ANALYTICS_WAREHOUSES' ? { limit: 1000, offset: 0, warehouse_type: warehouseType } : { limit: 1000, offset: 0 }
  }
  async function load(next?: SellerRecord) {
    const revision = context.current
    const operation: SellerReadOperation = report
    const controller=new AbortController();loadingController.current=controller
    setBusy(true); setError('');setProgress('Подготовка загрузки…')
    let accumulated=next&&result?.operation===operation?[...result.rows]:[]
    try {
      const body = next ?? parameters()
      if(operation==='ANALYTICS_STOCKS'){
        let remaining=[...selectedSkus],processed=0
        if(!remaining.length)throw new Error('Выберите товары для отчёта.')
        for(;;){
          setProgress(`Обработка ${processed} / ${selectedSkus.size} SKU…`)
          const submitted=remaining.slice(0,50000),later=remaining.slice(50000)
          const response=await analyticsStocksBatch(submitted,body,controller.signal);if(revision!==context.current)return
          accumulated=[...accumulated,...response.rows];processed+=response.processed_skus
          remaining=[...response.remaining_skus,...later];setResult({...response,rows:accumulated,complete:response.complete&&!remaining.length})
          if((!response.continue_automatically&&!response.complete)||!remaining.length){setProgress(response.complete&&!remaining.length?`Обработано ${processed} SKU`:`Обработано ${processed} / ${selectedSkus.size} SKU; загрузка неполная`);break}
          if(response.processed_skus===0)throw new Error('Загрузка не продвинулась; продолжение остановлено.')
        }
      }else{
        let request=body;const visited=new Set<string>();let page=0
        for(;;){
          if(operation==='ANALYTICS_TURNOVER'&&Date.now()<turnoverAvailable){setProgress('Ожидание разрешённого Ozon интервала между страницами…');await waitForAnalyticsPage(turnoverAvailable-Date.now()+150,controller.signal)}
          setProgress(`Загрузка страницы ${++page} · получено ${accumulated.length} строк…`)
          const started=Date.now();if(operation==='ANALYTICS_TURNOVER')setTurnoverAvailable(started+60000)
          const response=await sellerRead(operation,request,controller.signal);if(revision!==context.current)return
          accumulated=[...accumulated,...response.rows];setResult({...response,rows:accumulated})
          if(!response.next_request){setProgress(`Все страницы загружены · ${accumulated.length} строк`);break}
          const signature=JSON.stringify(response.next_request)
          if(visited.has(signature))throw new Error('Отчёт повторяет страницу; продолжение остановлено.');visited.add(signature);request=response.next_request
          if(page>=500)throw new Error('Достигнут защитный предел 500 страниц; показана загруженная часть.')
          if(operation==='ANALYTICS_TURNOVER'){setProgress(`Загружено ${accumulated.length} строк · следующая страница через минуту`);await waitForAnalyticsPage(Math.max(0,started+60150-Date.now()),controller.signal)}
        }
      }
      setLoadedAt(new Date().toISOString())
    } catch (err) { if (revision === context.current){if(controller.signal.aborted){setProgress(`Загрузка остановлена · ${accumulated.length} строк`);if(accumulated.length)setResult(previous=>previous?{...previous,complete:false}:previous)}else setError(err instanceof Error ? err.message : 'Не удалось загрузить отчёт.')} }
    finally { if (revision === context.current) setBusy(false) }
  }
  const rows = result?.rows ?? []
  const visibleProducts=catalogue.filter(row=>`${row.sku} ${row.name}`.toLocaleLowerCase('ru').includes(productSearch.toLocaleLowerCase('ru')))
  const productPageCount=Math.max(1,Math.ceil(visibleProducts.length/100))
  const effectiveProductPage=Math.min(productPage,productPageCount-1)
  const filtered = rows.filter(row => Object.values(row).some(value => sellerText(value).toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru'))))
  const statuses = new Map<string, number>()
  for (const row of rows) { const status = sellerText(row.turnover_grade ?? row.idc_grade); statuses.set(status, (statuses.get(status) ?? 0) + 1) }
  const atRisk = rows.filter(sellerRowNeedsAttention).length
  const missing = rows.filter(row => row.turnover_grade == null && row.idc_grade == null).length
  const availableField = result?.operation === 'ANALYTICS_WAREHOUSES' ? 'free_to_sell_amount' : result?.operation === 'ANALYTICS_TURNOVER' ? 'current_stock' : 'available_stock_count'
  const bars = rows.filter(row => row[availableField] !== undefined && row[availableField] !== null && Number.isFinite(Number(row[availableField]))).slice(0, 12)
  const maximum = Math.max(1, ...bars.map(row => Math.abs(Number(row[availableField]))))
  return <section className="seller-workspace" aria-label="Аналитические отчёты">
    <p className="context-note">Остатки, ликвидность и оборачиваемость FBO. Эти отчёты не отражают прибыль или дату банковской выплаты.</p>
    <nav className="subnav seller-tabs" aria-label="Аналитические отчёты">{(Object.keys(titles) as Report[]).map(value => <Button key={value} aria-pressed={report===value} variant={report === value ? 'solid' : 'ghost'} disabled={busy} onClick={() => { setReport(value); setResult(null); setError(''); setSearch('');setProgress('') }}>{titles[value]}</Button>)}</nav>
    {report==='ANALYTICS_STOCKS'&&<section className="analytics-product-picker" aria-label="Выбор товаров для аналитики"><div className="seller-toolbar"><label>Найти товар<input type="search" value={productSearch} disabled={busy} onChange={event=>{setProductSearch(event.target.value);setProductPage(0)}} /></label><span>{selectedSkus.size} / {catalogue.length} SKU выбрано</span><Button variant="soft" disabled={busy||catalogueBusy||!catalogue.length} onClick={()=>setSelectedSkus(new Set(catalogue.map(row=>row.sku)))}>{catalogueComplete?'Выбрать всё':'Выбрать все загруженные'}</Button><Button variant="ghost" disabled={busy||catalogueBusy} onClick={()=>setSelectedSkus(new Set())}>Снять выбор</Button></div>
      {catalogueBusy&&<p role="status">Загружаются все товары · получено {catalogue.length} SKU…</p>}{catalogueError&&<><Callout.Root color="orange"><Callout.Text role="alert">{catalogueError}</Callout.Text></Callout.Root><Button type="button" variant="soft" disabled={busy||catalogueBusy} onClick={()=>setCatalogueGeneration(value=>value+1)}>Обновить каталог</Button></>}
      <ScrollTable tableId="analytics-catalogue" className="analytics-product-table compact-table" aria-label="Аналитика · Выбор товаров" tabIndex={0}><table><thead><tr><th>Выбрать</th>{<th data-table-filter-key="sku">SKU</th>}{<th data-table-filter-key="name">Название</th>}</tr></thead><tbody>{visibleProducts.slice(effectiveProductPage*100,(effectiveProductPage+1)*100).map(row=><tr key={row.sku}><td><input type="checkbox" disabled={busy||catalogueBusy} checked={selectedSkus.has(row.sku)} aria-label={`Выбрать SKU ${row.sku}`} onChange={event=>setSelectedSkus(previous=>{const next=new Set(previous);if(event.target.checked)next.add(row.sku);else next.delete(row.sku);return next})}/></td>{<td data-table-filter-key="sku">{row.sku}</td>}{<td data-table-filter-key="name">{row.name}</td>}</tr>)}</tbody></table></ScrollTable>
      {productPageCount>1&&<div className="seller-toolbar"><Button type="button" variant="ghost" disabled={effectiveProductPage===0} onClick={()=>setProductPage(effectiveProductPage-1)}>Назад</Button><span>Страница {effectiveProductPage+1} / {productPageCount} · {visibleProducts.length} найдено</span><Button type="button" variant="ghost" disabled={effectiveProductPage===productPageCount-1} onClick={()=>setProductPage(effectiveProductPage+1)}>Далее</Button></div>}
    </section>}
    <form className="seller-controls" onSubmit={event => { event.preventDefault(); void load() }}>
      {report === 'ANALYTICS_WAREHOUSES' && <label>Склады<select value={warehouseType} onChange={event => setWarehouseType(event.target.value)}><option value="ALL">Все склады Ozon</option><option value="EXPRESS_DARK_STORE">С доставкой Fresh</option><option value="NOT_EXPRESS_DARK_STORE">Без доставки Fresh</option></select></label>}
      {report === 'ANALYTICS_STOCKS' && <details className="seller-filter-details"><summary>Склады, кластеры и статусы</summary><div className="seller-filter-grid"><label>ID складов<textarea value={warehouses} onChange={event => setWarehouses(event.target.value)} /></label><label>Уровень кластеров<select value={clusterType} onChange={event => setClusterType(event.target.value)}><option value="cluster_ids">Кластеры</option><option value="macrolocal_cluster_ids">Макролокальные кластеры</option></select></label><label>ID кластеров<textarea value={clusters} onChange={event => setClusters(event.target.value)} /></label><MultiFilter label="Ликвидность" values={grades} selected={gradeFilter} onChange={setGradeFilter} /><MultiFilter label="Теги товаров" values={tags} selected={tagFilter} onChange={setTagFilter} /><MultiFilter label="Зоны размещения" values={zones} selected={zoneFilter} onChange={setZoneFilter} /><label className="seller-checkbox"><input type="checkbox" checked={unmarked} onChange={event => setUnmarked(event.target.checked)} />Только товары, требующие маркировки</label></div></details>}
      <div className="refresh-control"><Button type="submit" disabled={busy || delayed || report==='ANALYTICS_STOCKS'&&(catalogueBusy||!selectedSkus.size)}>{busy ? 'Загрузка…' : delayed ? `Доступно через ${Math.ceil((turnoverAvailable - clock) / 1000)} сек.` : result?'Обновить':'Загрузить отчёт'}</Button>{result&&loadedAt&&<span>Обновлено: {formatDateTime(loadedAt)}</span>}</div>{busy&&<Button type="button" variant="soft" onClick={()=>loadingController.current?.abort()}>Остановить загрузку</Button>}
    </form>
    {report === 'ANALYTICS_WAREHOUSES' && <p className="context-note">Предыдущий отчёт Ozon планирует отключить. Для новых проверок используйте «Остатки и ликвидность». Срок отключения не указан.</p>}
    {report === 'ANALYTICS_TURNOVER' && <p className="context-note">Все товары, все страницы. Ozon разрешает один запрос в минуту: следующие страницы загружаются автоматически с этим интервалом.</p>}
    {progress&&<p className="analytics-progress" role="status">{progress}</p>}
    {report === 'ANALYTICS_STOCKS' && <p className="context-note">Текущие остатки. Среднесуточные продажи — за последние 28 дней. SKU не заменяется ID товара или артикулом.</p>}
    {mode === 'MOCK' && <p className="context-note">Показаны синтетические данные.</p>}
    {error && <Callout.Root color="red"><Callout.Text role="alert">{error}</Callout.Text></Callout.Root>}
    {result && <><div className="result-meta"><span>{result.complete ? 'Данные загружены' : 'Загружена часть данных'}</span></div>{result.warnings.map(warning => <Callout.Root key={warning} color="orange"><Callout.Text>{warning}</Callout.Text></Callout.Root>)}
      <dl className="seller-metrics"><div><dt>Строк отчёта</dt><dd>{rows.length}</dd></div>{result.operation !== 'ANALYTICS_WAREHOUSES' && <><div><dt>Требуют внимания по статусу</dt><dd>{atRisk}</dd></div><div><dt>Без статуса в ответе</dt><dd>{missing}</dd></div></>}</dl>
      {!!rows.length && <div className="seller-chart-grid"><section className="seller-chart"><h2>Доступные остатки</h2><p className="context-note">Первые {bars.length} строк текущей выборки, шт. Полные значения — в таблице.</p>{bars.length ? <ol className="seller-bars">{bars.map((row, index) => <li key={index}><span>{sellerText(row.offer_id ?? row.item_code ?? row.sku)}{row.warehouse_name ? ` · ${sellerText(row.warehouse_name)}` : ''}</span><svg viewBox="0 0 240 12" aria-hidden="true" preserveAspectRatio="none"><rect width={240 * Math.abs(Number(row[availableField])) / maximum} height="12" rx="3" /></svg><strong>{sellerText(row[availableField])}</strong></li>)}</ol> : <p>Нет значений доступного остатка в ответе.</p>}</section>{result.operation !== 'ANALYTICS_WAREHOUSES' && <section className="seller-chart"><h2>Статусы ликвидности</h2><ol className="seller-bars">{[...statuses].map(([label, count]) => <li key={label}><span>{label}</span><svg viewBox="0 0 240 12" aria-hidden="true" preserveAspectRatio="none"><rect width={rows.length ? 240 * count / rows.length : 0} height="12" rx="3" /></svg><strong>{count} строк</strong></li>)}</ol><p className="context-note">Один товар может присутствовать в нескольких складах или кластерах. Счётчики относятся к строкам.</p></section>}</div>}
      <div className="seller-toolbar"><label>Найти в отчёте<input type="search" value={search} onChange={event => setSearch(event.target.value)} /></label><span>{filtered.length} / {rows.length} строк</span></div><SellerDataTable rows={filtered} label={titles[result.operation as Report]} />{result.next_request && !busy && <Button variant="soft" disabled={delayed} onClick={() => void load(result.next_request!)}>Продолжить загрузку всех товаров</Button>}
    </>}
  </section>
}
