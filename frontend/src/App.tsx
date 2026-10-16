import { useMemo, useRef, useState, type ReactNode } from 'react'
import {
  Badge, Button, Callout, Checkbox, Flex, Heading, Select,
  Table, Text, TextField,
} from '@radix-ui/themes'
import {
  flexRender, getCoreRowModel, getSortedRowModel, useReactTable,
  type ColumnDef, type OnChangeFn, type RowSelectionState, type SortingState,
} from '@tanstack/react-table'
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  preparePromotionReview, formatDate, formatPrice, formatQuantity, formatTime, formatTimeZoneLabel,
  getCatalog, getConnection, getAccounts,
  type Action,
  type PreviewRequest, type Product, type ProductRole, type RuntimeMode,
  type Scenario, type MutationPlan, type ShopOperation, prepareMutation,
} from './api'
import {Finance} from './Finance'
import {compareDecimalStrings} from './numbers'
import {useTableFilters, TableFilterChips, type TableFilterColumn} from './TableTools'
import {LoadingOverlay} from './LoadingOverlay'
import {Accounts} from './Accounts'
import {StrategyWorkspace} from './StrategyWorkspace'
import {SellerAnalyticsWorkspace} from './SellerAnalyticsWorkspace'
import {OperationPanel} from './MutationPanel'
import {OperationHistory} from './OperationHistory'
import {ShutdownButton} from './ShutdownButton'
import {ProductCatalogue} from './ProductCatalogue'
import {ReportsWorkspace} from './ReportsWorkspace'
import {StatisticsWorkspace} from './StatisticsWorkspace'
import {Icon} from './Icon'
import {useConnectionStatus} from './useConnectionStatus'
import {InterfaceSettings,useTableColumns} from './uiPreferences'

type Area = 'promotions' | 'stocks' | 'finance' | 'strategies' | 'analytics' | 'statistics' | 'reports' | 'operations' | 'settings'
type WarehouseTab = 'stocks' | 'products'
type PromotionView = 'regular' | 'auto-add'
type Role = ProductRole
type AutoCount = number | 'loading' | 'refreshing' | 'error' | 'partial'
type AutoDateCounts = { date: string; member: AutoCount; candidate: AutoCount }

const autoReadQueue: Array<() => void> = []
let runningAutoReads = 0
function limitedAutoRead<T>(run: () => Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const cancelled = () => reject(new DOMException('Запрос отменён', 'AbortError'))
    const start = () => {
      signal.removeEventListener('abort', cancelled)
      if (signal.aborted) { cancelled(); autoReadQueue.shift()?.(); return }
      runningAutoReads++
      void run().then(resolve, reject).finally(() => {
        runningAutoReads--
        autoReadQueue.shift()?.()
      })
    }
    if (runningAutoReads < 3) start()
    else {
      autoReadQueue.push(start)
      signal.addEventListener('abort', cancelled, { once: true })
    }
  })
}
type DisplayRow = {
  id: string
  name: string
  offer_id: string | null
  product_id?: string | null
  sku?: string | null
  currency?: string | null
  money_currencies?: Record<string, string | null>
  price?: string | null
  action_price?: string | null
  min_price?: string | null
  max_price?: string | null
  price_min_elastic?: string | null
  price_max_elastic?: string | null
  max_discount_price?: string | null
  min_seller_price?: string | null
  max_action_price?: string | null
  elastic?: boolean | null
  role?: Role
  auto_add_at?: string | null
  available?: number | null
  reserved?: number | null
  present?: number | null
  warehouse_name?: string
  new_quantity?: string | null
  minimum_discount?: string | null
  maximum_discount?: string | null
  actual_discount?: string | null
}

const navItems: { id: Area; label: string }[] = [
  { id: 'promotions', label: 'Акции' }, { id: 'stocks', label: 'Склад' },
  {id:'finance',label:'Финансы'}, {id:'strategies',label:'Стратегии'}, {id:'analytics',label:'Аналитика'}, {id:'statistics',label:'Статистика'}, {id:'reports',label:'Отчёты'}, { id: 'operations', label: 'Операции' }, { id: 'settings', label: 'Настройки' },
]

function StatePanel({ title, children, busy = false }: { title: string; children: ReactNode; busy?: boolean }) {
  return <section className="state-panel" aria-busy={busy}>
    <Heading as="h2" size="4">{title}</Heading><Text as="p" color="gray">{children}</Text>
  </section>
}

function StatusBadge({ status }: { status: Action['status'] }) {
  const color = status === 'active' ? 'green' : status === 'upcoming' ? 'amber' : status === 'ended' ? 'gray' : 'gray'
  const label = status === 'active' ? 'Активна' : status === 'upcoming' ? 'Скоро' : status === 'ended' ? 'Завершена' : 'Статус неизвестен'
  return <Badge color={color} variant="soft">{label}</Badge>
}

function rowMoney(row: DisplayRow, field: 'price' | 'action_price' | 'min_price' | 'max_price' | 'max_discount_price' | 'min_seller_price' | 'max_action_price') {
  const currency = row.money_currencies && Object.hasOwn(row.money_currencies, field)
    ? row.money_currencies[field] : row.currency
  return formatPrice(row[field], currency)
}

function DataTable({
  data, kind, rowSelection, onSelectionChange, selectable, role, autoAdd,
}: {
  data: DisplayRow[]
  kind: 'prices' | 'stocks'
  rowSelection: RowSelectionState
  onSelectionChange: OnChangeFn<RowSelectionState>
  selectable: boolean
  role: Role
  autoAdd: boolean
}) {
  const [sorting, setSorting] = useState<SortingState>([])
  const scrollRegion = useRef<HTMLDivElement>(null)
  const columns = useMemo<ColumnDef<DisplayRow>[]>(() => {
    const selectColumn: ColumnDef<DisplayRow> = {
      id: 'select',
      header: ({ table }) => <Checkbox aria-label="Выбрать все показанные строки"
        checked={table.getIsAllPageRowsSelected() ? true : table.getIsSomePageRowsSelected() ? 'indeterminate' : false}
        onCheckedChange={value => table.toggleAllPageRowsSelected(Boolean(value))} />,
      cell: ({ row }) => <Checkbox aria-label={`Выбрать ${row.original.name}`}
        checked={row.getIsSelected()} onCheckedChange={value => row.toggleSelected(Boolean(value))} />,
      enableSorting: false,
      size: 42,
    }
    const nameColumn: ColumnDef<DisplayRow> = {accessorKey:'name',header:'Название',cell:({row})=><span title={row.original.name} className="compact-name">{row.original.name}</span>}
    const skuColumn:ColumnDef<DisplayRow>={accessorKey:'sku',header:'SKU',cell:({getValue})=>getValue<string|null>()??'Нет данных'}
    const identity: ColumnDef<DisplayRow>[] = [...(selectable?[skuColumn,nameColumn]:[nameColumn,skuColumn]),{accessorKey:'offer_id',header:'Артикул'}]
    const selectionColumn = selectable ? [selectColumn] : []
    const numeric = (field:'price'|'action_price'|'min_price'|'max_price'|'max_discount_price'|'min_seller_price'|'max_action_price',label:string):ColumnDef<DisplayRow> => ({id:field,accessorFn:row=>row[field]??undefined,header:label,cell:({row})=>rowMoney(row.original,field),sortingFn:(left,right,id)=>compareDecimalStrings(String(left.getValue(id)??''),String(right.getValue(id)??'')),sortUndefined:'last',meta:{numeric:true}})
    if(kind==='stocks')return [...selectionColumn,...identity,{accessorKey:'warehouse_name',header:'Склад'},...(['present','reserved','available'] as const).map((field,i):ColumnDef<DisplayRow>=>({id:field,accessorFn:row=>row[field]??undefined,header:['Остаток, шт.','Резерв, шт.','Доступно, шт.'][i],cell:({getValue})=>formatQuantity(getValue<number|undefined>()),sortUndefined:'last',meta:{numeric:true}})),{accessorKey:'new_quantity',header:'Новое кол-во, шт.',cell:({getValue})=>getValue<string|null>()??'Не рассчитано',meta:{numeric:true}}]
    const priceColumns=[numeric('price','Цена товара')]
    if(autoAdd)priceColumns.push(numeric('action_price','Цена автодобавления'))
    if(autoAdd)priceColumns.push(numeric('min_seller_price','Минимум продавца'))
    else if(role==='candidate')priceColumns.push(numeric('min_price','Минимальная цена участия'),numeric('max_action_price','Максимальная цена участия'))
    else priceColumns.push(numeric('max_price','Цена при минимальном бустинге'),numeric('min_price','Минимальная цена участия'))
    const percent=(key:'minimum_discount'|'maximum_discount'|'actual_discount'|'auto_add_discount',label:string):ColumnDef<DisplayRow>=>({accessorKey:key,header:label,cell:({getValue})=>{const v=getValue<string|null>();return v==null?'Нет данных':`${v.replace('.',',')} %`},sortingFn:(a,b,id)=>compareDecimalStrings(a.getValue(id)??'',b.getValue(id)??''),meta:{numeric:true}})
    const discountColumns=autoAdd?[percent('auto_add_discount','Скидка автодобавления, %')]:[percent('minimum_discount','Минимальная скидка, %'),percent('maximum_discount','Максимальная скидка, %'),...(role==='member'?[percent('actual_discount','Скидка, %')]:[])]
    return [...selectionColumn,...identity,...priceColumns.slice(0,autoAdd?2:1),...discountColumns,...priceColumns.slice(autoAdd?2:1)]
  }, [autoAdd, kind, role, selectable])
  const filterColumns=useMemo<TableFilterColumn[]>(()=>columns.filter(column=>column.id!=='select').map(column=>{const key='accessorKey' in column?String(column.accessorKey):column.id??'';return {key,label:typeof column.header==='string'?column.header:key,kind:(column.meta as {numeric?:boolean}|undefined)?.numeric?'decimal':'text'}}),[columns])
  const filters=useTableFilters<DisplayRow>(filterColumns)
  const tablePreferences=useTableColumns(kind==='stocks'?'stocks':`products-${autoAdd?'auto':'regular'}-${role}`,kind==='stocks'?'Остатки':`${autoAdd?'Автодобавление':'Акции'} · ${role==='member'?'участники':'кандидаты'}`,filterColumns)
  const filteredData=useMemo(()=>filters.apply(data,(row,key)=>{const v=row[key as keyof DisplayRow];return typeof v==='string'||typeof v==='number'?String(v):null}),[data,filters.filters,filterColumns])
  const table = useReactTable({
    data:filteredData, columns, autoResetPageIndex:false, state: { rowSelection, sorting,columnVisibility:tablePreferences.visibility }, onRowSelectionChange: onSelectionChange,
    onSortingChange: setSorting, getRowId: row => row.id, enableRowSelection: selectable,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
  })
  const visibleColumns = table.getVisibleLeafColumns()
  return <div className="table-frame"><TableFilterChips filters={filters.activeFilters} onClear={filters.clear} onClearAll={filters.clearAll}/>{filters.menu}<div {...filters.surfaceProps} ref={scrollRegion} className="table-scroll rt-TableRoot rt-r-size-2 rt-variant-surface" data-selectable={selectable} role="region" aria-label="Таблица данных" tabIndex={0}
    onKeyDown={event => {
      filters.surfaceProps.onKeyDown(event)
      if (event.target !== event.currentTarget || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return
      event.preventDefault()
      event.currentTarget.scrollLeft += event.key === 'ArrowRight' ? 80 : -80
    }}>
    <table className="rt-TableRootTable">
      <colgroup>{visibleColumns.map(column => <col key={column.id} className={column.id==='name'?'name-column':column.id==='select'?'selection-column':undefined} />)}</colgroup>
      <caption className="sr-only">{kind === 'prices' ? 'Товары акции, цены и пороги' : 'Доступные остатки товаров по складам'}</caption>
      <Table.Header><Table.Row>{table.getHeaderGroups().flatMap(group => group.headers).map(header => {
        const numeric = Boolean((header.column.columnDef.meta as { numeric?: boolean } | undefined)?.numeric)
        return <Table.ColumnHeaderCell {...(header.id==='select'?{}:filters.headerProps(header.column.id,header.column.getCanSort()))} aria-sort={header.column.getIsSorted()==='asc'?'ascending':header.column.getIsSorted()==='desc'?'descending':undefined} key={header.id} className={numeric ? 'numeric' : undefined}>
          {header.isPlaceholder ? null : header.column.getCanSort()
            ? <button className="sort-button" onClick={header.column.getToggleSortingHandler()}>
              {flexRender(header.column.columnDef.header, header.getContext())}
              <span>{header.column.getIsSorted() === 'asc' ? '↑' : header.column.getIsSorted() === 'desc' ? '↓' : ''}</span>
            </button>
            : flexRender(header.column.columnDef.header, header.getContext())}
        </Table.ColumnHeaderCell>
      })}</Table.Row></Table.Header>
      <Table.Body>{table.getRowModel().rows.length ? table.getRowModel().rows.map(row =>
        <Table.Row key={row.id} data-selected={row.getIsSelected() || undefined}>
          {row.getVisibleCells().map(cell => <Table.Cell {...(cell.column.id==='select'?{}:filters.cellProps(cell.column.id,cell.getValue()==null?null:String(cell.getValue())))} key={cell.id}
            className={Boolean((cell.column.columnDef.meta as { numeric?: boolean } | undefined)?.numeric) ? 'numeric' : undefined}>
            {flexRender(cell.column.columnDef.cell, cell.getContext())}
          </Table.Cell>)}
        </Table.Row>,
      ) : <Table.Row><Table.Cell colSpan={columns.length}>По заданным условиям ничего не найдено.</Table.Cell></Table.Row>}</Table.Body>
    </table>
  </div></div>
}

function countLabel(count: AutoCount) {
  if (count === 'loading') return 'загрузка…'
  if (count === 'refreshing') return 'обновляем…'
  if (count === 'error') return 'ошибка'
  if (count === 'partial') return 'неполно'
  return new Intl.NumberFormat('ru-RU').format(count)
}

function ActionCard({ action, counts, onOpen }: { action: Action; counts: AutoDateCounts[]; onOpen: () => void }) {
  return <button className="action-card" onClick={onOpen}>
    <span className="action-card-main"><strong>{action.name}</strong><span><StatusBadge status={action.status} /></span></span>
    <span className="action-card-meta">{formatDate(action.start_at)} — {formatDate(action.end_at)} <span className="timezone-note">{formatTimeZoneLabel()}</span></span>
    {counts.length ? counts.map(({date,member,candidate}) => <span className="action-card-date" key={date}>
      <span>{formatDate(date)} · {formatTimeZoneLabel()}</span>
      <span>Участники <b>{countLabel(member)}</b></span>
      <span>Кандидаты <b>{countLabel(candidate)}</b></span>
    </span>) : <span className="action-card-date">Даты автодобавления не предоставлены</span>}
    <span className="action-card-open">Открыть акцию</span>
  </button>
}

function ActionCount({ action, role, mode, currentRole, loadedCount, complete, mockProducts, autoAdd }: {
  action: Action; role: Role; mode: RuntimeMode; currentRole: Role; loadedCount: number; complete: boolean; mockProducts: Product[]; autoAdd: boolean
}) {
  const serverCount = role === 'member' ? action.member_count : action.candidate_count
  if (!autoAdd && serverCount !== null && serverCount !== undefined) return new Intl.NumberFormat('ru-RU').format(serverCount)
  if (mode === 'MOCK') return String(mockProducts.filter(product => product.action_id === action.id && product.role === role).length)
  if (currentRole === role && complete) return `${loadedCount} загружено`
  return 'загрузка…'
}

function ActionDetail({ action, role, setRole, mode, loadedProducts, complete, mockProducts, autoAdd,counts }: {
  action: Action; role: Role; setRole: (role: Role) => void; mode: RuntimeMode
  loadedProducts: Product[]; complete: boolean; mockProducts: Product[]; autoAdd: boolean;counts?:{member:number|null;candidate:number|null}
}) {
  const memberCount = counts?.member!=null?`${counts.member} загружено`:<ActionCount action={action} role="member" mode={mode} currentRole={role} loadedCount={loadedProducts.length} complete={complete} mockProducts={mockProducts} autoAdd={autoAdd} />
  const candidateCount = counts?.candidate!=null?`${counts.candidate} загружено`:<ActionCount action={action} role="candidate" mode={mode} currentRole={role} loadedCount={loadedProducts.length} complete={complete} mockProducts={mockProducts} autoAdd={autoAdd} />
  return <div className="action-detail"><div className="action-detail-meta"><StatusBadge status={action.status} /><span>Период акции: {formatDate(action.start_at)} — {formatDate(action.end_at)} · {formatTimeZoneLabel()}</span></div>
    <div className="role-tabs" role="group" aria-label="Состав акции"><Button aria-pressed={role==='member'} variant={role === 'member' ? 'soft' : 'ghost'} onClick={() => setRole('member')}>Участники <Badge variant="soft">{memberCount}</Badge></Button><Button aria-pressed={role==='candidate'} variant={role === 'candidate' ? 'soft' : 'ghost'} onClick={() => setRole('candidate')}>Кандидаты <Badge variant="soft">{candidateCount}</Badge></Button></div>
  </div>
}

function AutoAddDateControl({ action, value, onChange }: { action: Action; value: string | null; onChange: (value: string | null) => void }) {
  const dates = action.auto_add_dates ?? []
  return <div className="warehouse-select"><label htmlFor="auto-add-date">Дата автодобавления · {formatTimeZoneLabel()}</label>
    <Select.Root value={value ?? ''} onValueChange={onChange} disabled={dates.length === 0}>
      <Select.Trigger id="auto-add-date" placeholder={dates.length ? 'Выберите дату из списка акции' : 'Даты не предоставлены'} />
      <Select.Content>{dates.map(date => <Select.Item key={date} value={date}>{formatDate(date)} · {formatTimeZoneLabel()}</Select.Item>)}</Select.Content>
    </Select.Root>
    <Text size="1" color="gray">Выберите дату, которую вернул сервер для этой акции.</Text>
  </div>
}

function PartialNotice({ warning, partial = true }: { warning: string; partial?: boolean }) {
  const visibleWarning=warning.replace('Для части цен Ozon не указал валюту: суммы показаны без неё, скидки рассчитаны исходя из одной валюты','').trim()
  if(!partial&&!visibleWarning)return null
  return <Callout.Root color="amber" className="partial-notice"><Callout.Text><strong>{partial ? 'Загружена только часть данных.' : 'Замечание API.'}</strong> {visibleWarning}</Callout.Text></Callout.Root>
}

function ErrorPanel({ title = 'Не удалось загрузить данные', message, onRetry }: { title?: string; message: string; onRetry: () => void }) {
  return <section className="state-panel"><Heading as="h2" size="4">{title}</Heading><Text as="p" color="gray">{message}</Text><Button variant="outline" onClick={onRetry}>Повторить запрос</Button></section>
}

export default function App({dark,onThemeChange}:{dark:boolean;onThemeChange:(value?:boolean)=>void}) {
  const client = useQueryClient()
  const [area, setArea] = useState<Area>('promotions')
  const [warehouseTab,setWarehouseTab]=useState<WarehouseTab>('stocks')
  const [accountsOpen,setAccountsOpen]=useState(false)
  const [operationRequested,setOperationRequested]=useState<ShopOperation|null>(null)
  const [operationPlan,setOperationPlan]=useState<MutationPlan|null>(null)
  const [operationBusy,setOperationBusy]=useState(false)
  const [operationError,setOperationError]=useState<string|null>(null)
  const operationGeneration=useRef(0)
  const [promotionView, setPromotionView] = useState<PromotionView>('regular')
  const [activeActionId, setActiveActionId] = useState<string | null>(null)
  const [role, setRole] = useState<Role>('member')
  const [autoAddDate, setAutoAddDate] = useState<string | null>(null)
  const [scenario, setScenario] = useState<Scenario>('ready')
  const [statusFilter, setStatusFilter] = useState('all')
  const [selection, setSelection] = useState<RowSelectionState>({})
  const [stockValue, setStockValue] = useState('')
  const [stockMode,setStockMode]=useState<'set'|'add'>('set')
  const [stockResults,setStockResults]=useState<Record<string,string|null>>({})
  const [warehouseId, setWarehouseId] = useState('all')
  const [discountThreshold, setDiscountThreshold] = useState('5')

  const connectionQuery = useQuery({
    queryKey: ['connection'], queryFn: ({ signal }) => getConnection(signal),
    retry: false, refetchOnWindowFocus: false,
  })
  const mode = connectionQuery.data?.mode
  const accountsQuery = useQuery({queryKey:['accounts'],queryFn:({signal})=>getAccounts(signal),enabled:Boolean(mode),retry:false,refetchOnWindowFocus:false})
  const activeStoreName = accountsQuery.data?.items.find(item=>item.id===accountsQuery.data?.active_id)?.name ?? (accountsQuery.isLoading?'Загружаем магазин…':accountsQuery.isError?'Магазин недоступен':'Текущее подключение')
  const connectionStatus = useConnectionStatus(accountsQuery.data?.active_id,accountsQuery.data?.revision,mode,true)
  const effectiveScenario: Scenario = mode === 'LIVE_READ' ? 'ready' : scenario
  const actionsQuery = useQuery({
    queryKey: ['catalog', mode, effectiveScenario, 'actions'],
    queryFn: ({ signal }) => getCatalog(effectiveScenario, { scope: 'actions' }, signal),
    enabled: area === 'promotions' && mode !== undefined,
    retry: false, refetchOnWindowFocus: false,
  })
  const actionsCatalog = actionsQuery.data
  const actions = actionsCatalog?.actions ?? []
  const autoLoadSpecs = useMemo(() => (actionsCatalog?.actions ?? [])
    .filter(action => action.auto_add)
    .flatMap(action => [...new Set(action.auto_add_dates ?? [])].flatMap(date =>
      (['member', 'candidate'] as const).map(role => ({actionId: action.id, date, role})))), [actionsCatalog?.actions])
  const autoReadResults = useQueries({queries:autoLoadSpecs.map(({actionId,date,role}) => ({
    queryKey:['catalog',mode,effectiveScenario,'products',actionId,role,true,date],
    queryFn:({signal}:{signal:AbortSignal})=>limitedAutoRead(
      ()=>getCatalog(effectiveScenario,{scope:'products',action_id:actionId,role,auto_add:true,auto_add_date:date},signal),signal),
    enabled:area==='promotions'&&promotionView==='auto-add'&&mode!==undefined,
    retry:false,refetchOnWindowFocus:false,staleTime:120000,
  }))})
  const autoResultIndex = new Map(autoLoadSpecs.map((spec,index)=>[JSON.stringify([spec.actionId,spec.date,spec.role]),index]))
  const autoCounts = new Map<string, AutoDateCounts[]>()
  for (const action of actions.filter(item=>item.auto_add)) {
    const dates=[...new Set(action.auto_add_dates ?? [])]
    autoCounts.set(action.id,dates.map(date=>{
      const count=(role:Role):AutoCount=>{
        const index=autoResultIndex.get(JSON.stringify([action.id,date,role]))
        const result=index===undefined?undefined:autoReadResults[index]
        if (!result || result.isPending) return 'loading'
        if (result.isFetching) return 'refreshing'
        if (result.isError) return 'error'
        if (!result.data?.complete || result.data.warnings.length) return 'partial'
        return result.data.products.filter(item=>item.action_id===action.id&&item.role===role).length
      }
      return {date,member:count('member'),candidate:count('candidate')}
    }))
  }
  const activeAction = actions.find(action => action.id === activeActionId) ?? null
  const isAutoScope = promotionView === 'auto-add' && activeAction !== null
  // An action's auto_add flag doesn't change ordinary-membership semantics.
  // Only the dedicated auto-add section requests the date-scoped view.
  const activeDateIsValid = !isAutoScope || mode === 'MOCK' ||
    (autoAddDate !== null && (activeAction?.auto_add_dates ?? []).includes(autoAddDate))
  const memberProductsQuery = useQuery({
    queryKey: ['catalog', mode, effectiveScenario, 'products', activeActionId, 'member', isAutoScope, isAutoScope ? autoAddDate : null],
    queryFn: ({ signal }) => activeActionId && activeAction
      ? getCatalog(effectiveScenario, {
        scope: 'products', action_id: activeActionId, role:'member', auto_add: isAutoScope,
        ...(isAutoScope && autoAddDate ? { auto_add_date: autoAddDate } : {}),
      }, signal)
      : Promise.reject(new Error('Сначала выберите акцию.')),
    enabled: area === 'promotions' && mode !== undefined && activeAction !== null && activeDateIsValid,
    retry: false, refetchOnWindowFocus: false,staleTime:120000,
  })
  const candidateProductsQuery=useQuery({queryKey:['catalog',mode,effectiveScenario,'products',activeActionId,'candidate',isAutoScope,isAutoScope?autoAddDate:null],queryFn:({signal})=>getCatalog(effectiveScenario,{scope:'products',action_id:activeActionId!,role:'candidate',auto_add:isAutoScope,...(isAutoScope&&autoAddDate?{auto_add_date:autoAddDate}:{})},signal),enabled:area==='promotions'&&mode!==undefined&&activeAction!==null&&activeDateIsValid,retry:false,refetchOnWindowFocus:false,staleTime:120000})
  const productsQuery=role==='member'?memberProductsQuery:candidateProductsQuery
  const loadedCounts={member:memberProductsQuery.data?.complete?memberProductsQuery.data.products.filter(p=>p.action_id===activeActionId&&p.role==='member').length:null,candidate:candidateProductsQuery.data?.complete?candidateProductsQuery.data.products.filter(p=>p.action_id===activeActionId&&p.role==='candidate').length:null}
  const stocksQuery = useQuery({
    queryKey: ['catalog', mode, effectiveScenario, 'stocks'],
    queryFn: ({ signal }) => getCatalog(effectiveScenario, { scope: 'stocks' }, signal),
    enabled: area === 'stocks' && warehouseTab==='stocks' && mode !== undefined,
    retry: false, refetchOnWindowFocus: false,
  })
  const invalidatePreview = () => {setStockResults({})}
  const clearScope = () => {operationGeneration.current++;setOperationRequested(null);setOperationPlan(null);setOperationBusy(false);setOperationError(null);setSelection({});setStockResults({})}
  const changeArea = (next: Area) => {
    setArea(next); setPromotionView('regular'); setActiveActionId(null)
    setAutoAddDate(null); setStatusFilter('all'); setWarehouseId('all'); clearScope()
  }
  const changeScenario = (next: Scenario) => { setScenario(next); clearScope() }
  const chooseAction = (actionId: string) => {
    const action = actions.find(item => item.id === actionId)
    setActiveActionId(actionId); setRole('member'); setAutoAddDate(action?.auto_add_dates?.[0] ?? null); clearScope()
  }
  const openAutoAdd = (action: Action) => {
    setActiveActionId(action.id); setRole('member'); setAutoAddDate(action.auto_add_dates?.[0] ?? null)
    setStatusFilter('all'); clearScope()
  }
  const backToAutoList = () => { setActiveActionId(null); setAutoAddDate(null); setRole('member'); clearScope() }

  const activeProducts = useMemo(() => (productsQuery.data?.products ?? actionsCatalog?.products ?? []).filter(product =>
    product.action_id === activeActionId && product.role === role,
  ), [actionsCatalog?.products, activeActionId, productsQuery.data?.products, role])
  const filteredProducts = activeProducts
  const selectedIds = Object.keys(selection).filter(id => selection[id])
  const selectedCount = selectedIds.length
  const requestOperation=async(operation:ShopOperation,ids:string[],generation:number)=>{
    if(!mode)throw new Error('Режим сервера ещё не определён.')
    if(!ids.length)throw new Error('По заданному порогу подходящих товаров нет.')
    const payload:PreviewRequest=operation==='stocks'
      ?{kind:'stocks',ids,mode:'manual',value:stockValue,stock_mode:stockMode,rounding:'1',scenario:effectiveScenario}
      :{kind:'prices',ids,action_id:activeActionId,role,auto_add:isAutoScope,auto_add_date:isAutoScope?autoAddDate:null,mode:'manual',value:'0',rounding:'1',floor:null,scenario:effectiveScenario}
    const plan=await prepareMutation(operation,payload)
    if(generation===operationGeneration.current){setOperationPlan(plan);if(operation==='stocks')setStockResults(Object.fromEntries(plan.rows.map(row=>[row.id,row.after===null?null:String(row.after)])))}
  }
  const prepareOperation=async(operation:ShopOperation,ids=selectedIds)=>{
    const generation=++operationGeneration.current
    setOperationRequested(operation);setOperationBusy(true);setOperationError(null);setOperationPlan(null)
    try{await requestOperation(operation,ids,generation)}
    catch(error){if(generation===operationGeneration.current)setOperationError(error instanceof Error?error.message:'Не удалось получить план изменений')}
    finally{if(generation===operationGeneration.current)setOperationBusy(false)}
  }
  const removeOrAddByThreshold=async()=>{
    const operation:ShopOperation=role==='member'?(isAutoScope?'auto_remove':'action_remove'):(isAutoScope?'auto_add':'action_add')
    const generation=++operationGeneration.current
    setOperationRequested(operation);setOperationBusy(true);setOperationError(null);setOperationPlan(null)
    try{
      if(!activeActionId)throw new Error('Выберите акцию.')
      const review=await preparePromotionReview({action_id:activeActionId,role,auto_add:isAutoScope,auto_add_date:isAutoScope?autoAddDate:null,threshold:discountThreshold})
      if(generation!==operationGeneration.current)return
      if(!review.complete)throw new Error('Состав акции загружен не полностью. Обновите данные перед изменением.')
      if(!review.items.length&&review.unknown>0)throw new Error(`Не удалось проверить скидку для ${review.unknown} товаров: отсутствуют или противоречат друг другу цены либо валюты. Обновите состав акции.`)
      await requestOperation(operation,review.items.map(item=>item.id),generation)
    }catch(error){if(generation===operationGeneration.current)setOperationError(error instanceof Error?error.message:'Не удалось проверить скидки')}
    finally{if(generation===operationGeneration.current)setOperationBusy(false)}
  }
  const operationButtons=activeAction&&selectedCount>0?<Flex className="operation-toolbar" gap="3" wrap="wrap"><strong className="selection-count">Выбрано: {selectedCount}</strong>{(!isAutoScope||role==='candidate')&&<Button variant="solid" disabled={operationBusy||selectedCount>1000} onClick={()=>void prepareOperation(isAutoScope?'auto_add':'action_add')}>{isAutoScope?'Вернуть со скидкой Ozon':role==='member'?'Изменить цену участия':'Добавить выбранные'}</Button>}{role==='member'&&<Button variant="soft" color="red" disabled={operationBusy||selectedCount>1000} onClick={()=>void prepareOperation(isAutoScope?'auto_remove':'action_remove')}>Удалить выбранные</Button>}</Flex>:null
  const discountControls=activeAction?<section className="discount-filter" aria-label={role==='member'?'Удаление по скидке':'Добавление по скидке'}>
    <Text as="label" htmlFor="discount-threshold">{role==='member'?(isAutoScope?'Удалить из автодобавления товары со скидкой от, %':'Удалить участников со скидкой от, %'):'Добавить кандидатов с минимальной скидкой меньше, %'}</Text>
    <Flex gap="3" align="end" wrap="wrap"><TextField.Root autoComplete="off" id="discount-threshold" aria-label="Порог скидки в процентах" value={discountThreshold} inputMode="decimal" onChange={event=>setDiscountThreshold(event.target.value)}/>
    <Button variant="outline" disabled={operationBusy||productsQuery.isFetching||!activeProducts.length} onClick={()=>void removeOrAddByThreshold()}>{role==='member'?'Удалить по скидке':'Добавить'}</Button></Flex>
  </section>:null
  const stocks = stocksQuery.data?.stocks ?? []
  const filteredStocks = useMemo(() => stocks.filter(stock =>
    warehouseId === 'all' || stock.warehouse_id === warehouseId,
  ), [stocks, warehouseId])
  const autoActions = actions.filter(action => action.auto_add)
  const visibleAutoActions = autoActions.filter(action => statusFilter === 'all' || action.status === statusFilter)
  const scenarioQuery = mode === 'MOCK' ? scenario : 'ready'
  const sectionCatalog = area === 'stocks' ? stocksQuery.data : activeAction ? productsQuery.data : actionsCatalog
  const sectionTime = sectionCatalog?.fetched_at ? formatTime(sectionCatalog.fetched_at) : null

  const refreshSection = async () => {
    if (!mode) return
    clearScope()
    const key = ['catalog', mode, scenarioQuery] as const
    if (area === 'stocks') {
      await client.invalidateQueries({ queryKey: [...key, 'stocks'] })
      return
    }
    if (area === 'promotions') {
      await client.invalidateQueries({ queryKey: [...key, 'actions'] })
      if (promotionView==='auto-add') await client.invalidateQueries({queryKey:[...key,'products'],refetchType:'active'})
      else if (activeActionId) await client.invalidateQueries({ queryKey: [...key, 'products', activeActionId] })
    }
  }

  const changeSelection = (updater: Parameters<OnChangeFn<RowSelectionState>>[0]) => {
    setSelection(current => typeof updater === 'function' ? updater(current) : updater)
    invalidatePreview()
    setStockResults({})
  }
  const actionSelector = <div className="action-select"><Text as="label" htmlFor="regular-action">Акция</Text><Select.Root value={activeActionId ?? ''} onValueChange={chooseAction}><Select.Trigger id="regular-action" placeholder="Выберите акцию"/><Select.Content>{actions.map(action=><Select.Item key={action.id} value={action.id}>{action.name}</Select.Item>)}</Select.Content></Select.Root></div>

  return <>
  <div className="app-shell">
    <a className="skip-link" href="#main-content">К рабочей области</a>
    <header className="topbar">
      <div className="brand"><img className="brand-mark" src="/manager-mark.svg" alt="" width="40" height="40"/><span><strong>Ozon <span className="brand-manager">Manager</span></strong><small>Товары. Цены. Управление.</small></span></div>
      <div className="topbar-context"><div className="store-context"><Button variant="ghost" color="gray" className="store-name" onClick={()=>setAccountsOpen(true)} title={activeStoreName}><Icon name="account"/><span className="store-name-text">{activeStoreName}</span><span className={`connection-dot is-${connectionStatus.status}`} role="img" aria-label={connectionStatus.status==='ok'?'Подключение проверено':connectionStatus.status==='error'?'Ошибка подключения':connectionStatus.status==='checking'?'Проверяем подключение':'Подключение не проверено'}/></Button>{mode==='MOCK'&&<span className="mode-context">Демонстрация</span>}</div><Button className="theme-toggle" variant="ghost" color="gray" onClick={()=>onThemeChange()} aria-label={dark?'Включить светлую тему':'Включить тёмную тему'}><Icon name={dark?'sun':'moon'}/></Button></div>
    </header>
    <div className="layout">
      <aside className="sidebar">
        <nav aria-label="Основные разделы" className="primary-nav">
          {navItems.map(item => <Button key={item.id} variant={area === item.id ? 'soft' : 'ghost'} color={area === item.id ? 'blue' : 'gray'}
            className="nav-button" onClick={() => changeArea(item.id)} aria-current={area === item.id ? 'page' : undefined}><Icon name={item.id}/>{item.label}</Button>)}
        </nav>
        <ShutdownButton/>
      </aside>
      <main id="main-content" className="main-content" data-area={area} tabIndex={-1}>
        {connectionQuery.isLoading && <StatePanel title="Определяем режим сервера" busy>До получения статуса каталоги не запрашиваются.</StatePanel>}
        {connectionQuery.isError && <ErrorPanel title="Не удалось определить режим работы" message={connectionQuery.error.message} onRetry={() => void connectionQuery.refetch()} />}

        {area === 'promotions' && mode && !connectionQuery.isError && <>
          <div className="page-heading"><Heading size="7" className="page-title"><Icon name="promotions"/>{activeAction ? activeAction.name : 'Акции'}</Heading>{(promotionView==='regular'||activeAction)&&<RefreshControl time={sectionTime} busy={actionsQuery.isFetching||productsQuery.isFetching} disabled={Boolean(activeAction)&&!activeDateIsValid} label={activeAction?'Обновить состав':'Обновить'} onClick={()=>void refreshSection()}/>}</div>
          <div className="subnav" aria-label="Разделы акций">
            <Button aria-pressed={promotionView==='regular'} variant={promotionView === 'regular' ? 'solid' : 'outline'} onClick={() => {
              setPromotionView('regular'); setActiveActionId(null); setAutoAddDate(null); clearScope()
            }}>Акции Ozon <Badge color="gray" variant="soft">{actionsQuery.data?actions.length:'—'}</Badge></Button>
            <Button aria-pressed={promotionView==='auto-add'} variant={promotionView === 'auto-add' ? 'solid' : 'outline'} onClick={() => {
              setPromotionView('auto-add'); setActiveActionId(null); setAutoAddDate(null); clearScope()
            }}>Автодобавление <Badge color="gray" variant="soft">{actionsQuery.data?autoActions.length:'—'}</Badge></Button>
          </div>
          {mode === 'MOCK' && <ScenarioControl scenario={scenario} onChange={changeScenario} />}
          {promotionView === 'auto-add' && !activeAction && <>
            <Flex justify="between" align="center" className="section-heading"><Heading as="h2" size="4">Все акции с автодобавлением</Heading><RefreshControl time={sectionTime} busy={actionsQuery.isFetching||autoReadResults.some(result=>result.isFetching)} onClick={() => void refreshSection()} /></Flex>
            {actionsQuery.isLoading ? <StatePanel title="Загружаем список акций" busy>Читаем только список акций из выбранного режима.</StatePanel>
              : actionsQuery.isError ? <ErrorPanel message={actionsQuery.error.message} onRetry={() => void actionsQuery.refetch()} />
              : actionsCatalog ? <>
                {(!actionsCatalog.complete || actionsCatalog.warnings.length > 0) && <PartialNotice partial={!actionsCatalog.complete} warning={actionsCatalog.warnings.join(' ') || 'Список акций загружен не полностью.'} />}
                <StatusFilter value={statusFilter} onChange={setStatusFilter} />
                {visibleAutoActions.length ? <div className="action-list">{visibleAutoActions.map(action =>
                  <ActionCard key={action.id} action={action} counts={autoCounts.get(action.id) ?? []} onOpen={() => openAutoAdd(action)} />)}</div>
                  : <StatePanel title={autoActions.length ? 'По фильтру ничего не найдено' : 'Акций с автодобавлением нет'}>{autoActions.length ? 'Измените фильтр статуса.' : 'Сервер не вернул акции с auto_add=true.'}</StatePanel>}
              </> : null}
          </>}
          {promotionView === 'auto-add' && activeAction && <>
            <Button variant="ghost" className="back-button" onClick={backToAutoList}>Все акции с автодобавлением</Button>
            <ActionDetail counts={loadedCounts} action={activeAction} role={role} setRole={next => { setRole(next); clearScope() }} mode={mode} loadedProducts={activeProducts} complete={productsQuery.data?.complete ?? false} mockProducts={actionsCatalog?.products ?? []} autoAdd={isAutoScope} />
            {isAutoScope && <AutoAddDateControl action={activeAction} value={autoAddDate} onChange={next => { setAutoAddDate(next); clearScope() }} />}
            {discountControls}
            {operationButtons}
            {!activeDateIsValid ? <StatePanel title="Выберите дату из списка акции">Сервер не предоставил дат автодобавления для этой акции.</StatePanel>
              : productsQuery.isLoading ? <StatePanel title="Загружаем состав акции" busy>Запрашиваем только выбранные акцию, дату и состав.</StatePanel>
              : productsQuery.isError ? <ErrorPanel message={productsQuery.error.message} onRetry={() => void productsQuery.refetch()} />
              : productsQuery.data ? <>
                {(!productsQuery.data.complete || productsQuery.data.warnings.length > 0) && <PartialNotice partial={!productsQuery.data.complete} warning={productsQuery.data.warnings.join(' ') || 'Состав загружен не полностью.'} />}
                {filteredProducts.length ? <DataTable key={`${mode}:${activeActionId}:${role}:${isAutoScope}:${autoAddDate}`} data={filteredProducts.map(product => ({ ...product }))} kind="prices" rowSelection={selection} onSelectionChange={changeSelection} selectable={mode !== undefined} role={role} autoAdd={isAutoScope} />
                  : <StatePanel title={role === 'member' ? 'Участников нет' : 'Кандидатов нет'}>Выберите другой состав акции.</StatePanel>}
              </> : null}
          </>}
          {promotionView === 'regular' && <>
            {actionsQuery.isLoading ? <StatePanel title="Загружаем акции" busy>Читаем список акций из выбранного режима.</StatePanel>
              : actionsQuery.isError ? <ErrorPanel message={actionsQuery.error.message} onRetry={() => void actionsQuery.refetch()} />
              : actionsCatalog ? <>
                {(!actionsCatalog.complete || actionsCatalog.warnings.length > 0) && <PartialNotice partial={!actionsCatalog.complete} warning={actionsCatalog.warnings.join(' ') || 'Список акций загружен не полностью.'} />}
                {activeAction && actionSelector}
                {activeAction && <>
                  <ActionDetail counts={loadedCounts} action={activeAction} role={role} setRole={next => { setRole(next); clearScope() }} mode={mode} loadedProducts={activeProducts} complete={productsQuery.data?.complete ?? false} mockProducts={actionsCatalog.products} autoAdd={isAutoScope} />
                  {isAutoScope && <AutoAddDateControl action={activeAction} value={autoAddDate} onChange={next => { setAutoAddDate(next); clearScope() }} />}
                  {discountControls}
                  {operationButtons}
                        {!activeDateIsValid ? <StatePanel title="Выберите дату автодобавления">Дата должна совпадать с одним из значений, полученных от сервера.</StatePanel>
                    : productsQuery.isLoading ? <StatePanel title="Загружаем состав акции" busy>Запрашиваем выбранную акцию и состав.</StatePanel>
                    : productsQuery.isError ? <ErrorPanel message={productsQuery.error.message} onRetry={() => void productsQuery.refetch()} />
                    : productsQuery.data ? <>
                      {(!productsQuery.data.complete || productsQuery.data.warnings.length > 0) && <PartialNotice partial={!productsQuery.data.complete} warning={productsQuery.data.warnings.join(' ') || 'Состав загружен не полностью.'} />}
                {filteredProducts.length ? <DataTable key={`${mode}:${activeActionId}:${role}:${isAutoScope}:${autoAddDate}`} data={filteredProducts.map(product => ({ ...product }))} kind="prices" rowSelection={selection} onSelectionChange={changeSelection} selectable={mode !== undefined} role={role} autoAdd={isAutoScope} />
                        : <StatePanel title={role === 'member' ? 'Участников нет' : 'Кандидатов нет'}>Выберите другой состав акции.</StatePanel>}
                    </> : null}
                </>}
                {!activeAction && <section className="state-panel action-picker"><Heading as="h2" size="4">Выберите акцию</Heading><Text as="p" color="gray">Для просмотра состава выберите акцию из списка.</Text>{actionSelector}</section>}
              </> : null}
          </>}
        </>}

        {area === 'stocks' && mode && !connectionQuery.isError && <>
          <div className="page-heading"><Heading size="7" className="page-title"><Icon name="stocks"/>Склад</Heading>{warehouseTab==='stocks'&&<RefreshControl time={sectionTime} busy={stocksQuery.isFetching} label="Обновить" onClick={() => void refreshSection()} />}</div>
          <nav className="subnav" aria-label="Разделы склада">{([['stocks','Остатки'],['products','Товары']] as const).map(([id,label])=><Button key={id} aria-pressed={warehouseTab===id} variant={warehouseTab===id?'solid':'outline'} onClick={()=>{setWarehouseTab(id);clearScope()}}>{label}</Button>)}</nav>
          {warehouseTab==='stocks'&&<>
          {mode === 'MOCK' && <ScenarioControl scenario={scenario} onChange={changeScenario} />}
          {stocksQuery.isLoading ? <StatePanel title="Загружаем остатки" busy>Запрашиваем остатки только при открытии этого раздела.</StatePanel>
            : stocksQuery.isError ? <ErrorPanel message={stocksQuery.error.message} onRetry={() => void stocksQuery.refetch()} />
            : stocksQuery.data ? <>
              {(!stocksQuery.data.complete || stocksQuery.data.warnings.length > 0) && <PartialNotice partial={!stocksQuery.data.complete} warning={stocksQuery.data.warnings.join(' ') || 'Остатки загружены не полностью.'} />}
              <dl className="stock-summary">{(['present','reserved','available'] as const).map((field,i)=>{
                const values=filteredStocks.map(r=>r[field]);const known=values.every(v=>v!==null&&Number.isSafeInteger(v))
                const sum=known?values.reduce((sum,v)=>sum+BigInt(v!),0n).toLocaleString('ru-RU'):'Нет данных'
                return <div key={field}><dt>{['Остаток','Резерв','Доступно'][i]}</dt><dd>{sum}</dd></div>
              })}</dl>
              <section className="stock-editor" aria-label="Изменение остатков"><div className="warehouse-select"><label htmlFor="warehouse">Склад</label><Select.Root value={warehouseId} onValueChange={next => { setWarehouseId(next); clearScope() }}>
                <Select.Trigger id="warehouse" />
                <Select.Content><Select.Item value="all">Все склады</Select.Item>{Array.from(new Map(stocks.map(stock => [stock.warehouse_id, stock.warehouse_name])).entries()).map(([id, name]) => <Select.Item key={id} value={id}>{name}</Select.Item>)}</Select.Content>
              </Select.Root></div>
              {mode !== undefined && <StockValueInput value={stockValue} stockMode={stockMode} onModeChange={next=>{invalidatePreview();setStockResults({});setStockMode(next)}} onChange={next => { invalidatePreview();setStockResults({}); setStockValue(next) }} />}
              <div className="stock-editor-action"><Text size="2" color="gray">Выбрано: {selectedCount}</Text><Button variant="solid" disabled={operationBusy||selectedCount===0||selectedCount>100} onClick={()=>void prepareOperation('stocks')}>Изменить доступные остатки</Button></div></section>
              {filteredStocks.length ? <DataTable key={`${mode}:${warehouseId}:${scenario}`} data={filteredStocks.map(stock => ({ ...stock,new_quantity:stockResults[stock.id] }))} kind="stocks" rowSelection={selection} onSelectionChange={changeSelection} selectable={mode !== undefined} role="member" autoAdd={false} />
                : <StatePanel title={stocks.length ? 'По выбранному складу остатков нет' : 'Остатков нет'}>Выберите другой склад или проверьте предупреждения загрузки.</StatePanel>}
            </> : null}</>}
          {warehouseTab==='products'&&<ProductCatalogue mode={mode} scopeKey={`${mode}:${accountsQuery.data?.active_id}:${accountsQuery.data?.revision}`}/>}
        </>}

        {mode&&<div hidden={area!=='finance'}><Finance key={`${mode}:${accountsQuery.data?.active_id??'none'}:${accountsQuery.data?.revision??0}`} mode={mode} scopeKey={`${mode}:${accountsQuery.data?.active_id??'none'}:${accountsQuery.data?.revision??0}`}/></div>}
        {mode&&area==='strategies'&&<><Heading size="7" className="page-title"><Icon name="strategies"/>Стратегии</Heading><StrategyWorkspace key={`${mode}:${accountsQuery.data?.active_id}:${accountsQuery.data?.revision}`} mode={mode} scopeKey={`${mode}:${accountsQuery.data?.active_id??'none'}:${accountsQuery.data?.revision??0}`}/></>}
        {mode&&area==='analytics'&&<><Heading size="7" className="page-title"><Icon name="analytics"/>Аналитика</Heading><SellerAnalyticsWorkspace key={`${mode}:${accountsQuery.data?.active_id}:${accountsQuery.data?.revision}`} mode={mode} scopeKey={`${mode}:${accountsQuery.data?.active_id??'none'}:${accountsQuery.data?.revision??0}`}/></>}
        {mode&&area==='statistics'&&<><Heading size="7" className="page-title"><Icon name="statistics"/>Статистика</Heading><StatisticsWorkspace key={`${mode}:${accountsQuery.data?.active_id}:${accountsQuery.data?.revision}`} mode={mode}/></>}
        {area === 'operations' && <>
          <Heading size="7" className="page-title"><Icon name="operations"/>Операции</Heading>
          <OperationHistory/>
        </>}

        {mode&&area==='reports'&&<><Heading size="7" className="page-title"><Icon name="reports"/>Отчёты</Heading><ReportsWorkspace key={`${mode}:${accountsQuery.data?.active_id}:${accountsQuery.data?.revision}`} mode={mode} scopeKey={`${mode}:${accountsQuery.data?.active_id}:${accountsQuery.data?.revision}`}/></>}

        {area === 'settings' && <>
          <Heading size="7" className="page-title"><Icon name="settings"/>Настройки</Heading>
          <section className="appearance-settings"><div className="appearance-heading"><Icon name={dark?'moon':'sun'}/><div><Heading as="h2" size="4">Оформление</Heading><Text as="p" size="2" color="gray">Выберите тему для комфортной работы.</Text></div></div><div className="theme-previews"><button type="button" aria-pressed={!dark} onClick={()=>onThemeChange(false)} className={`theme-preview light-preview ${!dark?'current':''}`}><span aria-hidden="true"/><span aria-hidden="true"/><span aria-hidden="true"/><strong>Светлая</strong></button><button type="button" aria-pressed={dark} onClick={()=>onThemeChange(true)} className={`theme-preview dark-preview ${dark?'current':''}`}><span aria-hidden="true"/><span aria-hidden="true"/><span aria-hidden="true"/><strong>Тёмная</strong></button></div></section>
          <InterfaceSettings/>
        </>}

      </main>
    </div>

  </div>
  <LoadingOverlay active={connectionQuery.isFetching||(area==='promotions'&&(actionsQuery.isFetching||memberProductsQuery.isFetching||candidateProductsQuery.isFetching))||(area==='stocks'&&warehouseTab==='stocks'&&stocksQuery.isFetching)} label="Загружаем данные…"/>
  <Accounts open={accountsOpen} onClose={()=>setAccountsOpen(false)} onChanged={async()=>{clearScope();setActiveActionId(null);setAutoAddDate(null);await client.cancelQueries({predicate:q=>q.queryKey[0]!=='accounts'});client.removeQueries({predicate:q=>q.queryKey[0]!=='accounts'});await connectionQuery.refetch()}}/>
  {operationRequested&&<OperationPanel storeName={accountsQuery.data?.items.find(item=>item.id===(operationPlan?.account_id??accountsQuery.data?.active_id))?.name??activeStoreName} key={operationGeneration.current} operation={operationRequested} updating={role==='member'} preparing={operationBusy} error={operationError} plan={operationPlan} onClose={()=>{operationGeneration.current++;setOperationRequested(null);setOperationPlan(null);setOperationError(null)}} onResult={()=>{setStockResults({});void client.invalidateQueries({queryKey:['catalog']});void client.invalidateQueries({queryKey:['mutation-history']})}}/>}
  </>
}

function ScenarioControl({ scenario, onChange }: { scenario: Scenario; onChange: (next: Scenario) => void }) {
  return <div className="scenario-control"><Text as="label" htmlFor="mock-scenario">Сценарий MOCK</Text>
    <Select.Root value={scenario} onValueChange={value => onChange(value as Scenario)}>
      <Select.Trigger id="mock-scenario" />
      <Select.Content><Select.Item value="ready">Данные загружены</Select.Item><Select.Item value="empty">Нет данных</Select.Item><Select.Item value="partial">Частичная загрузка</Select.Item><Select.Item value="error">Ошибка сервера</Select.Item></Select.Content>
    </Select.Root>
    <Text size="1" color="gray">Выбор запрашивает соответствующее состояние локального API.</Text>
  </div>
}

function RefreshControl({time,busy,onClick,label='Обновить',disabled=false}:{time:string|null;busy:boolean;onClick:()=>void;label?:string;disabled?:boolean}) {
  return <div className="refresh-control"><Button variant="outline" disabled={disabled||busy} onClick={onClick}><Icon name="refresh" className={busy?'is-spinning':''}/>{busy?'Обновляем…':label}</Button>
    {time&&<span>Обновлено: {time}</span>}</div>
}

function StatusFilter({value,onChange}:{value:string;onChange:(next:string)=>void}) {
  return <div className="workspace-tools"><label className="filter-label">Статус акции
    <Select.Root value={value} onValueChange={onChange}><Select.Trigger aria-label="Фильтр статуса акции" />
      <Select.Content><Select.Item value="all">Все статусы</Select.Item><Select.Item value="active">Активна</Select.Item><Select.Item value="upcoming">Скоро</Select.Item><Select.Item value="ended">Завершена</Select.Item><Select.Item value="unknown">Статус неизвестен</Select.Item></Select.Content>
    </Select.Root>
  </label></div>
}

function StockValueInput({ value, onChange,stockMode,onModeChange }: { value: string; onChange: (value: string) => void;stockMode:'set'|'add';onModeChange:(value:'set'|'add')=>void }) {
  return <div className="stock-value"><label>Способ изменения<select autoComplete="off" aria-label="Способ изменения остатка" value={stockMode} onChange={event=>onModeChange(event.target.value as 'set'|'add')}><option value="set">Установить количество</option><option value="add">Добавить количество</option></select></label><label htmlFor="stock-value">{stockMode==='add'?'Добавить к доступному количеству, шт.':'Новое доступное количество, шт.'}
    <TextField.Root autoComplete="off" id="stock-value" inputMode="numeric" type="text" value={value} onChange={event => onChange(event.target.value)} placeholder="Целое число от 0" /></label>
  </div>
}
