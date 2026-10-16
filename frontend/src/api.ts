import {formatPrice} from './priceFormat'

export type Scenario = 'ready' | 'empty' | 'partial' | 'error'
export type RuntimeMode = 'MOCK' | 'LIVE_READ'
export type ActionStatus = 'active' | 'upcoming' | 'ended' | 'unknown'
export type ProductRole = 'member' | 'candidate'
export type CatalogScope = 'actions' | 'products' | 'stocks'

export type Connection = {
  mode: RuntimeMode
  configured: boolean
  read_only: boolean
  message: string
}
export type ConnectionCheck = {
  status: 'ok'
  read_only: true
  expires_at: string | null
  role_count: number
  methods_count: number
}
export type Action = {
  id: string
  name: string
  auto_add: boolean
  status: ActionStatus
  start_at: string | null
  end_at: string | null
  auto_add_dates?: string[] | null
  member_count?: number | null
  candidate_count?: number | null
}
export type Product = {
  id: string
  action_id: string
  offer_id: string | null
  name: string
  role: ProductRole
  price: string | null
  action_price: string | null
  min_price: string | null
  max_price: string | null
  elastic: boolean | null
  current_boost?: string | null
  auto_add_at: string | null
  product_id?: string | null
  sku?: string | null
  currency?: string | null
  currency_assumed?: boolean
  auto_add_discount?: string | null
  money_currencies?: Record<string, string | null>
  money_assumed_fields?: string[]
  price_min_elastic?: string | null
  price_max_elastic?: string | null
  max_discount_price?: string | null
  min_seller_price?: string | null
  max_action_price?: string | null
  minimum_discount?: string | null
  maximum_discount?: string | null
  actual_discount?: string | null
}
export type Stock = {
  id: string
  offer_id: string | null
  name: string
  warehouse_id: string
  warehouse_name: string
  available: number | null
  reserved: number | null
  present: number | null
  product_id?: string | null
  sku?: string | null
}
export type Catalog = {
  mode: RuntimeMode
  fetched_at: string
  complete: boolean
  warnings: string[]
  actions: Action[]
  products: Product[]
  stocks: Stock[]
}
export type DiscountFilterResult = { ids: string[]; matched: number; unknown: number; total: number }

export async function filterDiscount(items: Product[], threshold: string): Promise<DiscountFilterResult> {
  const response = await fetch('/api/discount-filter', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items,threshold})})
  if (!response.ok) throw new Error('Не удалось проверить скидку. Введите конечный процент, до 6 знаков после запятой.')
  return response.json() as Promise<DiscountFilterResult>
}
export type CatalogOptions = {
  scope?: CatalogScope
  action_id?: string
  role?: ProductRole
  auto_add?: boolean
  auto_add_date?: string
}
export type PreviewRow = {
  id: string
  offer_id: string
  name: string
  before: string | null
  after: string | null
  diff: string | null
  error: string | null
  currency?: string | null
}
export type PreviewSource = {
  mode: RuntimeMode
  scenario: Scenario
  fetched_at: string
  action_id: string | null
  action?: Action | null
  items: (Product | Stock)[]
}
export type Preview = {
  id: string
  hash: string
  kind: 'prices' | 'stocks'
  created_at: string
  complete: boolean
  valid: boolean
  rows: PreviewRow[]
  errors: string[]
  source: PreviewSource
}
export type Draft = {
  id: string
  status: 'DRAFT'
  mode: 'MOCK'
  created_at: string
  plan: Preview
}
export type PreviewRequest = {
  kind: 'prices' | 'stocks'
  ids: string[]
  action_id?: string | null
  mode: 'percent' | 'fixed' | 'manual'
  value: string
  rounding: '0.01' | '1' | '10' | '100'
  floor?: string | null
  scenario: Scenario
  role?: ProductRole
  auto_add?: boolean
  auto_add_date?: string | null
  stock_mode?: 'set' | 'add'
}

export function safeErrorMessage(detail: unknown): string {
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (Array.isArray(detail)) {
    const messages = detail.flatMap(item => {
      if (typeof item === 'string' && item.trim()) return [item.trim()]
      if (typeof item !== 'object' || item === null) return []
      const record = item as Record<string, unknown>
      const message = typeof record.msg === 'string' ? record.msg : typeof record.message === 'string' ? record.message : null
      return message ? [message] : []
    })
    return messages.length ? messages.join('; ') : 'Сервер вернул ошибку проверки данных.'
  }
  if (typeof detail === 'object' && detail !== null) {
    const record = detail as Record<string, unknown>
    if (typeof record.detail === 'string') return record.detail
    if (typeof record.message === 'string') return record.message
    if (typeof record.msg === 'string') return record.msg
  }
  return 'Сервер вернул ошибку в непредвиденном формате.'
}

export type VendorError={code:string;message:string;reason?:string;reason_ru?:string;severity?:'warning';original_unavailable?:boolean}
export class ApiError extends Error {
  readonly code?:string
  readonly reason?:string
  readonly reason_ru?:string
  constructor(detail:unknown,status:number){
    const record=typeof detail==='object'&&detail!==null&&!Array.isArray(detail)?detail as Record<string,unknown>:{}
    const message=typeof record.reason_ru==='string'&&record.reason_ru?record.reason_ru:safeErrorMessage(detail)||`Ошибка HTTP ${status}`
    super(message+(typeof record.reason==='string'&&record.reason&&record.reason!==message?` Оригинал Ozon: ${record.reason}`:''))
    this.name='ApiError'
    this.code=typeof record.code==='string'?record.code:undefined
    this.reason=typeof record.reason==='string'?record.reason:undefined
    this.reason_ru=typeof record.reason_ru==='string'?record.reason_ru:undefined
  }
}
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = typeof body === 'object' && body !== null && !Array.isArray(body) && 'detail' in body
      ? body.detail
      : body
    throw new ApiError(detail,response.status)
  }
  return body as T
}

export type AccountItem={id:string;name:string;client_id?:string}
export type AccountList={mode:RuntimeMode;items:AccountItem[];active_id:string|null;revision:number;legacy_available:boolean}
export const getAccounts=(signal?:AbortSignal)=>request<AccountList>('/api/accounts',{signal})
export const accountAction=(action:string,id?:string,data?:Record<string,string>)=>request<AccountList>('/api/accounts',{method:'POST',body:JSON.stringify({action,id:id??null,...data})})
export type ShopOperation='prices'|'stocks'|'action_add'|'action_remove'|'auto_add'|'auto_remove'
export type MutationEffect={id:string;card_price:{before:string;after:string;currency:string;application?:'possible';accepted_values?:string[]}|null;card_unchanged?:{before:string;currency:string};currency_binding_fields:string[]}
export type MutationPlan={id:string;hash:string;operation:ShopOperation;mode:RuntimeMode;account_id:string;valid:boolean;blocked_reason:string|null;expires_in:number;api_profile?:'STABLE'|'LEGACY_2026_10_09'|'V2_2026_10_13';api_path?:string;contract_valid_until?:string|null;rollback_of?:string|null;effects?:MutationEffect[];rows:{id:string;name:string;sku?:string|null;offer_id:string;warehouse_id?:string|null;before:string|number|null;after:string|number|null;currency:string|null}[]}
export type MutationResult={id:string;operation?:ShopOperation|null;operation_label?:string;account_id?:string;mode?:RuntimeMode;status:string;message?:string;recovered?:boolean;created_at?:string|null;attempted_at?:string|null;can_rollback?:boolean;can_reconcile?:boolean;rollback_blocked_reason?:string|null;rollback_of?:string|null;transport_error?:VendorError|null;plan_rows?:MutationPlan['rows'];rows?:{id:string;status:string;message?:string;before?:string|number|null;after?:string|number|null;actual?:string|number|null;actual_member?:boolean|null;actual_currency?:string|null;readback_complete?:boolean;card_actual?:string|null;card_changed?:boolean|null;card_currency?:string|null;currency?:string|null;name?:string;sku?:string|null;offer_id?:string;warehouse_id?:string|null;accepted?:boolean|null;errors?:VendorError[]}[]}
export const prepareRollback=(operationId:string)=>request<MutationPlan>('/api/mutations/rollback-preview',{method:'POST',body:JSON.stringify({operation_id:operationId})})
export const prepareMutation=(operation:ShopOperation,payload:PreviewRequest)=>request<MutationPlan>('/api/mutations/preview',{method:'POST',body:JSON.stringify({operation,request:payload})})
export const executeMutation=(plan:MutationPlan)=>request<MutationResult>('/api/mutations/execute',{method:'POST',body:JSON.stringify({plan_id:plan.id,plan_hash:plan.hash,confirmed:true})})
export const reconcileMutation=(plan:MutationPlan)=>request<MutationResult>('/api/mutations/reconcile',{method:'POST',body:JSON.stringify({plan_id:plan.id,plan_hash:plan.hash})})
export const reconcileHistory=(operationId:string)=>request<MutationResult>('/api/mutations/reconcile-history',{method:'POST',body:JSON.stringify({operation_id:operationId})})
export const getMutationHistory=(signal?:AbortSignal)=>request<{items:MutationResult[]}>('/api/operations/history',{signal})

export type FinanceMetric = {label:string;amount:string|null;currency:string|null}
export type FinanceResult = {mode:RuntimeMode;fetched_at:string;complete:boolean;warnings:string[];metrics:FinanceMetric[];columns:{key:string;label:string;kind:string}[];rows:Record<string,string|null>[];details:Record<string,string|null>[];source:string[];error_code?:string;error?:VendorError;report?:{code:string;status:string;file:string|null;created_at:string|null;expires_at:string|null;error?:VendorError}}
export type FinanceQuery = {view:string;date_from:string;date_to:string;postings?:string[];report_code?:string|null}
export const getFinance = (body:FinanceQuery, signal?:AbortSignal) => request<FinanceResult>('/api/finance',{method:'POST',body:JSON.stringify(body),signal})
export const createFinanceReport = (kind:string,month:string) => request<{code:string;mode:RuntimeMode;reused:boolean}>('/api/finance/reports',{method:'POST',body:JSON.stringify({kind,month})})
export const getAdvertising = (body:FinanceQuery,signal?:AbortSignal) => request<{mode:RuntimeMode;complete:boolean;columns:string[];rows:string[][];warnings:string[]}>('/api/finance/advertising',{method:'POST',body:JSON.stringify(body),signal})
export const calculateProfit = (revenue:string,cost:string,expenses:string) => request<{profit:string;margin:string|null;source:string;currency:string}>('/api/finance/profit',{method:'POST',body:JSON.stringify({revenue,cost,expenses})})
export type PromotionReview = {mode:RuntimeMode;fetched_at:string;complete:boolean;warnings:string[];items:Product[];unknown:number;total:number;purpose:'prepare_add'|'prepare_remove';executable:false}
export const preparePromotionReview = (body:{action_id:string;role:ProductRole;auto_add:boolean;auto_add_date:string|null;threshold:string}) => request<PromotionReview>('/api/promotion-review',{method:'POST',body:JSON.stringify(body)})

export const getConnection = (signal?: AbortSignal) =>
  request<Connection>('/api/connection', { signal })

export const checkConnection = (signal?: AbortSignal) =>
  request<ConnectionCheck>('/api/connection/check', { signal })

export const getCatalog = (scenario: Scenario, options: CatalogOptions = {}, signal?: AbortSignal) => {
  const params = new URLSearchParams({ scenario, scope: options.scope ?? 'actions' })
  if (options.action_id !== undefined) params.set('action_id', options.action_id)
  if (options.role !== undefined) params.set('role', options.role)
  if (options.auto_add !== undefined) params.set('auto_add', String(options.auto_add))
  if (options.auto_add_date !== undefined) params.set('auto_add_date', options.auto_add_date)
  return request<Catalog>(`/api/catalog?${params}`, { signal })
}

export const getDrafts = (signal?: AbortSignal) => request<{ items: Draft[] }>('/api/drafts', { signal })
export const createPreview = (payload: PreviewRequest, live = false) =>
  request<Preview>(live ? '/api/read-preview' : '/api/preview', { method: 'POST', body: JSON.stringify(payload) })
export const saveDraft = (plan: Preview) =>
  request<Draft>('/api/drafts', {
    method: 'POST',
    body: JSON.stringify({ plan_id: plan.id, plan_hash: plan.hash }),
  })

type DatePreference='ru'|'iso'
type TimeZonePreference='local'|'Europe/Moscow'|'UTC'
const displayPreference=(key:string)=>typeof document==='undefined'?undefined:document.documentElement.dataset[key]
const datePreference=():DatePreference=>displayPreference('uiDateFormat')==='iso'?'iso':'ru'
const timeZonePreference=():TimeZonePreference=>{
  const value=displayPreference('uiTimeZone')
  return value==='Europe/Moscow'||value==='UTC'?value:'local'
}
const preferredTimeZone=()=>timeZonePreference()==='local'?undefined:timeZonePreference()
const isDateOnly=(value:string)=>/^\d{4}-\d{2}-\d{2}$/.test(value)
const parseDisplayDate=(value:string)=>new Date(isDateOnly(value)?`${value}T00:00:00Z`:value)
const isoParts=(date:Date,timeZone:string|undefined,withTime:boolean)=>{
  const parts=new Intl.DateTimeFormat('en-CA',{year:'numeric',month:'2-digit',day:'2-digit',...(withTime?{hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}:{}),timeZone}).formatToParts(date)
  const part=(type:Intl.DateTimeFormatPartTypes)=>parts.find(item=>item.type===type)?.value??''
  return `${part('year')}-${part('month')}-${part('day')}${withTime?` ${part('hour')}:${part('minute')}:${part('second')}`:''}`
}

export const formatDate = (value: string | null | undefined) => {
  if (!value) return 'Неизвестно'
  const date=parseDisplayDate(value)
  if (!Number.isFinite(date.getTime())) return 'Неизвестно'
  const timeZone=isDateOnly(value)?'UTC':preferredTimeZone()
  if(datePreference()==='iso')return isoParts(date,timeZone,false)
  return new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'2-digit',year:'numeric',timeZone}).format(date)
}

export const formatShortDate=(value:string|null|undefined)=>{
  if(!value)return 'Нет даты'
  const date=parseDisplayDate(value)
  if(!Number.isFinite(date.getTime()))return 'Нет даты'
  const timeZone=isDateOnly(value)?'UTC':preferredTimeZone()
  if(datePreference()==='iso')return isoParts(date,timeZone,false)
  return new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'short',timeZone}).format(date)
}

export const formatTime=(value:string|null|undefined)=>{
  if(!value)return 'Время недоступно'
  const date=new Date(value)
  if(!Number.isFinite(date.getTime()))return 'Время недоступно'
  return new Intl.DateTimeFormat('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit',timeZone:preferredTimeZone()}).format(date)
}

export const formatDateTime=(value:string|null|undefined)=>{
  if(!value)return 'Дата недоступна'
  const date=new Date(value)
  if(!Number.isFinite(date.getTime()))return 'Дата недоступна'
  if(datePreference()==='iso')return isoParts(date,preferredTimeZone(),true)
  return new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'medium',timeZone:preferredTimeZone()}).format(date)
}

export const formatTimeZoneLabel=()=>timeZonePreference()==='Europe/Moscow'?'МСК':timeZonePreference()==='UTC'?'UTC':'местное время'

export {formatPrice}

export const formatMoney = (value: string | null | undefined, currency: string | null | undefined = 'RUB') => {
  if (value === null || value === undefined || value === '') return 'Нет данных'
  const amount = value.replace('.', ',')
  if (currency === 'RUB') return `${amount} ${displayPreference('uiCurrencyDisplay')==='code'?'RUB':'₽'}`
  if (!currency) return `${amount} · валюта не указана`
  return `${amount} ${currency}`
}

export const formatQuantity = (value: number | null | undefined) =>
  value === null || value === undefined ? 'Нет данных' : new Intl.NumberFormat('ru-RU').format(value)
