import {Button,Callout,Dialog,Text} from '@radix-ui/themes'
import {formatPrice,formatTime,type Product,type PromotionReview} from './api'
import {ScrollTable,TableFilterChips,useTableFilters,type TableFilterColumn} from './TableTools'

const currency=(p:PromotionReview['items'][number],key:string)=>Object.hasOwn(p.money_currencies??{},key)?p.money_currencies?.[key]:p.currency??(p.currency_assumed?'RUB':null)

export function PromotionReviewPanel({value,onClose,onPrepare}:{value:PromotionReview|null;onClose:()=>void;onPrepare?:(items:Product[])=>void}){
  const filterColumns:TableFilterColumn[]=[{key:'name',label:'Название'},{key:'sku',label:'SKU'},{key:'offer_id',label:'Артикул'},{key:'price',label:'Цена товара',kind:'decimal'},{key:'threshold',label:'Цена участия',kind:'decimal'},{key:'minimum_discount',label:'Мин. скидка',kind:'decimal'},{key:'maximum_discount',label:'Макс. скидка',kind:'decimal'},...(value?.purpose==='prepare_remove'?[{key:'actual_discount',label:'Фактическая скидка',kind:'decimal' as const}]:[])]
  const isAutoAdd=value?.items.some(p=>p.auto_add_at!==null)??false
  const discountColumns:TableFilterColumn[]=isAutoAdd?[{key:'auto_add_discount',label:'Скидка автодобавления, %',kind:'decimal'}]:[{key:'minimum_discount',label:'Мин. скидка',kind:'decimal'},{key:'maximum_discount',label:'Макс. скидка',kind:'decimal'},...(value?.purpose==='prepare_remove'?[{key:'actual_discount',label:'Фактическая скидка',kind:'decimal' as const}]:[])]
  filterColumns.splice(5,filterColumns.length-5,...discountColumns)
  const filters=useTableFilters<PromotionReview['items'][number]>(filterColumns)
  const visibleItems=value?filters.apply(value.items,(p,key)=>{
    switch(key){
      case 'sku':return p.sku
      case 'name':return p.name
      case 'offer_id':return p.offer_id
      case 'price':return p.price
      case 'threshold':return p[value.purpose==='prepare_remove'?'action_price':p.auto_add_at?'max_discount_price':'max_action_price']
      case 'minimum_discount':return p.minimum_discount
      case 'maximum_discount':return p.maximum_discount
      case 'actual_discount':return p.actual_discount
      case 'auto_add_discount':return p.auto_add_discount
      default:return undefined
    }
  }):[]
  return <Dialog.Root open={value!==null} onOpenChange={open=>{if(!open)onClose()}}>{value&&<Dialog.Content className="preview-panel" onEscapeKeyDown={filters.onDialogEscapeKeyDown}>
    <div className="panel-header"><Dialog.Title>{value.purpose==='prepare_remove'?'Список для удаления':'Список для добавления'}</Dialog.Title><Button variant="ghost" onClick={onClose}>Закрыть</Button></div>
    <Dialog.Description>Проверьте подходящие товары перед переходом к подтверждению.</Dialog.Description>
    <div className="dialog-summary"><Text size="2"><strong>{value.items.length}</strong> из {value.total} товаров подходит</Text>{value.unknown>0&&<Text size="2">Не удалось сравнить: {value.unknown}</Text>}<Text size="2" color="gray">{formatTime(value.fetched_at)} · {value.mode}</Text></div>
    {!value.complete&&<Callout.Root color="amber"><Callout.Text>Данные неполные. Это не готовый план операции.</Callout.Text></Callout.Root>}
    {value.unknown>0&&<Callout.Root color="amber"><Callout.Text>Часть строк не проверена: API не предоставил цену или подтверждённую общую валюту.</Callout.Text></Callout.Root>}
    <TableFilterChips filters={filters.activeFilters} onClear={filters.clear} onClearAll={filters.clearAll}/><ScrollTable {...filters.surfaceProps} overlay={filters.menu} className="compact-table" aria-label="Подготовленный список товаров" tabIndex={0}><table><thead><tr>{filterColumns.map(c=><th {...filters.headerProps(c.key)} key={c.key}>{c.key==='threshold'?(value.purpose==='prepare_remove'?'Цена участия':'Предельная цена участия'):c.label}</th>)}</tr></thead><tbody>{visibleItems.map(p=>{const key=value.purpose==='prepare_remove'?'action_price':p.auto_add_at?'max_discount_price':'max_action_price';const discount=(v:string|null|undefined)=>v==null?'Нет данных':`${v.replace('.',',')}%`;return <tr key={p.id}><td {...filters.cellProps('name',p.name)}>{p.name}</td><td {...filters.cellProps('sku',p.sku)}>{p.sku??'Нет данных'}</td><td {...filters.cellProps('offer_id',p.offer_id)}>{p.offer_id}</td><td {...filters.cellProps('price',p.price)} className="numeric">{formatPrice(p.price,currency(p,'price'))}</td><td {...filters.cellProps('threshold',p[key])} className="numeric">{formatPrice(p[key],currency(p,key))}</td>{isAutoAdd?<td {...filters.cellProps('auto_add_discount',p.auto_add_discount)} className="numeric">{discount(p.auto_add_discount)}</td>:<><td {...filters.cellProps('minimum_discount',p.minimum_discount)} className="numeric">{discount(p.minimum_discount)}</td><td {...filters.cellProps('maximum_discount',p.maximum_discount)} className="numeric">{discount(p.maximum_discount)}</td>{value.purpose==='prepare_remove'&&<td {...filters.cellProps('actual_discount',p.actual_discount)} className="numeric">{discount(p.actual_discount)}</td>}</>}</tr>})}</tbody></table>{!visibleItems.length&&<Text as="p">Подтверждённых подходящих товаров нет.</Text>}</ScrollTable>
    <div className="dialog-actions">{onPrepare&&<Button disabled={!value.complete||visibleItems.length===0} onClick={()=>{onPrepare(visibleItems);onClose()}}>Перейти к подтверждению ({visibleItems.length})</Button>}<Dialog.Close><Button variant="outline">Вернуться к таблице</Button></Dialog.Close></div>
  </Dialog.Content>}</Dialog.Root>
}
