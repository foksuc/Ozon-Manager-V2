import { useEffect, useRef, useState } from 'react'
import { Button, Callout, Dialog } from '@radix-ui/themes'
import type { RuntimeMode } from './api'
import { ScrollTable } from './TableTools'
import { sellerIds, sellerLabel, sellerRead, sellerRecord, sellerText, strategyExecute, strategyPreview, strategyReconcile, strategyHistory } from './sellerApi'
import type { SellerReadOperation, SellerReadResult, SellerRecord, SellerValue, StrategyOperation, StrategyPlan, StrategyResult, StrategyHistory, StrategyHistoryItem } from './sellerApi'
import './seller-workspaces.css'
import {VendorErrorNotice} from './VendorErrorNotice'

function StrategyErrors({result}:{result:StrategyResult}){
  return <><VendorErrorNotice error={result.transport_error}/>{result.items.flatMap(item=>item.errors??[]).map((error,index)=><VendorErrorNotice key={index} error={error}/>)}</>
}

const operationNames: Record<StrategyOperation, string> = { STRATEGY_CREATE: 'Создать стратегию', STRATEGY_UPDATE: 'Изменить стратегию', STRATEGY_ADD_PRODUCTS: 'Добавить товары', STRATEGY_REMOVE_PRODUCTS: 'Убрать товары из стратегий', STRATEGY_STATUS: 'Изменить статус', STRATEGY_DELETE: 'Удалить стратегию' }

export function SellerDataTable({ rows, label, onSelect }: { rows: SellerRecord[]; label: string; onSelect?: (row: SellerRecord) => void }) {
  const keys = [...new Set(rows.flatMap(row => Object.keys(row)))]
  const prioritized = ['sku', 'product_id', 'offer_id', 'name', 'item_name', 'warehouse_name', 'cluster_name', 'enabled', 'turnover_grade', 'available_stock_count', 'free_to_sell_amount', 'current_stock', 'idc']
  keys.sort((a, b) => (prioritized.indexOf(a) < 0 ? 100 : prioritized.indexOf(a)) - (prioritized.indexOf(b) < 0 ? 100 : prioritized.indexOf(b)))
  return <ScrollTable className="compact-table seller-table" aria-label={label} tabIndex={0}><table><thead><tr>{onSelect && <th>Открыть</th>}{keys.map(key => <th key={key} data-table-filter-key={key}>{sellerLabel(key)}</th>)}</tr></thead><tbody>{rows.length?rows.map((row, index) => <tr key={`${sellerText(row.id ?? row.sku ?? row.product_id)}:${index}`}>{onSelect && <td><Button size="1" variant="soft" onClick={() => onSelect(row)}>Выбрать</Button></td>}{keys.map(key => <td key={key} data-table-filter-key={key}>{key.endsWith('_url') && typeof row[key] === 'string' && /^https:\/\//.test(row[key]) ? <a href={row[key]} target="_blank" rel="noreferrer">Открыть товар</a> : sellerText(row[key])}</td>)}</tr>):<tr><td colSpan={Math.max(1,keys.length+(onSelect?1:0))}>Данных нет</td></tr>}</tbody></table></ScrollTable>
}

function Summary({ value }: { value: SellerValue | undefined }) {
  const record = sellerRecord(value)
  return <dl className="seller-definition">{Object.entries(record).map(([key, item]) => <div key={key}><dt>{sellerLabel(key)}</dt><dd>{sellerText(item)}</dd></div>)}</dl>
}

export function StrategyWorkspace({ mode, scopeKey }: { mode: RuntimeMode; scopeKey: string }) {
  const [list, setList] = useState<SellerReadResult | null>(null)
  const [competitors, setCompetitors] = useState<SellerRecord[]>([])
  const [competitorNext, setCompetitorNext] = useState<SellerRecord | null>(null)
  const [selected, setSelected] = useState<SellerRecord | null>(null)
  const [info, setInfo] = useState<SellerRecord | null>(null)
  const [goods, setGoods] = useState<SellerReadResult | null>(null)
  const [lookup, setLookup] = useState<SellerReadResult | null>(null)
  const [ids, setIds] = useState('')
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [editor, setEditor] = useState<StrategyOperation | null>(null)
  const [name, setName] = useState('')
  const [coefficients, setCoefficients] = useState<Record<string, string>>({})
  const [plan, setPlan] = useState<StrategyPlan | null>(null)
  const [result, setResult] = useState<StrategyResult | null>(null)
  const [planTime, setPlanTime] = useState(0)
  const [clock, setClock] = useState(Date.now())
  const [executionStarted, setExecutionStarted] = useState(false)
  const [history, setHistory] = useState<StrategyHistory | null>(null)
  const context = useRef(0)
  const activeScope = useRef(scopeKey)
  activeScope.current = scopeKey
  useEffect(() => { const timer = window.setInterval(() => setClock(Date.now()), 1000); return () => window.clearInterval(timer) }, [])
  useEffect(() => {
    const revision = ++context.current
    const controller = new AbortController()
    setList(null); setCompetitors([]); setCompetitorNext(null); setSelected(null); setInfo(null); setGoods(null); setLookup(null); setPlan(null); setResult(null); setHistory(null); setEditor(null); setExecutionStarted(false); setError(''); setIds(''); setSearch(''); setName(''); setCoefficients({}); setBusy(true)
    Promise.allSettled([sellerRead('STRATEGY_LIST', { page: 1, limit: 20 }, controller.signal), sellerRead('STRATEGY_COMPETITORS', { page: 1, limit: 20 }, controller.signal), strategyHistory(controller.signal)]).then(([strategies, rivals, historyResponse]) => {
      if (revision !== context.current) return
      if (strategies.status === 'fulfilled') setList(strategies.value)
      if (rivals.status === 'fulfilled') { setCompetitors(rivals.value.rows); setCompetitorNext(rivals.value.next_request) }
      if (historyResponse.status === 'fulfilled') setHistory(historyResponse.value)
      const failures = [strategies, rivals, historyResponse].filter(value => value.status === 'rejected').map(value => value.status === 'rejected' && value.reason instanceof Error ? value.reason.message : 'Не удалось загрузить данные.')
      if (failures.length) setError(failures.join(' '))
    }).catch(err => { if (revision === context.current && !controller.signal.aborted) setError(err instanceof Error ? err.message : 'Не удалось загрузить стратегии.') }).finally(() => { if (revision === context.current) setBusy(false) })
    return () => { controller.abort(); context.current++ }
  }, [mode, scopeKey])
  async function run(work: () => Promise<void>) {
    const revision = context.current
    setBusy(true); setError('')
    try { await work() } catch (err) { if (revision === context.current) setError(err instanceof Error ? err.message : 'Не удалось выполнить действие.') } finally { if (revision === context.current) setBusy(false) }
  }
  async function read(operation: SellerReadOperation, request: SellerRecord, apply: (value: SellerReadResult) => void) {
    const revision = context.current
    const response = await sellerRead(operation, request)
    if (revision === context.current) apply(response)
  }
  const strategyId = typeof selected?.id === 'string' ? selected.id : selected?.id === undefined ? '' : String(selected.id)
  const system = selected?.type === 'MIN_EXT_PRICE' || info?.type === 'MIN_EXT_PRICE'
  const current = info ?? selected
  const currentEnabled = current?.enabled === true
  const visibleRows = list?.rows.filter(row => sellerText(row.name).toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru'))) ?? []
  function openEditor(operation: StrategyOperation) {
    setEditor(operation); setPlan(null); setResult(null); setExecutionStarted(false); setError(''); setName(typeof current?.name === 'string' ? current.name : '')
    const configured = Array.isArray(info?.competitors) ? info.competitors : []
    setCoefficients(Object.fromEntries(configured.map(value => { const row = sellerRecord(value); return [String(row.competitor_id), String(row.coefficient ?? '1')] })))
  }
  async function prepare() {
    if (!editor) return
    const revision = context.current
    let request: SellerRecord = {}
    if (editor === 'STRATEGY_CREATE' || editor === 'STRATEGY_UPDATE') {
      if (!name.trim()) throw new Error('Введите название стратегии.')
      const choices = Object.entries(coefficients).map(([id, coefficient]) => {
        const normalized = coefficient.trim().replace(',', '.')
        const number = Number(normalized)
        if (!/^\d+(?:\.\d+)?$/.test(normalized) || !Number.isFinite(number) || number < .5 || number > 1.2 || !Number.isSafeInteger(Number(id))) throw new Error('Коэффициент конкурента должен быть от 0,5 до 1,2.')
        return { competitor_id: Number(id), coefficient: normalized }
      })
      if (!choices.length) throw new Error('Выберите хотя бы одного конкурента.')
      request = { strategy_name: name.trim(), competitors: choices }
      if (editor === 'STRATEGY_UPDATE') request.strategy_id = strategyId
    } else if (editor === 'STRATEGY_ADD_PRODUCTS') request = { strategy_id: strategyId, product_id: sellerIds(ids, 50) }
    else if (editor === 'STRATEGY_REMOVE_PRODUCTS') request = { product_id: sellerIds(ids, 50) }
    else if (editor === 'STRATEGY_STATUS') request = { strategy_id: strategyId, enabled: !currentEnabled }
    else request = { strategy_id: strategyId }
    const response = await strategyPreview(editor, request)
    if (revision === context.current) { setPlan(response); setPlanTime(Date.now()); setResult(null) }
  }
  async function execute() {
    if (!plan || executionStarted || !plan.valid || clock >= planTime + plan.expires_in * 1000) return
    const revision = context.current
    const scope = activeScope.current
    setExecutionStarted(true)
    let outcome: StrategyResult
    try { outcome = await strategyExecute(plan) } catch { outcome = { status: 'UNKNOWN_RESULT', success_count: 0, error_count: 0, items: [], message: 'Ответ не получен. Проверьте состояние стратегии перед новой операцией.' } }
    if (revision !== context.current || scope !== activeScope.current) return
    setResult(outcome)
    if (outcome.status === 'SUCCESS' || outcome.status === 'PARTIAL') {
      await read('STRATEGY_LIST', { page: 1, limit: 20 }, value => {
        setList(value)
        const updated = value.rows.find(row => String(row.id) === strategyId)
        setSelected(updated ?? null); setInfo(null); setGoods(null)
      })
      if (strategyId && plan.operation !== 'STRATEGY_DELETE') await read('STRATEGY_INFO', { strategy_id: strategyId }, value => setInfo(sellerRecord(value.data.result ?? value.data)))
    }
    const updatedHistory = await strategyHistory()
    if (revision === context.current) setHistory(updatedHistory)
  }
  async function reconcile() {
    if (!plan) return
    const revision = context.current
    const response = await strategyReconcile(plan)
    if (revision === context.current) setResult(response)
    const updatedHistory = await strategyHistory()
    if (revision === context.current) setHistory(updatedHistory)
  }
  async function reconcileHistory(item: StrategyHistoryItem) {
    const revision = context.current
    await strategyReconcile(item)
    const updated = await strategyHistory()
    if (revision === context.current) setHistory(updated)
  }
  return <section className="seller-workspace" aria-label="Стратегии ценообразования">
    <div className="seller-intro"><p>Ценообразование по ценам конкурентов. Сначала настройте стратегию, затем добавьте товары.</p><Button disabled={busy} onClick={() => { setSelected(null); setInfo(null); openEditor('STRATEGY_CREATE'); setName(''); setCoefficients({}) }}>Создать стратегию</Button></div>
    {mode === 'MOCK' && <p className="context-note">Синтетические стратегии. Изменения Ozon не отправляются.</p>}
    <div className="seller-toolbar"><label>Найти стратегию<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Название" /></label><Button variant="soft" disabled={busy} onClick={() => void run(() => read('STRATEGY_LIST', { page: 1, limit: 20 }, setList))}>Обновить</Button></div>
    {busy && <p role="status">Загрузка…</p>}{error && <Callout.Root color="red"><Callout.Text role="alert">{error}</Callout.Text></Callout.Root>}
    {list && <><p className="context-note">Загружено: {list.rows.length} · {list.complete ? 'Список полный' : 'Есть следующая страница'}</p>{list.warnings.map(warning => <p role="status" key={warning}>{warning}</p>)}<SellerDataTable rows={visibleRows} label="Стратегии" onSelect={row => { if (busy) return; setSelected(row); setInfo(null); setGoods(null); setLookup(null); setEditor(null); setPlan(null); void run(() => read('STRATEGY_INFO', { strategy_id: String(row.id) }, value => setInfo(sellerRecord(value.data.result ?? value.data)))) }} />{list.next_request && <Button variant="soft" disabled={busy} onClick={() => void run(() => read('STRATEGY_LIST', list.next_request!, value => setList(previous => previous ? { ...value, rows: [...previous.rows, ...value.rows] } : value)))}>Загрузить ещё стратегии</Button>}</>}
    {selected && <section className="seller-detail"><h2>{sellerText(selected.name)}</h2>{info && <Summary value={info} />}<div className="seller-toolbar seller-actions"><Button variant="soft" disabled={busy} onClick={() => void run(() => read('STRATEGY_PRODUCTS', { strategy_id: strategyId }, setGoods))}>Товары стратегии</Button><Button variant="soft" disabled={busy || system} onClick={() => openEditor('STRATEGY_UPDATE')}>Изменить</Button><Button variant="soft" disabled={busy || system} onClick={() => openEditor('STRATEGY_STATUS')}>{currentEnabled ? 'Остановить' : 'Включить'}</Button><Button variant="soft" disabled={busy} onClick={() => openEditor('STRATEGY_ADD_PRODUCTS')}>Добавить товары</Button><Button variant="soft" color="red" disabled={busy || system} onClick={() => openEditor('STRATEGY_DELETE')}>Удалить</Button></div>{system && <p className="context-note">Системную стратегию нельзя изменить, остановить или удалить.</p>}{goods && <SellerDataTable rows={goods.rows} label="Товары стратегии" />}</section>}
    <section className="seller-detail"><h2>Проверка товаров</h2><p className="context-note">Укажите ID товаров Ozon, до 50. Артикул продавца и SKU здесь не подходят.</p><div className="seller-toolbar"><label>ID товаров<textarea value={ids} onChange={event => setIds(event.target.value)} placeholder="Один ID на строку" /></label><Button variant="soft" disabled={busy} onClick={() => void run(() => read('STRATEGY_IDS', { product_id: sellerIds(ids, 50) }, setLookup))}>Найти стратегии</Button><Button variant="soft" disabled={busy} onClick={() => void run(() => { const values = sellerIds(ids, 1); const value = Number(values[0]); if (!Number.isSafeInteger(value)) throw new Error('Этот ID превышает безопасный числовой диапазон запроса.'); return read('STRATEGY_PRODUCT_INFO', { product_id: value }, setLookup) })}>Цена конкурента</Button><Button variant="soft" color="red" disabled={busy} onClick={() => openEditor('STRATEGY_REMOVE_PRODUCTS')}>Убрать из стратегий</Button></div>{lookup && <><SellerDataTable rows={lookup.rows.length ? lookup.rows : [sellerRecord(lookup.data.result ?? lookup.data)]} label="Проверка товаров" />{lookup.operation === 'STRATEGY_PRODUCT_INFO' && <p className="context-note">Цена показана как вернул Ozon; валюта этим методом не подтверждается.</p>}</>}</section>
    {editor && <section className="seller-editor"><h2>{operationNames[editor]}</h2><form onSubmit={event => { event.preventDefault(); void run(prepare) }}>
      {(editor === 'STRATEGY_CREATE' || editor === 'STRATEGY_UPDATE') && <><label>Название<input required value={name} onChange={event => setName(event.target.value)} /></label><fieldset><legend>Конкуренты и коэффициенты</legend><p className="context-note">Цена конкурента умножается на коэффициент от 0,5 до 1,2.</p>{competitors.map(row => { const id = String(row.id); return <div className="seller-rival" key={id}><label><input type="checkbox" checked={id in coefficients} onChange={event => setCoefficients(previous => { const next = { ...previous }; if (event.target.checked) next[id] = '1'; else delete next[id]; return next })} />{sellerText(row.name)}</label>{id in coefficients && <label>Коэффициент<input inputMode="decimal" autoComplete="off" value={coefficients[id]} onChange={event => setCoefficients(previous => ({ ...previous, [id]: event.target.value }))} /></label>}</div> })}{!competitors.length && <p>Конкурентов нет. Обновите данные.</p>}{competitorNext && <Button type="button" variant="soft" disabled={busy} onClick={() => void run(() => read('STRATEGY_COMPETITORS', competitorNext, value => { setCompetitors(previous => [...previous, ...value.rows]); setCompetitorNext(value.next_request) }))}>Ещё конкуренты</Button>}</fieldset></>}
      {(editor === 'STRATEGY_ADD_PRODUCTS' || editor === 'STRATEGY_REMOVE_PRODUCTS') && <label>ID товаров, до 50<textarea required value={ids} onChange={event => setIds(event.target.value)} /></label>}
      {editor === 'STRATEGY_STATUS' && <p>Стратегия будет {currentEnabled ? 'остановлена' : 'включена'}.</p>}{editor === 'STRATEGY_DELETE' && <p>Удалить выбранную пользовательскую стратегию.</p>}
      <div className="seller-toolbar"><Button type="submit" disabled={busy}>Подготовить изменение</Button><Button type="button" variant="soft" disabled={busy} onClick={() => setEditor(null)}>Отмена</Button></div>
    </form></section>}
    <section className="seller-detail"><div className="seller-intro"><h2>История стратегий</h2><Button variant="soft" disabled={busy} onClick={() => void run(async () => { const revision = context.current; const value = await strategyHistory(); if (revision === context.current) setHistory(value) })}>Обновить историю</Button></div><p className="context-note">Подробности операций хранятся локально в зашифрованном виде и доступны после перезапуска.</p>{history?.requires_manual_reconciliation && <Callout.Root color="orange"><Callout.Text>После перезапуска обнаружены незавершённые попытки: {history.recovered.length}. Сверьте стратегии и товары с текущим состоянием Ozon. Снимок операции не восстановлен; повторная отправка недоступна.</Callout.Text></Callout.Root>}{history?.items.length ? <div className="seller-history">{history.items.map((item, index) => <article key={item.plan_id}><span>Операция {index + 1}</span><strong>{item.status === 'SUCCESS' ? 'Успешно' : item.status === 'UNKNOWN_RESULT' ? 'Результат неизвестен' : `Успешно: ${item.success_count}, Ошибки: ${item.error_count}`}</strong><StrategyErrors result={item}/>{item.status === 'UNKNOWN_RESULT' && <Button variant="soft" disabled={busy||item.can_reconcile===false} onClick={() => void run(() => reconcileHistory(item))}>Сверить результат</Button>}</article>)}</div> : <p className="context-note">Сохранённых операций пока нет.</p>}</section>
    <Dialog.Root open={!!plan} onOpenChange={open => { if (!open && !busy) setPlan(null) }}><Dialog.Content className="preview-panel seller-plan" onEscapeKeyDown={event => { if (busy) event.preventDefault() }}><Dialog.Title>{plan ? operationNames[plan.operation] : 'Проверка изменения'}</Dialog.Title><Dialog.Description>Проверьте сохранённые параметры перед подтверждением.</Dialog.Description>{plan && <><h3>Будет отправлено</h3><Summary value={plan.request} />{plan.before && <><h3>Исходное состояние</h3><Summary value={plan.before} /></>}{!result && <p className="context-note">План действителен ещё {Math.min(plan.expires_in, Math.max(0, Math.ceil((planTime + plan.expires_in * 1000 - clock) / 1000)))} сек.</p>}</>}{result && <Callout.Root color={result.status === 'SUCCESS' ? 'green' : 'orange'}><Callout.Text>{result.status === 'SUCCESS' ? 'Успешно' : result.status === 'UNKNOWN_RESULT' ? 'Результат неизвестен. Сверьте состояние стратегии; повторная отправка отключена.' : `Успешно: ${result.success_count}, Ошибки: ${result.error_count}`}</Callout.Text></Callout.Root>}{result&&<StrategyErrors result={result}/>} {result?.items.length ? <SellerDataTable rows={result.items} label="Результаты изменения стратегии" /> : null}{error && <p role="alert">{error}</p>}<div className="dialog-actions">{result?.status === 'UNKNOWN_RESULT' && <Button variant="soft" disabled={busy} onClick={() => void run(reconcile)}>Сверить результат</Button>}<Button variant="soft" disabled={busy} onClick={() => setPlan(null)}>Закрыть</Button><Button disabled={busy || executionStarted || !plan?.valid || clock >= planTime + (plan?.expires_in ?? 0) * 1000} onClick={() => void run(execute)}>Подтвердить изменение</Button></div></Dialog.Content></Dialog.Root>
  </section>
}
