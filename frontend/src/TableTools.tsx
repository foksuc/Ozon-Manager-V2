import {Children,cloneElement,isValidElement,useEffect,useRef,useState,type HTMLAttributes,type KeyboardEvent,type MouseEvent,type ReactNode,type ReactElement} from 'react'
import {useTableColumns,type PreferenceColumn} from './uiPreferences'
import {isValidDecimalFilter,matchesTableFilter,normalizeFilterValue,type TableFilterCondition,type TableFilterValue} from './tableFilterValues'

export type TableFilterColumn = {key:string;label:string;kind?:'text'|'decimal'}
export type ActiveTableFilter = {key:string;label:string;condition:'equals'|'greater'|'less';value:string}
type Condition = TableFilterCondition
type Filter = TableFilterValue

export function useTableFilters<T>(columns:TableFilterColumn[]){
  const [filters,setFilters]=useState<Record<string,Filter>>({})
  const [menu,setMenu]=useState<{key:string;x:number;y:number;value:string}|null>(null)
  const [draft,setDraft]=useState<Filter>({condition:'equals',value:''})
  const menuElement=useRef<HTMLDivElement>(null)
  const returnFocus=useRef<HTMLElement|null>(null)
  const closeMenu=(restoreFocus=false)=>{setMenu(null);if(restoreFocus)returnFocus.current?.focus({preventScroll:true})}
  const columnSignature=columns.map(c=>`${c.key}:${c.kind??'text'}`).join('\u0000')
  useEffect(()=>{setFilters({});setMenu(null)},[columnSignature])
  useEffect(()=>{
    if(!menu)return
    const dismiss=(event:Event)=>{if(event.target instanceof Node&&menuElement.current?.contains(event.target))return;setMenu(null)}
    window.addEventListener('pointerdown',dismiss,true)
    window.addEventListener('scroll',dismiss,{capture:true,passive:true})
    window.addEventListener('resize',dismiss)
    return ()=>{window.removeEventListener('pointerdown',dismiss,true);window.removeEventListener('scroll',dismiss,true);window.removeEventListener('resize',dismiss)}
  },[menu])
  const open=(event:MouseEvent<HTMLElement>,key:string,value:string)=>{
    event.preventDefault()
    openAt(key,value,event.clientX,event.clientY,true)
  }
  const openAt=(key:string,value:string,x:number,y:number,fromCell=false)=>{returnFocus.current=document.activeElement instanceof HTMLElement?document.activeElement:null;setMenu({key,x:Math.min(x,window.innerWidth-278),y:Math.min(y,window.innerHeight-320),value});setDraft(fromCell?{condition:filters[key]?.condition??'equals',value}:filters[key]??{condition:'equals',value})}
  const column=(key:string)=>columns.find(c=>c.key===key)
  const valueForMenu=()=>draft.value
  const decimalInput=menu&&column(menu.key)?.kind==='decimal'
  const normalized=normalizeFilterValue(valueForMenu(),decimalInput?'decimal':'text')
  const invalid=menu&&(valueForMenu().trim()===''||(decimalInput&&!isValidDecimalFilter(valueForMenu())))
  const applyMenu=()=>{if(!menu||invalid)return;setFilters(current=>({...current,[menu.key]:{...draft,value:normalized}}));closeMenu(true)}
  const menuKey=(event:KeyboardEvent<HTMLElement>)=>{if(event.key==='Escape'&&menu){event.preventDefault();event.stopPropagation();closeMenu(true);return}if((event.key==='F10'&&event.shiftKey)||event.key==='ContextMenu'){const target=(event.target as HTMLElement).closest<HTMLElement>('[data-table-filter-key]');if(!target)return;event.preventDefault();const rect=target.getBoundingClientRect();openAt(target.dataset.tableFilterKey!,target.dataset.tableFilterValue??'',rect.left,rect.bottom)}}
  return {
    apply:(rows:T[],getValue:(row:T,key:string)=>string|null|undefined)=>rows.filter(row=>Object.entries(filters).every(([key,filter])=>{const c=column(key);return !c||matchesTableFilter(getValue(row,key),filter,c.kind??'text')})),
    filters,
    activeFilters:Object.entries(filters).map(([key,filter])=>({key,label:column(key)?.label??key,...filter})),
    clear:(key:string)=>setFilters(current=>{const next={...current};delete next[key];return next}),
    clearAll:()=>setFilters({}),
    onDialogEscapeKeyDown:(event:{preventDefault:()=>void})=>{if(menu)event.preventDefault()},
    surfaceProps:{onContextMenu:(event:MouseEvent<HTMLElement>)=>{const target=(event.target as HTMLElement).closest<HTMLElement>('[data-table-filter-key]');if(target)open(event,target.dataset.tableFilterKey!,target.dataset.tableFilterValue??'')},onKeyDown:menuKey},
    headerProps:(key:string,hasFocusableChild=false)=>({'data-table-filter-key':key,'data-table-filter-value':'',tabIndex:hasFocusableChild?-1:0,title:'ПКМ или Shift+F10 — фильтр'}),
    cellProps:(key:string,value:string|null|undefined)=>({'data-table-filter-key':key,'data-table-filter-value':value??'',title:'ПКМ — фильтр'}),
    menu:menu&&<div ref={menuElement} className="table-filter-menu" role="dialog" aria-modal="false" aria-label={`Фильтр: ${column(menu.key)?.label??menu.key}`} style={{left:Math.max(8,menu.x),top:Math.max(8,menu.y)}} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))closeMenu()}} onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closeMenu(true)}}}>
      <strong>{column(menu.key)?.label??menu.key}</strong>
      <select aria-label="Условие фильтра" value={draft.condition} onChange={e=>setDraft(current=>({...current,condition:e.target.value as Condition}))}>
        <option value="equals">Равно</option><option value="greater">Больше</option><option value="less">Меньше</option>
      </select>
      <input autoComplete="off" autoFocus aria-label="Значение фильтра" value={draft.value} onChange={e=>setDraft(current=>({...current,value:e.target.value}))} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();applyMenu()}}} />
      {invalid&&<span role="alert" className="table-filter-error">{valueForMenu().trim()===''?'Введите значение фильтра.':"Введите число, например 12,50."}</span>}
      <div><button type="button" disabled={!!invalid} onClick={applyMenu}>Применить</button><button type="button" onClick={()=>{if(menu){setFilters(current=>{const next={...current};delete next[menu.key];return next})};closeMenu(true)}}>Сбросить</button><button type="button" onClick={()=>closeMenu(true)}>Отмена</button></div>
    </div>,
  }
}

export function TableFilterChips({filters,onClear,onClearAll}:{filters:ActiveTableFilter[];onClear:(key:string)=>void;onClearAll:()=>void}){
  if(!filters.length)return null
  const operator:Record<Condition,string>={equals:'=',greater:'>',less:'<'}
  return <div className="table-filter-chips" aria-label="Активные фильтры"><span>Фильтры:</span>{filters.map(filter=><button type="button" key={filter.key} aria-label={`Убрать фильтр ${filter.label}`} onClick={()=>onClear(filter.key)}>{filter.label} {operator[filter.condition]} {filter.value} <span aria-hidden="true">×</span></button>)}<button type="button" className="clear-all" onClick={onClearAll}>Сбросить все</button></div>
}

export function ScrollTable({children,overlay,className,tableId,scrollLabel='Прокрутка таблицы',...props}:{children:ReactNode;overlay?:ReactNode;className:string;tableId?:string;scrollLabel?:string}&HTMLAttributes<HTMLDivElement>){
  void scrollLabel
  const columns:PreferenceColumn[]=[]
  const text=(node:ReactNode):string=>Children.toArray(node).map(child=>typeof child==='string'||typeof child==='number'?String(child):isValidElement<{children?:ReactNode}>(child)?text(child.props.children):'').join('')
  const collect=(node:ReactNode)=>Children.forEach(node,child=>{
    if(!isValidElement<{children?:ReactNode;'data-table-filter-key'?:string}>(child))return
    const key=child.props['data-table-filter-key']
    if(child.type==='th'&&key&&!columns.some(c=>c.key===key))columns.push({key,label:text(child.props.children)})
    collect(child.props.children)
  })
  collect(children)
  const label=props['aria-label']??'Таблица'
  const id=tableId??`${className}:${label}`
  const preferences=useTableColumns(id,label,columns)
  const filter=(node:ReactNode):ReactNode=>Children.map(node,child=>{
    if(!isValidElement<{children?:ReactNode;'data-table-filter-key'?:string}>(child))return child
    if(child.props['data-table-filter-key']&&preferences.hidden.includes(child.props['data-table-filter-key']))return null
    return child.props.children===undefined?child:cloneElement(child as ReactElement<{children?:ReactNode}>,{children:filter(child.props.children)})
  })
  return <div className="table-frame"><div {...props} role={props.role??'region'} className={`table-scroll ${className}`}>{filter(children)}</div>{overlay}</div>
}
