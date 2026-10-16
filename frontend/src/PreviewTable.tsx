import {Text} from '@radix-ui/themes'
import {formatPrice,type Preview,type Product} from './api'
import {ScrollTable,TableFilterChips,useTableFilters,type TableFilterColumn} from './TableTools'

export function PreviewTable({plan}:{plan:Preview}){
  const items=new Map(plan.source.items.map(i=>[i.id,i]))
  const hasMembers=plan.source.items.some(item=>'role'in item&&item.role==='member')
  const isAutoAdd=plan.source.items.some(item=>'auto_add_at'in item&&item.auto_add_at!==null)
  const discountColumns:TableFilterColumn[]=isAutoAdd?[{key:'auto_add_discount',label:'Скидка автодобавления, %',kind:'decimal'}]:[{key:'minimum_discount',label:'Мин. скидка',kind:'decimal'},{key:'maximum_discount',label:'Макс. скидка',kind:'decimal'},...(hasMembers?[{key:'actual_discount',label:'Фактическая скидка',kind:'decimal' as const}]:[])]
  const columns:TableFilterColumn[]=[{key:'name',label:'Название'},{key:'sku',label:'SKU'},{key:'offer_id',label:'Артикул'},...(plan.kind==='stocks'?[{key:'warehouse',label:'Склад'}]:[]),...(plan.kind==='prices'?discountColumns:[]),{key:'before',label:'Было',kind:'decimal'},{key:'after',label:'Расчёт',kind:'decimal'},{key:'diff',label:'Разница',kind:'decimal'},{key:'error',label:'Проверка'}]
  const filters=useTableFilters<typeof plan.rows[number]>(columns)
  const value=(row:typeof plan.rows[number],key:string)=>{
    const item=items.get(row.id)
    switch(key){
      case 'sku':return item?.sku
      case 'name':return row.name
      case 'offer_id':return row.offer_id
      case 'warehouse':return item&&'warehouse_name'in item?item.warehouse_name:undefined
      case 'minimum_discount':return item&&'minimum_discount'in item?item.minimum_discount:undefined
      case 'maximum_discount':return item&&'maximum_discount'in item?item.maximum_discount:undefined
      case 'actual_discount':return item&&'role'in item&&item.role==='member'?item.actual_discount:undefined
      case 'auto_add_discount':return item&&'auto_add_discount'in item?item.auto_add_discount:undefined
      case 'before':return row.before
      case 'after':return row.after
      case 'diff':return row.diff
      case 'error':return row.error
      default:return undefined
    }
  }
  const rows=filters.apply(plan.rows,value)
  return <section className="preview-results"><TableFilterChips filters={filters.activeFilters} onClear={filters.clear} onClearAll={filters.clearAll}/><ScrollTable {...filters.surfaceProps} overlay={filters.menu} className="compact-table" tabIndex={0} aria-label="Таблица предварительного просмотра"><table><thead><tr>{columns.map(c=><th {...filters.headerProps(c.key)} key={c.key}>{c.label}</th>)}</tr></thead><tbody>{rows.map(row=>{
    const item=items.get(row.id)
    const format=(value:string|null)=>plan.kind==='prices'?formatPrice(value,row.currency??(isAutoAdd&&item&&'currency_assumed'in item&&item.currency_assumed?'RUB':null)):value===null?'Нет данных':value+' шт.'
    const discount=(key:'minimum_discount'|'maximum_discount'|'actual_discount'|'auto_add_discount')=>{const raw=item&&'role'in item?(item as Product)[key]:null;return raw==null?'Нет данных':`${raw.replace('.',',')}%`}
    const actual=item&&'role'in item&&item.role==='member'
    return <tr key={row.id}><td {...filters.cellProps('name',row.name)}>{row.name}</td><td {...filters.cellProps('sku',item?.sku)}>{item?.sku??'Нет данных'}</td><td {...filters.cellProps('offer_id',row.offer_id)}>{row.offer_id}</td>{plan.kind==='stocks'&&<td {...filters.cellProps('warehouse',item&&'warehouse_name'in item?item.warehouse_name:undefined)}>{item&&'warehouse_name'in item?item.warehouse_name:'Нет данных'}</td>}{plan.kind==='prices'&&<>{isAutoAdd?<td {...filters.cellProps('auto_add_discount',item&&'auto_add_discount'in item?item.auto_add_discount:undefined)} className="numeric">{discount('auto_add_discount')}</td>:<><td {...filters.cellProps('minimum_discount',item&&'minimum_discount'in item?item.minimum_discount:undefined)} className="numeric">{discount('minimum_discount')}</td><td {...filters.cellProps('maximum_discount',item&&'maximum_discount'in item?item.maximum_discount:undefined)} className="numeric">{discount('maximum_discount')}</td>{hasMembers&&<td {...filters.cellProps('actual_discount',actual&&item&&'role'in item?item.actual_discount:undefined)} className="numeric">{actual?discount('actual_discount'):'Нет данных'}</td>}</>}</>}<td {...filters.cellProps('before',row.before)} className="numeric">{format(row.before)}</td><td {...filters.cellProps('after',row.after)} className="numeric">{format(row.after)}</td><td {...filters.cellProps('diff',row.diff)} className="numeric">{format(row.diff)}</td><td {...filters.cellProps('error',row.error)}>{row.error?<Text color="red">{row.error}</Text>:'Без ошибок'}</td></tr>
  })}</tbody></table></ScrollTable></section>
}
