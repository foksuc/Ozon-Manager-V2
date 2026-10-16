import { useEffect, useRef, useState } from 'react'
import { Button, Callout, Dialog } from '@radix-ui/themes'
import type { RuntimeMode } from './api'
import type { SellerRecord, SellerValue } from './sellerApi'
import { sellerText } from './sellerApi'
import { ScrollTable } from './TableTools'
import { warehouseContracts,warehouseRead,warehousePreview,warehouseExecute,warehouseReconcile,warehouseHistory,warehouseLabel } from './warehouseReportsApi'
import type { FieldSchema,WarehouseContracts,WarehouseReadResult,WarehousePlan,WarehouseOutcome } from './warehouseReportsApi'
import './warehouse-reports.css'
import {isVendorError,VendorErrorNotice} from './VendorErrorNotice'

type Draft=string|boolean|Draft[]|{[key:string]:Draft}
function isObject(value:Draft|undefined):value is {[key:string]:Draft}{return typeof value==='object'&&value!==null&&!Array.isArray(value)}
function initial(schema:FieldSchema):Draft{
 if(schema.type==='object')return Object.fromEntries((schema.required??[]).map(key=>[key,initial(schema.properties?.[key]??{})]))
 if(schema.type==='array')return schema.items?.type==='object'?[initial(schema.items)]:[]
 if(schema.type==='boolean')return false
 return ''
}
function operationInitial(operation:string,schema:FieldSchema):Draft{
 if(operation==='PRODUCT_LIST')return{filter:{visibility:'ALL'},limit:'1000',last_id:''}
 if(operation==='PRODUCT_PRICES')return{filter:{visibility:'ALL'},limit:'1000',cursor:''}
 if(operation==='PRODUCT_ATTRIBUTES')return{filter:{visibility:'ALL'},limit:'1000',last_id:''}
 if(operation==='REPORT_LIST')return{page:'1',page_size:'100',report_type:'ALL'}
 if(operation==='CATEGORY_TREE')return{language:'RU'}
 return initial(schema)
}
function serialize(value:Draft,schema:FieldSchema):SellerValue{
 if(schema.type==='object'){const record=isObject(value)?value:{};return Object.fromEntries(Object.entries(record).filter(([key,item])=>schema.required?.includes(key)||item!==''&&!(Array.isArray(item)&&!item.length)).map(([key,item])=>[key,serialize(item,schema.properties?.[key]??{})]))}
 if(schema.type==='array')return(Array.isArray(value)?value:[]).map(item=>serialize(item,schema.items??{}))
 if(schema.type==='integer'){const integer=Number(value);if(!/^-?\d+$/.test(String(value))||!Number.isSafeInteger(integer))throw new Error('Введите точный целый ID в поддерживаемом диапазоне');return integer}
 if(schema.type==='boolean')return Boolean(value)
 return String(value)
}
const warehouseOptionNames:Record<string,string>={ALL:'Все',VISIBLE:'Видимые',INVISIBLE:'Невидимые',EMPTY_STOCK:'Без остатков',NOT_MODERATED:'Не прошли модерацию',MODERATED:'Прошли модерацию',DISABLED:'Отключены',STATE_FAILED:'Ошибка обработки',READY_TO_SUPPLY:'Готовы к поставке',VALIDATION_STATE_PENDING:'Проверка данных',VALIDATION_STATE_FAIL:'Ошибка проверки данных',VALIDATION_STATE_SUCCESS:'Данные проверены',TO_SUPPLY:'Ожидают поставки',IN_SALE:'В продаже',REMOVED_FROM_SALE:'Сняты с продажи',OVERPRICED:'Завышенная цена',CRITICALLY_OVERPRICED:'Критически завышенная цена',EMPTY_BARCODE:'Без штрихкода',BARCODE_EXISTS:'Со штрихкодом',QUARANTINE:'Карантин',ARCHIVED:'Архив',OVERPRICED_WITH_STOCK:'Завышенная цена с остатками',PARTIAL_APPROVED:'Частично одобрены',AUTO_ARCHIVED:'Архивированы автоматически',MANUAL_ARCHIVED:'Архивированы вручную',SEASONAL_AUTO_ARCHIVED:'Сезонный автоматический архив',VISIBLE_WITH_FBO_STOCK:'Видимые с остатками FBO',DEFAULT:'По умолчанию',RU:'Русский',EN:'Английский',TR:'Турецкий',ZH_HANS:'Китайский',mm:'мм',cm:'см',in:'дюймы',g:'г',kg:'кг',lb:'фунты'}
function SchemaFields({schema,value,onChange,name='Параметры',fieldKey='',required=false}:{schema:FieldSchema;value:Draft|undefined;onChange:(value:Draft)=>void;name?:string;fieldKey?:string;required?:boolean}){
 if(schema.type==='object'){
  const record=isObject(value)?value:{};const keys=Object.keys(schema.properties??{});const important=['offer_id','sku','product_id','name','description_category_id','type_id','currency_code','price','old_price','min_price','vat','items','prices','filter','attributes'];keys.sort((a,b)=>(important.includes(a)?important.indexOf(a):100)-(important.includes(b)?important.indexOf(b):100))
  return <fieldset className="warehouse-fieldset"><legend>{name}</legend><div className="warehouse-fields">{keys.map(key=>{const child=schema.properties?.[key]??{};const must=schema.required?.includes(key)??false;const active=key in record;return <div key={key} className={`warehouse-field ${child.type==='object'||child.type==='array'?'warehouse-field-wide':''} ${!must?'is-optional':''}`} >{!must&&<label className={`warehouse-optional ${active?'is-enabled':''}`}><input aria-label={`Использовать параметр: ${warehouseLabel(key,child)}`} type="checkbox" checked={active} onChange={event=>{const next={...record};if(event.target.checked)next[key]=initial(child);else delete next[key];onChange(next)}}/><span className={active?'sr-only':undefined}>{warehouseLabel(key,child)}</span></label>}{(must||active)&&<SchemaFields schema={child} fieldKey={key} name={warehouseLabel(key,child)} value={record[key]} required={must} onChange={item=>onChange({...record,[key]:item})}/>}</div>})}</div></fieldset>
 }
 if(schema.type==='array'){
  const list=Array.isArray(value)?value:[];const child=schema.items??{}
  if(child.type==='object')return <fieldset className="warehouse-fieldset"><legend>{name}{required?' *':''}</legend>{list.map((item,index)=><div className="warehouse-array-item" key={index}><SchemaFields schema={child} name={`${name} ${index+1}`} value={item} onChange={next=>onChange(list.map((old,i)=>i===index?next:old))}/><Button type="button" size="1" variant="soft" color="red" onClick={()=>onChange(list.filter((_,i)=>i!==index))}>Убрать строку</Button></div>)}<Button type="button" size="1" variant="soft" disabled={list.length>=Math.min(schema.maxItems??100,100)} onClick={()=>onChange([...list,initial(child)])}>Добавить строку</Button></fieldset>
  return <label>{name}{required?' *':''}<textarea rows={3} value={list.map(String).join('\n')} placeholder="По одному значению на строку" onChange={event=>onChange(event.target.value.split(/\n/).filter(Boolean))}/></label>
 }
 if(schema.type==='boolean')return <label className="warehouse-optional"><input type="checkbox" checked={Boolean(value)} onChange={event=>onChange(event.target.checked)}/>{name}</label>
 const enums=schema.enum??(fieldKey==='currency_code'?['RUB','BYN','KZT','EUR','USD','CNY']:fieldKey==='vat'?['0','0.05','0.07','0.1','0.2','0.22']:fieldKey==='dimension_unit'?['mm','cm','in']:fieldKey==='weight_unit'?['g','kg','lb']:fieldKey==='language'?['RU','EN']:[])
 return <label>{name}{required?' *':''}{enums.length?<select value={String(value??'')} onChange={event=>onChange(event.target.value)}><option value="">Выберите</option>{enums.map(item=><option key={item} value={item}>{warehouseOptionNames[item]??item}</option>)}</select>:<input required={required} inputMode={schema.type==='integer'?'numeric':undefined} type={schema.format==='date-time'?'text':fieldKey==='date_from'||fieldKey==='date_to'||fieldKey==='from'||fieldKey==='to'?'date':'text'} placeholder={schema.format==='date-time'?'2026-10-09T00:00:00Z':undefined} value={String(value??'')} onChange={event=>onChange(event.target.value)}/>}</label>
}

export function WarehouseTable({rows,label,tableId}:{rows:SellerRecord[];label:string;tableId:string}){
 const keys=[...new Set(rows.flatMap(row=>Object.keys(row)))].filter(key=>key!=='file'&&key!=='params')
 if(!rows.length)return null
 return <ScrollTable tableId={tableId} data-warehouse-table={tableId} className="compact-table warehouse-table" aria-label={label} tabIndex={0}><table><thead><tr>{keys.map(key=><th key={key} data-table-filter-key={key}>{warehouseLabel(key)}</th>)}</tr></thead><tbody>{rows.map((row,index)=><tr key={index}>{keys.map(key=><td key={key} data-table-filter-key={key} className={isStructured(row[key])?'warehouse-structured-cell':technicalFields.has(key)?'warehouse-technical-cell':undefined}><WarehouseValue fieldKey={key} value={row[key]}/></td>)}</tr>)}</tbody></table></ScrollTable>
}
const statuses:Record<string,string>={SUCCESS:'Успешно',PARTIAL:'Частично',REJECTED:'Отклонено',UNKNOWN_RESULT:'Результат неизвестен',PENDING:'Выполняется',ACCEPTED:'Принято Ozon',imported:'Обработан',pending:'В очереди',processing:'Выполняется',waiting:'В очереди',success:'Готов',failed:'Ошибка',skipped:'Пропущен'}
const technicalFields=new Set(['plan_id','plan_hash','task_id','code'])
const isStructured=(value:SellerValue|undefined):value is SellerRecord|SellerValue[]=>value!==null&&typeof value==='object'
const shortTechnical=(value:string)=>value.length>22?`${value.slice(0,10)}…${value.slice(-7)}`:value
function structuredSummary(value:SellerRecord|SellerValue[]):string{
 const entries=Array.isArray(value)?value.map((item,index)=>[String(index),item]as const):Object.entries(value)
 if(!entries.length)return Array.isArray(value)?'Нет элементов':'Нет параметров'
 const preferred=['price','marketing_seller_price','min_price','currency_code','currency','amount','value']
 const scalar=entries.filter(([,item])=>!isStructured(item)).sort(([left],[right])=>(preferred.indexOf(left)<0?99:preferred.indexOf(left))-(preferred.indexOf(right)<0?99:preferred.indexOf(right))).slice(0,2)
 if(scalar.length){const text=scalar.map(([key,item])=>`${Array.isArray(value)?`Элемент ${Number(key)+1}`:warehouseLabel(key)}: ${sellerText(item)}`).join(' · ');return entries.length>scalar.length?`${text} · ещё ${entries.length-scalar.length}`:text}
 return Array.isArray(value)?`${entries.length} ${entries.length===1?'элемент':'элементов'}`:`${entries.length} ${entries.length===1?'параметр':'параметров'}`
}
function StructuredValue({value}:{value:SellerRecord|SellerValue[]}){
 if(isVendorError(value))return <VendorErrorNotice error={value}/>
 const entries=Array.isArray(value)?value.map((item,index)=>[String(index),item]as const):Object.entries(value)
 return <details className="warehouse-value-details"><summary>{structuredSummary(value)}</summary><dl>{entries.map(([key,item])=><div key={key}><dt>{Array.isArray(value)?`Элемент ${Number(key)+1}`:warehouseLabel(key)}</dt><dd>{isStructured(item)?<StructuredValue value={item}/>:sellerText(item)}</dd></div>)}</dl></details>
}
function WarehouseValue({fieldKey,value}:{fieldKey:string;value:SellerValue|undefined}){
 if(fieldKey==='download_url')return typeof value==='string'?<a href={value} target="_blank" rel="noreferrer noopener" referrerPolicy="no-referrer">Скачать отчёт</a>:<>—</>
 if((fieldKey==='status'||fieldKey==='report_status')&&typeof value==='string')return <>{statuses[value]??value}</>
 if(fieldKey.endsWith('_fields')&&Array.isArray(value))return <>{value.map(item=>warehouseLabel(String(item))).join(', ')}</>
 if(technicalFields.has(fieldKey)&&typeof value==='string')return <span className="warehouse-technical-value" title={value} aria-label={value}>{shortTechnical(value)}</span>
 if(isStructured(value))return <StructuredValue value={value}/>
 return <>{sellerText(value)}</>
}
function requestRows(value:SellerValue,prefix=''):SellerRecord[]{
 if(value&&typeof value==='object')return Object.entries(value).flatMap(([key,item])=>requestRows(item,prefix?`${prefix} → ${warehouseLabel(key)}`:warehouseLabel(key)))
 return [{parameter:prefix,value}]
}

export function WarehouseTool({mode,scopeKey,operations,title,initialOperation}:{mode:RuntimeMode;scopeKey:string;operations:string[];title:string;initialOperation?:string}){
 const [contracts,setContracts]=useState<WarehouseContracts|null>(null);const [operation,setOperation]=useState<string>(initialOperation??operations[0]??'');const [draft,setDraft]=useState<Draft>({});const [data,setData]=useState<WarehouseReadResult|null>(null);const [plan,setPlan]=useState<WarehousePlan|null>(null);const [outcome,setOutcome]=useState<WarehouseOutcome|null>(null);const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [confirmed,setConfirmed]=useState(false);const [attempted,setAttempted]=useState(false);const [expires,setExpires]=useState(0);const [history,setHistory]=useState<Awaited<ReturnType<typeof warehouseHistory>>|null>(null);const generation=useRef(0)
 useEffect(()=>{let alive=true;void warehouseContracts().then(value=>{if(alive)setContracts(value)}).catch(error=>{if(alive)setError(error instanceof Error?error.message:'Не удалось загрузить формы')});return()=>{alive=false}},[])
 useEffect(()=>{generation.current++;setDraft(operationInitial(operation,contracts?.operations[operation]?.request??{}));setData(null);setPlan(null);setOutcome(null);setHistory(null);setConfirmed(false);setAttempted(false);setError('');setBusy(false)},[scopeKey,operation,contracts])
 useEffect(()=>{if(!plan)return;const timer=window.setInterval(()=>setExpires(Math.max(0,Math.floor((expiresAt.current-Date.now())/1000))),1000);return()=>window.clearInterval(timer)},[plan])
 const expiresAt=useRef(0);const contract=contracts?.operations[operation];const blocked=mode==='LIVE_READ'?contracts?.live_blocked[operation]:undefined
 const run=async(work:()=>Promise<void>)=>{if(busy)return;const token=generation.current;setBusy(true);setError('');try{await work()}catch(error){if(token===generation.current)setError(error instanceof Error?error.message:'Не удалось выполнить операцию')}finally{if(token===generation.current)setBusy(false)}}
 const load=async(next?:SellerRecord)=>{const token=generation.current;const body=next??serialize(draft,contract?.request??{})as SellerRecord;if(contract?.read){const response=await warehouseRead(operation,body);if(token===generation.current)setData(response)}else{const response=await warehousePreview(operation,body);if(token===generation.current){setPlan(response);setOutcome(null);setConfirmed(false);setAttempted(false);setExpires(response.expires_in);expiresAt.current=Date.now()+response.expires_in*1000}}}
 const send=async()=>{if(!plan||attempted)return;const token=generation.current;setAttempted(true);try{const result=await warehouseExecute(plan);if(token===generation.current)setOutcome(result)}catch(error){if(token===generation.current)setOutcome({plan_id:plan.plan_id,plan_hash:plan.plan_hash,status:'UNKNOWN_RESULT',success_count:0,error_count:0,items:[]});throw error}}
 return <section className="warehouse-tool"><div className="warehouse-tool-heading"><h2>{title}</h2><label>Действие<select value={operation} disabled={busy} onChange={event=>setOperation(event.target.value)}>{operations.map(name=><option value={name} key={name}>{contracts?.operations[name]?.label??name}</option>)}</select></label></div>{error&&<Callout.Root color="red" role="alert"><Callout.Text>{error}</Callout.Text></Callout.Root>}{blocked&&<Callout.Root color="amber"><Callout.Text>{blocked}</Callout.Text></Callout.Root>}{contract&&<form onSubmit={event=>{event.preventDefault();void run(()=>load())}}><SchemaFields schema={contract.request} value={draft} onChange={setDraft}/>{operation==='PRODUCT_UPDATE_PICTURES'&&<p className="warehouse-note">Передайте все изображения карточки: прежние изображения, не включённые в список, будут заменены.</p>}<Button type="submit" disabled={busy||Boolean(blocked)}>{busy?'Подготовка…':contract.read?'Загрузить':'Подготовить план'}</Button></form>}{data&&<><WarehouseTable rows={data.rows.length||operation!=='PRODUCT_LIMITS'||!Object.keys(data.data).length?data.rows:[data.data]} label={title} tableId={`warehouse-${operation.toLowerCase()}`}/>{data.next_request&&<Button variant="soft" disabled={busy} onClick={()=>void run(()=>load(data.next_request!))}>Следующая страница</Button>}<p className="warehouse-note">{data.complete?'Данные загружены':'Загружена часть данных'}</p></>}
 <div className="warehouse-attempts"><Button variant="soft" disabled={busy} onClick={()=>void run(async()=>{const token=generation.current;const loaded=await warehouseHistory();if(token===generation.current)setHistory(loaded)})}>Отправки и сверка</Button>{history&&<><WarehouseTable rows={history.items.map(item=>({...item}))} label="Отправки склада и отчётов" tableId="warehouse-history"/>{history.items.map(item=>{const reference=String(item.task_id??item.code??item.plan_id);return <div key={item.plan_id} className="warehouse-attempt"><span><strong>{item.task_id?'Задача':item.code?'Отчёт':'Операция'}</strong> <span className="warehouse-technical-value" title={reference} aria-label={reference}>{shortTechnical(reference)}</span><small>{statuses[item.status]??item.status}</small></span><Button size="1" variant="soft" disabled={busy||item.can_reconcile===false} onClick={()=>void run(async()=>{const token=generation.current;await warehouseReconcile(item);const loaded=await warehouseHistory();if(token===generation.current)setHistory(loaded)})}>Проверить результат</Button></div>})}{history.requires_manual_reconciliation&&<p role="alert">После перезапуска сохранились неизвестные отправки. Проверьте задачи и список отчётов в Seller. Подробности доступны в зашифрованной истории; исполняемый план прежнего сеанса не восстановлен.</p>}</>}</div>
 <Dialog.Root open={Boolean(plan)} onOpenChange={open=>{if(!open&&!busy)setPlan(null)}}><Dialog.Content maxWidth="860px"><Dialog.Title>{contract?.label??title}</Dialog.Title><Dialog.Description>{plan?.api_path}. Проверьте сохранённые значения перед отправкой.</Dialog.Description>{plan&&<WarehouseTable rows={requestRows(plan.request)} label="Сохранённый план" tableId="warehouse-plan"/>}{plan&&<details className="warehouse-snapshot"><summary>Исходные данные</summary><WarehouseTable rows={requestRows(plan.before)} label="Исходный снимок" tableId="warehouse-snapshot"/></details>}{!attempted&&<><p className="warehouse-note">План действителен ещё {expires} с. Отправляется один раз.</p><label className="warehouse-optional"><input type="checkbox" checked={confirmed} onChange={event=>setConfirmed(event.target.checked)}/>Подтверждаю эти значения для активного магазина</label><Button disabled={!confirmed||busy||expires===0} onClick={()=>void run(send)}>Подтвердить отправку</Button></>}{outcome&&<><Callout.Root color={outcome.status==='SUCCESS'?'green':outcome.status==='UNKNOWN_RESULT'?'amber':'blue'}><Callout.Text>{outcome.status==='SUCCESS'?'Успешно':outcome.status==='ACCEPTED'?'Принято Ozon; часть значений не проверяется API':outcome.status==='PENDING'?'Задача выполняется':outcome.status==='UNKNOWN_RESULT'?'Результат неизвестен — повторная отправка запрещена':`Успешно: ${outcome.success_count}, Ошибки: ${outcome.error_count}`}</Callout.Text></Callout.Root><VendorErrorNotice error={outcome.transport_error}/>{outcome.verification_scope&&<p className="warehouse-note">{outcome.verification_scope}</p>}{outcome.verification_unavailable&&<p className="warehouse-note">{outcome.verification_unavailable}</p>}{outcome.task_id&&<p>Задача {outcome.task_id}</p>}{outcome.code&&<p>Код отчёта: {outcome.code}</p>}{outcome.download_url&&<a href={outcome.download_url} referrerPolicy="no-referrer" target="_blank" rel="noreferrer noopener">Скачать отчёт</a>}<WarehouseTable rows={outcome.items.map(item=>({...item}))} label="Результат по строкам" tableId="warehouse-outcome"/></>}{attempted&&<Button variant="soft" disabled={busy} onClick={()=>void run(async()=>{if(plan){const token=generation.current;const result=await warehouseReconcile(plan);if(token===generation.current)setOutcome(result)}})}>Проверить результат</Button>}<Dialog.Close><Button variant="soft" disabled={busy}>Закрыть</Button></Dialog.Close></Dialog.Content></Dialog.Root>
 </section>
}

export function WarehouseProducts({mode,scopeKey}:{mode:RuntimeMode;scopeKey:string}){
 const [tab,setTab]=useState('edit');const choices:[string,string][]=[['edit','Создание и обновление'],['catalog','Товары'],['categories','Категории и характеристики'],['tasks','Задачи и лимиты']]
 const operations=tab==='edit'?['PRODUCT_IMPORT','PRODUCT_IMPORT_SKU','PRODUCT_UPDATE_ATTRIBUTES','PRODUCT_UPDATE_PICTURES']:tab==='catalog'?['PRODUCT_LIST','PRODUCT_INFO','PRODUCT_ATTRIBUTES','PRODUCT_DESCRIPTION','PRODUCT_PICTURES']:tab==='categories'?['CATEGORY_TREE','CATEGORY_ATTRIBUTES','CATEGORY_VALUES','CATEGORY_SEARCH','CATEGORY_DEPENDENT','CATEGORY_DEPENDENT_VALUES']:['PRODUCT_IMPORT_INFO','PRODUCT_LIMITS']
 return <section className="warehouse-workspace"><nav className="subnav" aria-label="Работа с товарами">{choices.map(([key,label])=><Button key={key} variant="ghost" aria-pressed={tab===key} className={tab===key?'active':''} onClick={()=>setTab(key)}>{label}</Button>)}</nav><WarehouseTool key={tab} mode={mode} scopeKey={scopeKey} operations={operations} title={choices.find(([key])=>key===tab)?.[1]??'Товары'}/></section>
}
