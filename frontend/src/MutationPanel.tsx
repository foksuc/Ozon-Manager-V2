import {useEffect,useRef,useState} from 'react'
import {Badge,Button,Callout,Dialog,Text} from '@radix-ui/themes'
import {useMutation} from '@tanstack/react-query'
import {executeMutation,reconcileMutation,formatPrice,type MutationPlan,type MutationResult,type ShopOperation} from './api'
import {LoadingOverlay} from './LoadingOverlay'
import {ScrollTable,TableFilterChips,useTableFilters} from './TableTools'
import {operationResultSummary} from './operationSummary'

export const operationLabels:Record<ShopOperation,string>={prices:'Изменить цену карточки',stocks:'Изменить доступные остатки',action_add:'Добавить или обновить цену участия',action_remove:'Удалить из акции',auto_add:'Вернуть в автодобавление со скидкой Ozon',auto_remove:'Удалить из автодобавления'}
export function OperationPanel({plan,onClose,onResult,operation=null,preparing=false,error=null,updating=false,storeName='Текущее подключение',rollback=false,needsRecalculation=false,onRecalculate}:{needsRecalculation?:boolean;onRecalculate?:()=>void;plan:MutationPlan|null;onClose:()=>void;onResult:(value:MutationResult)=>void;operation?:ShopOperation|null;preparing?:boolean;error?:string|null;updating?:boolean;storeName?:string;rollback?:boolean}) {
  const [result,setResult]=useState<MutationResult|null>(null)
  const closeRef=useRef(onClose)
  closeRef.current=onClose
  const attempted=useRef(false)
  const execution=useMutation({mutationFn:()=>executeMutation(plan!),retry:false,onSuccess:value=>{setResult(value);onResult(value)}})
  const reconciliation=useMutation({mutationFn:()=>reconcileMutation(plan!),retry:false,onSuccess:value=>{setResult(value);onResult(value)}})
  useEffect(()=>{attempted.current=false;setResult(null);execution.reset();reconciliation.reset()},[plan?.id,operation])
  const success=result?.id===plan?.id&&result?.status==='VERIFIED_SUCCESS'
  useEffect(()=>{
    if(!success)return
    const timer=window.setTimeout(()=>closeRef.current(),1000)
    return ()=>window.clearTimeout(timer)
  },[success,result?.id])
  const filters=useTableFilters<NonNullable<MutationPlan>['rows'][number]>([{key:'name',label:'Название',kind:'text'},{key:'sku',label:'SKU',kind:'text'},{key:'offer_id',label:'Артикул',kind:'text'},{key:'before',label:'Было',kind:'decimal'},{key:'after',label:'Станет',kind:'decimal'}])
  const busy=preparing||execution.isPending||reconciliation.isPending||success
  const currentOperation=plan?.operation??operation
  const updatesParticipation=updating&&(currentOperation==='action_add'||currentOperation==='auto_add')
  const title=rollback?'Откат операции':updatesParticipation?(currentOperation==='auto_add'?'Изменить цену автодобавления':'Изменить цену участия'):currentOperation?operationLabels[currentOperation]:''
  if(!plan&&!operation)return null
  if(!plan)return <Dialog.Root open onOpenChange={open=>{if(!open&&!busy)onClose()}}><Dialog.Content className="preview-panel" onEscapeKeyDown={e=>{filters.onDialogEscapeKeyDown(e);if(busy)e.preventDefault()}} onInteractOutside={e=>{if(busy)e.preventDefault()}}>
    <div className="panel-header"><Dialog.Title>{title}</Dialog.Title><Button variant="ghost" disabled={busy} onClick={onClose}>Закрыть</Button></div>
    <Dialog.Description>Проверяем актуальные данные перед подтверждением.</Dialog.Description>
    {preparing&&<div className="plan-loading" role="status" aria-live="polite"><span className="loading-overlay-indicator" aria-hidden="true"/><Text>Составляем список изменений…</Text></div>}
    {error&&<Callout.Root color="red"><Callout.Text>{error}</Callout.Text></Callout.Root>}
  </Dialog.Content></Dialog.Root>
  const rows=filters.apply(plan.rows,(row,key)=>{const v=row[key as keyof typeof row];return v==null?null:String(v)})
  const quantity=(v:string|number|null,currency:string|null)=>v===null?'Нет значения':plan.operation==='stocks'?`${v} шт.`:formatPrice(String(v),currency)
  return <Dialog.Root open onOpenChange={open=>{if(!open&&!busy)onClose()}}><Dialog.Content className="preview-panel" onEscapeKeyDown={e=>{filters.onDialogEscapeKeyDown(e);if(busy)e.preventDefault()}} onInteractOutside={e=>{if(busy)e.preventDefault()}}>
    <div className="panel-header"><Dialog.Title>{title}</Dialog.Title><Button variant="ghost" disabled={busy} onClick={onClose}>Закрыть</Button></div>
    <Dialog.Description>Проверьте значения «Было» и «Станет». Изменения отправятся после вашего подтверждения и повторной проверки данных.</Dialog.Description>
    <div className="dialog-summary"><Badge color={plan.mode==='MOCK'?'amber':'blue'}>{plan.mode==='MOCK'?'Демонстрационные данные':'Магазин'}</Badge><strong>{storeName}</strong><Text size="2"><strong>{plan.rows.length}</strong> товаров</Text></div>
    {plan.blocked_reason&&<Callout.Root color="amber"><Callout.Text>{plan.blocked_reason}. Отправка заблокирована.</Callout.Text></Callout.Root>}
    {needsRecalculation&&!attempted.current&&<div className="plan-recalculation"><Text size="2">Параметры изменены. Пересчитайте план перед подтверждением.</Text><Button variant="outline" disabled={busy} onClick={onRecalculate}>Пересчитать план</Button></div>}
    <TableFilterChips filters={filters.activeFilters} onClear={filters.clear} onClearAll={filters.clearAll}/>
    <div className="mobile-plan-diff" aria-label="Изменяемые значения">{rows.map(row=><div className="mobile-plan-row" key={row.id}><strong>{row.name}</strong><span className="mobile-plan-sku">SKU {row.sku??'Нет данных'}</span><dl><div><dt>Было</dt><dd>{quantity(row.before,row.currency)}</dd></div><div><dt>Станет</dt><dd>{row.after===null&&plan.operation.endsWith('remove')?'Удаление':quantity(row.after,row.currency)}</dd></div></dl></div>)}</div>
    <section className="operation-confirmation" aria-label="Подтверждение операции">
    <ScrollTable {...filters.surfaceProps} className="operation-table" role="region" aria-label="План изменений" tabIndex={0} overlay={filters.menu}><table><thead><tr>{['name','sku','offer_id','before','after'].map((key,i)=><th {...filters.headerProps(key)} className={key==='before'||key==='after'?'numeric':undefined} key={key}>{['Название','SKU','Артикул','Было','Станет'][i]}</th>)}</tr></thead><tbody>{rows.map(row=><tr key={row.id}><td {...filters.cellProps('name',row.name)}>{row.name}</td><td {...filters.cellProps('sku',row.sku)}>{row.sku??'Нет данных'}</td><td {...filters.cellProps('offer_id',row.offer_id)}>{row.offer_id}</td><td {...filters.cellProps('before',row.before==null?null:String(row.before))} className="numeric">{quantity(row.before,row.currency)}</td><td {...filters.cellProps('after',row.after==null?null:String(row.after))} className="numeric">{row.after===null&&plan.operation.endsWith('remove')?'Удаление':quantity(row.after,row.currency)}</td></tr>)}</tbody></table></ScrollTable>
    <div className="dialog-actions"><Button color={plan.operation.endsWith('remove')?'red':'blue'} disabled={!plan.valid||busy||attempted.current||needsRecalculation} onClick={()=>{if(attempted.current||busy||!plan.valid||needsRecalculation)return;attempted.current=true;execution.mutate()}}>{rollback?'Подтвердить откат':plan.operation.endsWith('remove')?'Подтвердить удаление':plan.operation.endsWith('add')&&!updatesParticipation?'Подтвердить добавление':'Подтвердить изменение'}</Button>{attempted.current&&<Button variant="outline" disabled={busy} onClick={()=>reconciliation.mutate()}>Сверить результат</Button>}<Button variant="outline" disabled={busy} onClick={onClose}>{result?'Закрыть результат':'Отмена'}</Button></div>
    </section>
    {(execution.error||reconciliation.error)&&<Callout.Root color="red" mt="3"><Callout.Text>{execution.error?.message??reconciliation.error?.message} Повторная отправка этого плана отключена.</Callout.Text></Callout.Root>}
    {result&&<Callout.Root color={result.status==='VERIFIED_SUCCESS'?'green':'amber'} mt="3"><Callout.Text>{operationResultSummary(result)}</Callout.Text></Callout.Root>}
    {result?.rows?.filter(row=>row.card_actual!=null).map(row=><Text as="p" size="2" key={`card-${row.id}`}>{row.name??'Товар'}: предельная цена карточки после сверки — {formatPrice(row.card_actual!,row.card_currency??row.currency??null)}{row.card_changed===false?' (не изменилась)':row.card_changed===true?' (изменилась)':''}.</Text>)}
    {result&&[...(result.transport_error?[result.transport_error]:[]),...(result.rows?.flatMap(row=>row.errors??[])??[])].map((error,index)=><Callout.Root color={error.severity==='warning'?'amber':'red'} mt="2" key={index}><Callout.Text>{error.reason_ru??error.message}{error.reason&&<details><summary>{error.original_unavailable?'Сохранённое пояснение прежней версии':'Оригинал ответа Ozon'}</summary><Text as="p" size="2">{error.reason}</Text></details>}</Callout.Text></Callout.Root>)}
    <LoadingOverlay active={busy} success={success} label={success?'Успешно':reconciliation.isPending?'Сверяем результат в Ozon…':'Проверяем, отправляем и сверяем результат…'}/>
  </Dialog.Content></Dialog.Root>
}
