import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Callout, Dialog } from '@radix-ui/themes'
import {formatPrice,getCatalog,type RuntimeMode} from './api'
import type { SellerRecord, SellerValue } from './sellerApi'
import { sellerRecord } from './sellerApi'
import { warehouseContracts, warehouseRead } from './warehouseReportsApi'
import type { WarehouseContracts, WarehouseReadResult } from './warehouseReportsApi'
import './product-catalogue.css'

const pageSize = 40
const text = (value: SellerValue | undefined) => value === undefined || value === null ? '' : String(value)
const productId = (row: SellerRecord) => text(row.product_id ?? row.id)
const message = (error: unknown) => error instanceof Error ? error.message : 'Не удалось загрузить данные'
type BoostState={loading:boolean;active:boolean|null;values:{action:string;percent:string|null}[]}
type DetailSection = { operation: string; title: string; loading: boolean; result?: WarehouseReadResult; error?: string }

function imageUrls(value: SellerValue | undefined): string[] {
  if (typeof value === 'string') {
    try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? [url.href] : [] } catch { return [] }
  }
  return Array.isArray(value) ? value.flatMap(imageUrls) : []
}
function productImages(row: SellerRecord): string[] {
  return [...new Set(['primary_image', 'primary_photo', 'images', 'photo', 'color_image', 'color_photo'].flatMap(key => imageUrls(row[key])))]
}
function resultImages(result: WarehouseReadResult): string[] {
  const visit = (value: SellerValue): string[] => Array.isArray(value) ? value.flatMap(visit) : value && typeof value === 'object' ? [...productImages(value), ...Object.values(value).filter(item => item && typeof item === 'object').flatMap(visit)] : []
  return [...new Set(visit(result.data))]
}
function ProductImage({ url, name, large = false }: { url?: string; name: string; large?: boolean }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [url])
  return <span className={`catalogue-image${large ? ' is-large' : ''}`}>{url && !failed ? <img src={url} alt={name} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} /> : <span aria-label="Изображение отсутствует">Нет фото</span>}</span>
}
function plainDescription(value: string): string {
  return value.replace(/<br\s*\/?\s*>/gi, '\n').replace(/<\/(p|div|li)>/gi, '\n').replace(/<[^>]*>/g, '').replace(/&(amp|lt|gt|quot|apos|nbsp|#\d+|#x[\da-f]+);/gi, (match, entity: string) => {
    const named: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' }
    const replacement = named[entity.toLowerCase()]
    if (replacement !== undefined) return replacement
    const point = entity[1]?.toLowerCase() === 'x' ? parseInt(entity.slice(2), 16) : parseInt(entity.slice(1), 10)
    return Number.isInteger(point) && point > 0 && point <= 0x10ffff && !(point >= 0xd800 && point <= 0xdfff) ? String.fromCodePoint(point) : match
  }).trim()
}
function statusLabel(row: SellerRecord): string {
  if (row.archived || row.is_archived || row.is_autoarchived) return 'В архиве'
  const status = sellerRecord(row.statuses)
  return text(status.status_name ?? status.status_description ?? status.status) || 'Статус не указан'
}
function skuLabel(row: SellerRecord): string {
  const sources = Array.isArray(row.sources) ? row.sources.map(sellerRecord).map(source => text(source.sku)).filter(Boolean) : []
  return text(row.sku) || [...new Set(sources)].join(', ') || '—'
}

export function ProductCatalogue({ mode, scopeKey }: { mode: RuntimeMode; scopeKey: string }) {
  const [contracts, setContracts] = useState<WarehouseContracts | null>(null)
  const [items, setItems] = useState<SellerRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [stage, setStage] = useState('')
  const [warnings, setWarnings] = useState<string[]>([])
  const [complete, setComplete] = useState(false)
  const [listComplete, setListComplete] = useState(false)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<SellerRecord | null>(null)
  const [sections, setSections] = useState<DetailSection[]>([])
  const generation = useRef(0)
  const detailGeneration = useRef(0)
  const boostAbort=useRef<AbortController|null>(null)
  const [boost,setBoost]=useState<BoostState>({loading:false,active:null,values:[]})

  async function load() {
    const token = ++generation.current
    ++detailGeneration.current
    boostAbort.current?.abort()
    setSelected(null); setSections([]); setLoading(true); setComplete(false); setListComplete(false); setError(''); setWarnings([]); setItems([]); setPage(0)
    const current = () => token === generation.current
    const catalogue = new Map<string, SellerRecord>()
    const notices = new Set<string>()
    try {
      const metadata = await warehouseContracts()
      if (!current()) return
      setContracts(metadata)
      if (!metadata.operations.PRODUCT_LIST?.read || !metadata.operations.PRODUCT_INFO?.read) throw new Error('Чтение каталога недоступно в текущем контракте')
      for (const visibility of ['ALL', 'ARCHIVED']) {
        let request: SellerRecord = { filter: { visibility }, limit: 1000, last_id: '' }
        const cursors = new Set<string>()
        const seenProducts = new Set<string>()
        while (current()) {
          setStage(visibility === 'ARCHIVED' ? 'Загружаем архивные товары…' : 'Загружаем список товаров…')
          const result = await warehouseRead('PRODUCT_LIST', request)
          if (!current()) return
          result.warnings.forEach(warning => notices.add(warning))
          let added = 0
          for (const row of result.rows) {
            const id = productId(row)
            if (!id) { notices.add('В ответе есть товар без точного ID'); continue }
            if (seenProducts.has(id)) notices.add('Ozon вернул повторяющиеся товары; повторные строки объединены')
            else { seenProducts.add(id); added++ }
            catalogue.set(id, { ...catalogue.get(id), ...row, ...(visibility === 'ARCHIVED' ? { archived: true } : {}) })
          }
          setItems([...catalogue.values()])
          if (!result.next_request) { if (!result.complete) throw new Error('Ozon вернул неполный список без следующей страницы'); break }
          if (!added) throw new Error('Следующая страница не содержит новых товаров. Полнота каталога не подтверждена')
          const cursor = JSON.stringify(result.next_request)
          if (cursors.has(cursor) || cursor === JSON.stringify(request)) throw new Error('Следующая страница каталога повторяется. Загружена только часть товаров')
          cursors.add(cursor); request = result.next_request
        }
      }
      if (!current()) return
      setListComplete(true)
      const ids = [...catalogue.keys()]
      for (let offset = 0; offset < ids.length && current(); offset += 100) {
        setStage(`Загружаем карточки: ${Math.min(offset + 100, ids.length)} из ${ids.length}…`)
        const batch = ids.slice(offset, offset + 100)
        try {
          const result = await warehouseRead('PRODUCT_INFO', { product_id: batch })
          if (!current()) return
          result.warnings.forEach(warning => notices.add(warning))
          if (!result.complete) notices.add('Часть основных данных товаров не получена')
          const received = new Set<string>()
          for (const row of result.rows) { const id = productId(row); if (catalogue.has(id)) { catalogue.set(id, { ...catalogue.get(id), ...row }); received.add(id) } }
          if (batch.some(id => !received.has(id))) notices.add('Для некоторых товаров Ozon не вернул основную карточку')
        } catch (failure) { if (!current()) return; notices.add(`Основные карточки: ${message(failure)}`) }
        setItems([...catalogue.values()]); setWarnings([...notices])
      }
      if (current()) { setComplete(notices.size === 0); setWarnings([...notices]); setStage('') }
    } catch (failure) { if (current()) { setError(message(failure)); setWarnings([...notices]) } }
    finally { if (current()) setLoading(false) }
  }
  useEffect(() => { void load(); return () => { ++generation.current; ++detailGeneration.current;boostAbort.current?.abort() } }, [mode, scopeKey])

  function cancel() { ++generation.current; setLoading(false); setStage(''); setComplete(false); setWarnings(previous => [...previous, 'Загрузка остановлена. Показана только загруженная часть каталога']) }
  function close() { boostAbort.current?.abort(); ++detailGeneration.current; setSelected(null); setSections([]) }
  function open(row: SellerRecord) {
    const token = ++detailGeneration.current
    setSelected(row)
    boostAbort.current?.abort()
    const controller=new AbortController();boostAbort.current=controller
    setBoost({loading:true,active:null,values:[]})
    void (async()=>{
      try {
        const actions=await getCatalog('ready',{scope:'actions'},controller.signal)
        let known=actions.complete&&!actions.actions.some(a=>a.status==='unknown')
        const values:BoostState['values']=[]
        for(const action of actions.actions.filter(a=>a.status==='active'&&!a.auto_add)){
          const result=await getCatalog('ready',{scope:'products',action_id:action.id,role:'member',auto_add:false},controller.signal)
          known=known&&result.complete
          for(const item of result.products.filter(item=>item.product_id===productId(row))){
            if(item.elastic===true)values.push({action:action.name,percent:item.current_boost??null})
            else if(item.current_boost!=null&&Number(item.current_boost)>0)known=false
          }
        }
        if(token===detailGeneration.current)setBoost({loading:false,active:values.length?true:known?false:null,values})
      }catch{if(token===detailGeneration.current)setBoost({loading:false,active:null,values:[]})}
    })()
    const id = productId(row)
    const offer = text(row.offer_id)
    const numericId = Number(id)
    const visibility = row.archived || row.is_archived || row.is_autoarchived ? 'ARCHIVED' : 'ALL'
    const descriptionRequest: SellerRecord | null = offer ? { offer_id: offer } : Number.isSafeInteger(numericId) && numericId > 0 ? { product_id: numericId } : null
    const requests: { operation: string; title: string; request: SellerRecord | null; unavailable?: string }[] = [
      { operation: 'PRODUCT_INFO', title: 'Основные данные', request: { product_id: [id] } },
      { operation: 'PRODUCT_DESCRIPTION', title: 'Описание', request: descriptionRequest },
      { operation: 'PRODUCT_PICTURES', title: 'Фотографии', request: { product_id: [id] } },
      { operation: 'PRODUCT_PRICES', title: 'Цены и комиссии', request: { filter: { product_id: [id], visibility }, limit: 100, cursor: '' } },
    ]
    setSections(requests.map(({ operation, title }) => ({ operation, title, loading: true })))
    for (const job of requests) {
      const update = (section: DetailSection) => { if (token === detailGeneration.current) setSections(previous => previous.map(item => item.operation === job.operation ? section : item)) }
      if (!contracts?.operations[job.operation]?.read || !job.request) { update({ operation: job.operation, title: job.title, loading: false, error: job.request ? 'Чтение недоступно в текущем контракте' : job.unavailable ?? 'Нет точного идентификатора для чтения описания' }); continue }
      void warehouseRead(job.operation, job.request).then(result => update({ operation: job.operation, title: job.title, loading: false, result })).catch(failure => update({ operation: job.operation, title: job.title, loading: false, error: message(failure) }))
    }
  }
  const filtered = useMemo(() => { const query = search.trim().toLocaleLowerCase('ru'); return query ? items.filter(row => [row.name, row.offer_id, productId(row), skuLabel(row)].some(value => text(value).toLocaleLowerCase('ru').includes(query))) : items }, [items, search])
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const activePage = Math.min(page, pages - 1)
  const visible = filtered.slice(activePage * pageSize, (activePage + 1) * pageSize)
  const gallery = [...new Set(sections.flatMap(section => section.result && ['PRODUCT_INFO', 'PRODUCT_PICTURES'].includes(section.operation) ? resultImages(section.result) : []))]
  const descriptionSection = sections.find(section => section.operation === 'PRODUCT_DESCRIPTION')
  const description = descriptionSection?.result ? text(sellerRecord(descriptionSection.result.data.result).description ?? descriptionSection.result.rows[0]?.description) : ''

  const priceSection=sections.find(section=>section.operation==='PRODUCT_PRICES')
  const priceRow=priceSection?.result?.rows.find(row=>productId(row)===productId(selected??{}))
  const prices=sellerRecord(priceRow?.price)
  const priceCurrency=text(prices.currency_code)||text(selected?.currency_code)||null
  const priceValue=(key:string)=>{const raw=prices[key]??selected?.[key];return typeof raw==='string'||typeof raw==='number'?formatPrice(String(raw),priceCurrency):'Нет данных'}

  return <section className="product-catalogue" aria-label="Каталог товаров">
    <div className="catalogue-toolbar"><div><h2>Товары</h2><p className="warehouse-note">Все товары, включая архив. Откройте товар, чтобы увидеть фотографии, описание и данные Ozon.</p></div><div className="catalogue-actions"><Button variant="soft" disabled={loading} onClick={() => void load()}>Обновить товары</Button>{loading && <Button variant="soft" color="gray" onClick={cancel}>Остановить загрузку</Button>}</div></div>
    <div className="catalogue-search"><label htmlFor="catalogue-search">Найти товар<input id="catalogue-search" type="search" placeholder="Название, артикул, SKU или ID" value={search} onChange={event => { setSearch(event.target.value); setPage(0) }} /></label><p role="status" aria-live="polite">{loading ? stage : complete ? `Каталог загружен: ${items.length} товаров` : listComplete ? `Список загружен: ${items.length} товаров · карточки частично` : `Загружено: ${items.length} товаров`}{search && ` · найдено: ${filtered.length}`}</p></div>
    {error && <Callout.Root color="red"><Callout.Text>{error}. Уже полученные товары доступны ниже.</Callout.Text></Callout.Root>}
    {warnings.length > 0 && <Callout.Root color="amber"><Callout.Text>{warnings.join(' · ')}</Callout.Text></Callout.Root>}
    {!loading && !error && !filtered.length && <p className="catalogue-empty">{search ? 'По этому запросу товаров нет.' : 'В каталоге пока нет товаров.'}</p>}
    {visible.length > 0 && <div className="catalogue-list"><table><thead><tr><th>Фото</th><th>Товар</th><th>SKU</th><th>Статус</th></tr></thead><tbody>{visible.map(row => <tr key={productId(row)}><td><div className="catalogue-photo-action"><ProductImage url={productImages(row)[0]} name={text(row.name) || 'Товар'} /><Button size="1" variant="soft" onClick={() => open(row)}>Открыть</Button></div></td><td><button type="button" className="catalogue-name" onClick={() => open(row)}>{text(row.name) || 'Название не получено'}</button><small>ID товара: {productId(row)}</small><small>Артикул: {text(row.offer_id) || '—'}</small></td><td>{skuLabel(row)}</td><td>{statusLabel(row)}</td></tr>)}</tbody></table></div>}
    {filtered.length > pageSize && <nav className="catalogue-pagination" aria-label="Страницы товаров"><Button variant="soft" disabled={activePage === 0} onClick={() => setPage(activePage - 1)}>Назад</Button><span>Страница {activePage + 1} из {pages}</span><Button variant="soft" disabled={activePage >= pages - 1} onClick={() => setPage(activePage + 1)}>Далее</Button></nav>}
    <Dialog.Root open={Boolean(selected)} onOpenChange={value => { if (!value) close() }}><Dialog.Content className="catalogue-dialog" maxWidth="1080px"><div className="catalogue-dialog-heading"><div><Dialog.Title>{text(selected?.name) || 'Карточка товара'}</Dialog.Title><Dialog.Description>Артикул: {text(selected?.offer_id) || '—'} · ID: {selected ? productId(selected) : '—'} · SKU: {selected ? skuLabel(selected) : '—'}</Dialog.Description></div><Dialog.Close><Button variant="soft">Закрыть</Button></Dialog.Close></div>
      {selected && <>
        <div className="catalogue-product-overview"><div className="catalogue-gallery" aria-label="Фотографии товара">{(gallery.length?gallery:productImages(selected)).length?(gallery.length?gallery:productImages(selected)).map(url=><ProductImage key={url} url={url} name={text(selected.name)||'Товар'} large/>):<ProductImage name="Товар" large/>}</div><div className="catalogue-price-panel"><dl>{[['price','Цена'],['old_price','Зачёркнутая цена'],['min_price','Минимальная цена']].map(([key,label])=><div key={key}><dt>{label}</dt><dd>{priceSection?.loading?'Загрузка…':priceValue(key!)}</dd></div>)}</dl><div className="catalogue-boost"><span>Эластичный бустинг</span>{boost.loading?<strong>Загрузка…</strong>:boost.active===false?<strong>Нет</strong>:boost.active===null?<strong>Нет данных</strong>:boost.values.map((entry,index)=>{const percent=entry.percent===null?null:Number(entry.percent);const valid=percent!==null&&Number.isFinite(percent)&&percent>=0&&percent<=100;return <div className="catalogue-boost-entry" key={index}>{boost.values.length>1&&<span>{entry.action}</span>}<strong>{entry.percent===null?'Активен · процент не предоставлен':`${entry.percent.replace('.',',')} %`}</strong>{valid&&<><div className="catalogue-boost-meter" role="progressbar" aria-label="Эластичный бустинг" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent!}><span style={{width:`${percent}%`}}/></div><div className="catalogue-boost-scale"><span>0 %</span><span>100 %</span></div></>}</div>})}</div>{priceSection?.error&&<p role="alert">Не удалось обновить цены.</p>}</div></div>
        <section className="catalogue-description"><h3>Описание товара</h3>{description?<p>{plainDescription(description)}</p>:<p>{descriptionSection?.loading?'Загрузка…':descriptionSection?.error?'Не удалось загрузить описание.':'Описание не предоставлено.'}</p>}</section>
      </>}
    </Dialog.Content></Dialog.Root>
  </section>
}
