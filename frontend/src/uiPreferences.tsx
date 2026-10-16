import {createContext,useContext,useEffect,useMemo,useRef,useState,type ReactNode} from 'react'
import {useQueryClient} from '@tanstack/react-query'
import {Button,Checkbox,Heading,Select,Text} from '@radix-ui/themes'
import {warehouseTableDefinitions} from './warehouseTableDefinitions'
import {warehouseLabel} from './warehouseReportsApi'
import './interface-settings.css'

export type PreferenceColumn={key:string;label:string}
type TableDefinition={label:string;columns:PreferenceColumn[]}
export type Preferences={
  density:'compact'|'comfortable'
  textSize:'small'|'normal'|'large'
  reduceMotion:boolean
  hidden:Record<string,string[]>
  autoRefresh:boolean
  refreshMinutes:5|15|30|60
  refreshVisibleOnly:boolean
  tableWrap:boolean
  stickyHeaders:boolean
  exportDelimiter:'semicolon'|'comma'|'tab'
  exportBom:boolean
  exportFilteredOnly:boolean
  dateFormat:'ru'|'iso'
  timeZone:'local'|'Europe/Moscow'|'UTC'
  currencyDisplay:'symbol'|'code'
  highContrast:boolean
  largeControls:boolean
}
const defaults:Preferences={density:'compact',textSize:'normal',reduceMotion:false,hidden:{},autoRefresh:false,refreshMinutes:15,refreshVisibleOnly:true,tableWrap:false,stickyHeaders:true,exportDelimiter:'semicolon',exportBom:true,exportFilteredOnly:true,dateFormat:'ru',timeZone:'local',currencyDisplay:'symbol',highContrast:false,largeControls:false}
const storageKey='ozon-v2-ui-preferences'
export function applyDocumentPreferences(value:Preferences){
  if(typeof document==='undefined')return
  const root=document.documentElement.dataset
  root.uiDensity=value.density
  root.uiText=value.textSize
  root.uiMotion=value.reduceMotion?'reduced':'standard'
  root.uiRefresh=value.autoRefresh?'on':'off'
  root.uiRefreshMinutes=String(value.refreshMinutes)
  root.uiRefreshVisibility=value.refreshVisibleOnly?'visible':'always'
  root.uiTableWrap=value.tableWrap?'wrap':'nowrap'
  root.uiStickyHeaders=value.stickyHeaders?'sticky':'scroll'
  root.uiExportDelimiter=value.exportDelimiter
  root.uiExportBom=value.exportBom?'on':'off'
  root.uiExportScope=value.exportFilteredOnly?'filtered':'all'
  root.uiDateFormat=value.dateFormat
  root.uiTimeZone=value.timeZone
  root.uiCurrencyDisplay=value.currencyDisplay
  root.uiContrast=value.highContrast?'high':'standard'
  root.uiControls=value.largeControls?'large':'standard'
}
const initialTables:Record<string,TableDefinition>={stocks:{label:'Склад · Остатки',columns:[{key:'sku',label:'SKU'},{key:'name',label:'Название'},{key:'offer_id',label:'Артикул'},{key:'warehouse_name',label:'Склад'},{key:'present',label:'Остаток, шт.'},{key:'reserved',label:'Резерв, шт.'},{key:'available',label:'Доступно, шт.'},{key:'new_quantity',label:'Новое кол-во, шт.'}]}}
const table=(id:string,label:string,fields:Record<string,string>)=>{initialTables[id]={label,columns:Object.entries(fields).map(([key,label])=>({key,label}))}}
const identity={sku:'SKU',name:'Название',offer_id:'Артикул'}
for(const auto of [false,true])for(const role of ['member','candidate'])table(`products-${auto?'auto':'regular'}-${role}`,`${auto?'Автодобавление':'Акции'} · ${role==='member'?'участники':'кандидаты'}`,{...identity,price:'Цена товара',...(auto?{action_price:'Цена автодобавления',auto_add_discount:'Скидка автодобавления, %',min_seller_price:'Минимум продавца'}:role==='member'?{minimum_discount:'Минимальная скидка, %',maximum_discount:'Максимальная скидка, %',actual_discount:'Скидка, %',max_price:'Цена при минимальном бустинге',min_price:'Цена при максимальном бустинге'}:{minimum_discount:'Минимальная скидка, %',maximum_discount:'Максимальная скидка, %',max_action_price:'Максимальная цена участия'})})
const sellerTables:Record<string,Record<string,string>>={'Стратегии':{id:'ID стратегии',name:'Название',enabled:'Включена',type:'Тип'},'Товары стратегии':{product_id:'ID товара',sku:'SKU',name:'Название',enabled:'Включена'},'Проверка товаров':{product_id:'ID товара',strategy_ids:'Стратегии',strategy_id:'ID стратегии',price:'Цена'},'Результаты изменения стратегии':{product_id:'ID товара',status:'Результат',error:'Ошибка'},'Остатки и ликвидность':{sku:'SKU',name:'Название',cluster_name:'Кластер',available_stock_count:'Доступно, шт.',idc:'Запас в днях'},'Оборачиваемость':{sku:'SKU',name:'Название',current_stock:'Остаток',turnover_grade:'Оборачиваемость',idc:'Запас в днях'},'Остатки по складам':{sku:'SKU',item_name:'Название',warehouse_name:'Склад',free_to_sell_amount:'Доступно, шт.',reserved_amount:'Резерв, шт.'}}
for(const [label,fields] of Object.entries(sellerTables))table(`compact-table seller-table:${label}`,label,fields)
table('analytics-catalogue','Аналитика · Выбор товаров',{sku:'SKU',name:'Название'})
table('finance-daily-chart','Финансы · Динамика по дням',{date:'День',accrued:'Начислено',paid:'Выплачено'})
export const financeTableNames:Record<string,string>={overview:'Финансы · Состав начислений',payouts:'Финансы · Выплаты',analytics:'Финансы · Сравнение периодов',types:'Финансы · Виды начислений',postings:'Финансы · Начисления по отправлениям',accruals:'Финансы · Дневные начисления',products:'Финансы · Реализация товаров','daily-products':'Финансы · Дневная реализация',report:'Финансы · Документы','daily-balance':'Финансы · Дневной баланс'}
for(const [id,label] of Object.entries(financeTableNames)){
 table(`finance-${id}`,label,{period:'Период',label:'Доход / расход',amount:'Сумма',currency:'Валюта'})
 if(['overview','payouts','analytics','postings','accruals'].includes(id))table(`finance-${id}-details`,`${label} · Детализация`,{period:'Период',label:'Доход / расход',amount:'Сумма',currency:'Валюта'})
}
table('operation-table operation-history-table:История отправок','Операции · История отправок',{created_at:'Операция',operation:'Действие',status:'Результат',details:'Построчная сверка'})
table('operation-table:План изменений','Операции · План изменений',{...identity,before:'Было',after:'Станет'})
for(const [id,definition] of Object.entries(warehouseTableDefinitions))table(id,definition.label,Object.fromEntries(definition.keys.map(key=>[key,warehouseLabel(key)])))
for(const [id,label] of [['warehouse-plan','Склад и отчёты · Сохранённый план'],['warehouse-snapshot','Склад и отчёты · Исходные данные'],['warehouse-outcome','Склад и отчёты · Результат по строкам']])table(id!,label!,{id:'ID',status:'Результат',errors:'Ошибки'})
table('warehouse-history','Склад и отчёты · Отправки и сверка',{plan_id:'Операция',status:'Результат',success_count:'Успешно',error_count:'Ошибки'})
export function readPreferences(value:string|null):Preferences{
  try{
    const raw=JSON.parse(value??'null')
    if(!raw||typeof raw!=='object')return {...defaults,hidden:{}}
    const hidden:Record<string,string[]>={}
    if(raw.hidden&&typeof raw.hidden==='object')for(const [key,columns] of Object.entries(raw.hidden))if(Array.isArray(columns))hidden[key]=columns.filter((v):v is string=>typeof v==='string'&&/^[a-zA-Z0-9_.-]+$/.test(v))
    const refreshMinutes=[5,15,30,60].includes(raw.refreshMinutes)?raw.refreshMinutes:defaults.refreshMinutes
    return {
      density:raw.density==='comfortable'?'comfortable':'compact',
      textSize:['small','normal','large'].includes(raw.textSize)?raw.textSize:'normal',
      reduceMotion:raw.reduceMotion===true,
      hidden,
      autoRefresh:raw.autoRefresh===true,
      refreshMinutes,
      refreshVisibleOnly:raw.refreshVisibleOnly!==false,
      tableWrap:raw.tableWrap===true,
      stickyHeaders:raw.stickyHeaders!==false,
      exportDelimiter:raw.exportDelimiter==='comma'||raw.exportDelimiter==='tab'?raw.exportDelimiter:'semicolon',
      exportBom:raw.exportBom!==false,
      exportFilteredOnly:raw.exportFilteredOnly!==false,
      dateFormat:raw.dateFormat==='iso'?'iso':'ru',
      timeZone:raw.timeZone==='Europe/Moscow'||raw.timeZone==='UTC'?raw.timeZone:'local',
      currencyDisplay:raw.currencyDisplay==='code'?'code':'symbol',
      highContrast:raw.highContrast===true,
      largeControls:raw.largeControls===true,
    }
  }catch{return {...defaults,hidden:{}}}
}
type PreferenceContext={value:Preferences;update:(patch:Partial<Preferences>)=>void;tables:Record<string,TableDefinition>;register:(id:string,definition:TableDefinition)=>void;toggle:(id:string,key:string,visible:boolean)=>void}
const Context=createContext<PreferenceContext|null>(null)
export function UIPreferencesProvider({children}:{children:ReactNode}){
  const queryClient=useQueryClient()
  const [value,setValue]=useState<Preferences>(()=>{try{return readPreferences(localStorage.getItem(storageKey))}catch{return defaults}})
  const [tables,setTables]=useState(initialTables)
  useEffect(()=>{
    applyDocumentPreferences(value)
    try{localStorage.setItem(storageKey,JSON.stringify(value))}catch{/* Preferences remain effective in this session. */}
  },[value])
  useEffect(()=>{
    if(!value.autoRefresh)return
    const refresh=()=>{
      if(value.refreshVisibleOnly&&document.visibilityState!=='visible')return
      void queryClient.invalidateQueries({type:'active'})
    }
    const timer=window.setInterval(refresh,value.refreshMinutes*60_000)
    const onVisibility=()=>{if(document.visibilityState==='visible')refresh()}
    document.addEventListener('visibilitychange',onVisibility)
    return()=>{window.clearInterval(timer);document.removeEventListener('visibilitychange',onVisibility)}
  },[queryClient,value.autoRefresh,value.refreshMinutes,value.refreshVisibleOnly])
  const context=useMemo<PreferenceContext>(()=>({value,tables,update:patch=>setValue(current=>{const next={...current,...patch};applyDocumentPreferences(next);return next}),register:(id,definition)=>setTables(current=>JSON.stringify(current[id])===JSON.stringify(definition)?current:{...current,[id]:definition}),toggle:(id,key,visible)=>setValue(current=>{const next={...current,hidden:{...current.hidden,[id]:visible?(current.hidden[id]??[]).filter(v=>v!==key):[...new Set([...(current.hidden[id]??[]),key])]}};applyDocumentPreferences(next);return next})}),[value,tables])
  return <Context.Provider value={context}>{children}</Context.Provider>
}
export function usePreferences(){const context=useContext(Context);if(!context)throw new Error('UI preferences provider missing');return context}
export function useTableColumns(id:string,label:string,columns:PreferenceColumn[]){
  const context=usePreferences()
  const signature=JSON.stringify(columns)
  useEffect(()=>{if(columns.length)context.register(id,{label,columns:JSON.parse(signature) as PreferenceColumn[]})},[id,label,signature])
  const hidden=context.value.hidden[id]??[]
  // A changed schema or stale saved preference must never hide the entire table.
  const effective=columns.every(c=>hidden.includes(c.key))?hidden.filter(key=>key!==columns[0]?.key):hidden
  return {hidden:effective,visibility:Object.fromEntries(effective.map(key=>[key,false]))}
}
export function InterfaceSettings(){
  const {value,update,tables,toggle}=usePreferences()
  const queryClient=useQueryClient()
  const [selected,setSelected]=useState('stocks')
  const [tableSearch,setTableSearch]=useState('')
  const [localStatus,setLocalStatus]=useState('')
  const tablePicker=useRef<HTMLDetailsElement>(null)
  const definition=tables[selected]??initialTables.stocks!
  const hidden=value.hidden[selected]??[]
  const visibleColumns=definition.columns.filter(column=>!hidden.includes(column.key)).length
  const normalizedSearch=tableSearch.trim().toLocaleLowerCase('ru')
  const filteredTables=Object.entries(tables).filter(([,table])=>!normalizedSearch||table.label.toLocaleLowerCase('ru').includes(normalizedSearch))
  const selectTable=(id:string)=>{setSelected(id);setTableSearch('');tablePicker.current?.removeAttribute('open')}
  const clearCache=async()=>{
    setLocalStatus('Очищаем временные данные…')
    queryClient.removeQueries({type:'inactive'})
    await queryClient.invalidateQueries({type:'active'})
    setLocalStatus('Временные данные очищены, открытые разделы обновлены.')
  }
  const resetInterface=()=>{
    try{localStorage.removeItem(storageKey)}catch{/* The in-memory reset still applies. */}
    update({...defaults,hidden:{}})
    setSelected('stocks')
    setTableSearch('')
    setLocalStatus('Настройки интерфейса сброшены.')
  }
  return <section className="interface-settings" aria-label="Настройки интерфейса">
    <div className="settings-section">
      <Heading as="h2" size="4">Обновление данных</Heading>
      <Text as="p" size="2" color="gray">Автообновление повторно запрашивает только данные открытых разделов.</Text>
      <div className="settings-fields">
        <label className="settings-check"><Checkbox checked={value.autoRefresh} onCheckedChange={next=>update({autoRefresh:Boolean(next)})}/>Обновлять данные автоматически</label>
        <label>Интервал<Select.Root disabled={!value.autoRefresh} value={String(value.refreshMinutes)} onValueChange={next=>update({refreshMinutes:Number(next) as Preferences['refreshMinutes']})}><Select.Trigger aria-label="Интервал автообновления"/><Select.Content><Select.Item value="5">5 минут</Select.Item><Select.Item value="15">15 минут</Select.Item><Select.Item value="30">30 минут</Select.Item><Select.Item value="60">60 минут</Select.Item></Select.Content></Select.Root></label>
      </div>
      <label className="settings-check"><Checkbox disabled={!value.autoRefresh} checked={value.refreshVisibleOnly} onCheckedChange={next=>update({refreshVisibleOnly:Boolean(next)})}/>Обновлять только когда окно открыто</label>
    </div>

    <div className="settings-section">
      <Heading as="h2" size="4">Таблицы</Heading>
      <div className="settings-fields">
        <label>Плотность таблиц<Select.Root value={value.density} onValueChange={density=>update({density:density as Preferences['density']})}><Select.Trigger aria-label="Плотность таблиц"/><Select.Content><Select.Item value="compact">Компактная</Select.Item><Select.Item value="comfortable">Свободная</Select.Item></Select.Content></Select.Root></label>
        <label>Размер текста<Select.Root value={value.textSize} onValueChange={textSize=>update({textSize:textSize as Preferences['textSize']})}><Select.Trigger aria-label="Размер текста в таблицах"/><Select.Content><Select.Item value="small">Мелкий</Select.Item><Select.Item value="normal">Обычный</Select.Item><Select.Item value="large">Крупный</Select.Item></Select.Content></Select.Root></label>
      </div>
      <div className="settings-inline-options">
        <label className="settings-check"><Checkbox checked={value.tableWrap} onCheckedChange={next=>update({tableWrap:Boolean(next)})}/>Переносить длинный текст</label>
        <label className="settings-check"><Checkbox checked={value.stickyHeaders} onCheckedChange={next=>update({stickyHeaders:Boolean(next)})}/>Закреплять заголовки</label>
      </div>
      <div className="settings-columns">
        <Text as="span" size="2" color="gray">Столбцы таблицы</Text>
        <details className="settings-table-picker" ref={tablePicker}>
          <summary>{definition.label}</summary>
          <div className="settings-table-picker-panel">
            <input className="settings-search" type="search" value={tableSearch} onChange={event=>setTableSearch(event.target.value)} placeholder="Найти таблицу" aria-label="Найти таблицу"/>
            <div className="settings-table-options">
              {filteredTables.map(([id,table])=><button key={id} type="button" aria-pressed={id===selected} onClick={()=>selectTable(id)}>{table.label}</button>)}
              {!filteredTables.length&&<Text as="p" size="2" color="gray">Таблицы не найдены</Text>}
            </div>
          </div>
        </details>
        <div className="settings-column-list">{definition.columns.map(column=><label key={column.key}><Checkbox checked={!hidden.includes(column.key)} disabled={!hidden.includes(column.key)&&visibleColumns===1} onCheckedChange={checked=>toggle(selected,column.key,Boolean(checked))}/>{column.label}</label>)}</div>
        <Text as="p" size="2" color="gray">Настройки применяются сразу. После загрузки список столбцов уточняется по ответу Ozon.</Text>
      </div>
    </div>

    <div className="settings-section">
      <Heading as="h2" size="4">Экспорт</Heading>
      <div className="settings-fields">
        <label>Разделитель<Select.Root value={value.exportDelimiter} onValueChange={exportDelimiter=>update({exportDelimiter:exportDelimiter as Preferences['exportDelimiter']})}><Select.Trigger aria-label="Разделитель экспорта"/><Select.Content><Select.Item value="semicolon">Точка с запятой</Select.Item><Select.Item value="comma">Запятая</Select.Item><Select.Item value="tab">Табуляция (TSV)</Select.Item></Select.Content></Select.Root></label>
        <label>Какие строки<Select.Root value={value.exportFilteredOnly?'filtered':'all'} onValueChange={next=>update({exportFilteredOnly:next==='filtered'})}><Select.Trigger aria-label="Строки для экспорта"/><Select.Content><Select.Item value="filtered">После фильтров</Select.Item><Select.Item value="all">Все загруженные</Select.Item></Select.Content></Select.Root></label>
      </div>
      <label className="settings-check"><Checkbox checked={value.exportBom} onCheckedChange={next=>update({exportBom:Boolean(next)})}/>Совместимость CSV с Excel (UTF-8 BOM)</label>
      <Text as="p" size="2" color="gray">Параметры сохраняются глобально и применяются экспортёрами таблиц.</Text>
    </div>

    <div className="settings-section">
      <Heading as="h2" size="4">Дата, время и деньги</Heading>
      <div className="settings-fields settings-fields-three">
        <label>Дата<Select.Root value={value.dateFormat} onValueChange={dateFormat=>update({dateFormat:dateFormat as Preferences['dateFormat']})}><Select.Trigger aria-label="Формат даты"/><Select.Content><Select.Item value="ru">09.10.2026</Select.Item><Select.Item value="iso">2026-10-09</Select.Item></Select.Content></Select.Root></label>
        <label>Часовой пояс<Select.Root value={value.timeZone} onValueChange={timeZone=>update({timeZone:timeZone as Preferences['timeZone']})}><Select.Trigger aria-label="Часовой пояс"/><Select.Content><Select.Item value="local">Системный</Select.Item><Select.Item value="Europe/Moscow">Москва</Select.Item><Select.Item value="UTC">UTC</Select.Item></Select.Content></Select.Root></label>
        <label>Валюта<Select.Root value={value.currencyDisplay} onValueChange={currencyDisplay=>update({currencyDisplay:currencyDisplay as Preferences['currencyDisplay']})}><Select.Trigger aria-label="Отображение валюты"/><Select.Content><Select.Item value="symbol">₽</Select.Item><Select.Item value="code">RUB</Select.Item></Select.Content></Select.Root></label>
      </div>
      <Text as="p" size="2" color="gray">Настройка меняет только отображение. Исходные суммы и валюты не пересчитываются.</Text>
    </div>

    <div className="settings-section">
      <Heading as="h2" size="4">Доступность</Heading>
      <div className="settings-inline-options">
        <label className="settings-check"><Checkbox checked={value.reduceMotion} onCheckedChange={next=>update({reduceMotion:Boolean(next)})}/>Уменьшить анимации</label>
        <label className="settings-check"><Checkbox checked={value.highContrast} onCheckedChange={next=>update({highContrast:Boolean(next)})}/>Повысить контраст границ</label>
        <label className="settings-check"><Checkbox checked={value.largeControls} onCheckedChange={next=>update({largeControls:Boolean(next)})}/>Увеличить элементы управления</label>
      </div>
    </div>

    <div className="settings-section settings-local-data">
      <Heading as="h2" size="4">Локальные данные</Heading>
      <Text as="p" size="2" color="gray">Здесь очищаются только настройки интерфейса и временный кеш запросов. Магазины, Client-Id и API-ключи не затрагиваются.</Text>
      <div className="settings-actions">
        <Button variant="outline" onClick={resetInterface}>Сбросить настройки интерфейса</Button>
        <Button variant="soft" onClick={()=>void clearCache()}>Очистить временные данные</Button>
      </div>
      <Text as="p" size="2" color="gray" aria-live="polite">{localStatus}</Text>
    </div>
  </section>
}
