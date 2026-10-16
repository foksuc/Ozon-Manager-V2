import {useEffect,useRef,useState} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import {Badge,Button,Callout,Dialog,Heading,Text} from '@radix-ui/themes'
import {formatDateTime,formatPrice,getAccounts,getMutationHistory,prepareRollback,reconcileHistory,type MutationPlan,type MutationResult,type VendorError} from './api'
import {OperationPanel,operationLabels} from './MutationPanel'
import {Icon} from './Icon'
import {LoadingOverlay} from './LoadingOverlay'
import {ScrollTable,TableFilterChips,useTableFilters} from './TableTools'
import {operationStatusText as statusText} from './operationStatus'

const operationText=(row:MutationResult)=>row.operation_label??(row.operation?operationLabels[row.operation]:'После перезапуска')
const operationTime=(row:MutationResult)=>formatDateTime(row.attempted_at??row.created_at)
const detailsText=(row:MutationResult)=>[row.message??'',row.transport_error?.reason??'',...(row.rows?.map(item=>`${statusText(item.status)} ${item.message??''} ${item.errors?.map(error=>`${error.reason_ru??error.message} ${error.reason??''}`).join(' ')??''}`)??[])].join(' ')
export function VendorErrorDetails({error}:{error:VendorError}){
  return <>{error.severity==='warning'?'Предупреждение: ':''}{error.reason_ru??error.message}{error.code&&<span className="operation-log-code"> · Код: {error.code}</span>}{error.reason&&<details><summary>{error.original_unavailable?'Сохранённое пояснение прежней версии':'Оригинал ответа Ozon'}</summary><Text as="p" size="2">{error.reason}</Text></details>}</>
}

function OperationLog({row,storeName,onClose,onReconcile,busy,error}:{row:MutationResult|null;storeName:string;onClose:()=>void;onReconcile:(row:MutationResult)=>void;busy:boolean;error:string|null}){
  if(!row)return null
  const snapshots=new Map(row.plan_rows?.map(item=>[item.id,item])??[])
  const results=new Map(row.rows?.map(item=>[item.id,item])??[])
  const ids=[...new Set([...snapshots.keys(),...results.keys()])]
  const quantity=(value:string|number|null|undefined,currency?:string|null)=>value==null?'Нет данных':row.operation==='stocks'?`${value} шт.`:formatPrice(String(value),currency??null)
  return <Dialog.Root open onOpenChange={open=>{if(!open)onClose()}}>
    <Dialog.Content className="preview-panel operation-log-panel" maxWidth="1000px">
      <div className="panel-header"><Dialog.Title>Лог операции</Dialog.Title><Button variant="ghost" onClick={onClose}>Закрыть</Button></div>
      <Dialog.Description>{operationText(row)} · {operationTime(row)}</Dialog.Description>
      <Callout.Root color={row.status==='VERIFIED_SUCCESS'?'green':row.status==='REJECTED'||row.status==='DISCREPANCY'?'red':'amber'} mt="3"><Callout.Text>{statusText(row.status)}{row.message&&` · ${row.message}`}</Callout.Text></Callout.Root>
      {row.transport_error&&<Callout.Root color="red" mt="3"><Callout.Text><VendorErrorDetails error={row.transport_error}/></Callout.Text></Callout.Root>}
      {row.can_reconcile&&<Button variant="outline" mt="3" disabled={busy} onClick={()=>onReconcile(row)}>{busy?'Сверяем результат…':'Сверить результат'}</Button>}
      {error&&<Callout.Root color="red" mt="3"><Callout.Text>{error}</Callout.Text></Callout.Root>}
      <dl className="operation-log-metadata">
        <div><dt>Операция</dt><dd>{row.id}</dd></div>
        <div><dt>Дата и время</dt><dd>{formatDateTime(row.created_at)}</dd></div>
        <div><dt>Отправка</dt><dd>{formatDateTime(row.attempted_at)}</dd></div>
        <div><dt>Режим</dt><dd>{row.mode==='MOCK'?'Демонстрационные данные':row.mode==='LIVE_READ'?'Магазин':'Не сохранён'}</dd></div>
        <div><dt>Магазин</dt><dd>{storeName}</dd></div>
        <div><dt>Результат</dt><dd>{statusText(row.status)} <span className="operation-log-code">({row.status})</span></dd></div>
        {row.rollback_of&&<div><dt>Откат исходной операции</dt><dd>{row.rollback_of}</dd></div>}
      </dl>
      {row.rollback_blocked_reason&&<Callout.Root color="amber" mb="3"><Callout.Text>Откат недоступен: {row.rollback_blocked_reason}</Callout.Text></Callout.Root>}
      {row.recovered&&<Text as="p" size="2">{row.can_reconcile?'История и исходный план восстановлены в зашифрованном виде; отправка повторно не выполняется.':row.rows?.length||row.plan_rows?.length?'Подробности восстановлены из зашифрованной истории. Исполняемый план предыдущего сеанса не восстановлен; автоматический откат недоступен.':'После перезапуска осталась только отметка исхода; подробностей этой старой операции нет.'} Неизвестную отправку нельзя повторять автоматически.</Text>}
      {ids.length>0?<div className="operation-log-items">{ids.map((id,index)=>{
        const item=results.get(id)
        const snapshot=snapshots.get(id)
        const currency=item?.currency===undefined?snapshot?.currency:item.currency
        const actualCurrency=item?.actual_currency===undefined?currency:item.actual_currency
        const before=item?.before===undefined?snapshot?.before:item.before
        const after=item?.after===undefined?snapshot?.after:item.after
        return <section className="operation-log-item" key={id}>
          <Heading as="h3" size="3">{item?.name??snapshot?.name??`Товар ${index+1}`}</Heading>
          <dl className="operation-log-grid">
            <div><dt>SKU</dt><dd>{item?.sku??snapshot?.sku??'Нет данных'}</dd></div>
            <div><dt>Артикул</dt><dd>{item?.offer_id??snapshot?.offer_id??'Нет данных'}</dd></div>
            {(item?.warehouse_id??snapshot?.warehouse_id)&&<div><dt>Склад</dt><dd>{item?.warehouse_id??snapshot?.warehouse_id}</dd></div>}
            <div><dt>Было</dt><dd>{quantity(before,currency)}</dd></div>
            <div><dt>Планировалось</dt><dd>{after===null&&row.operation?.endsWith('remove')?'Удаление':quantity(after,currency)}</dd></div>
            <div><dt>После сверки</dt><dd>{item?.actual_member===false&&row.operation?.endsWith('remove')?(item.status==='VERIFIED_SUCCESS'?'Удаление участия подтверждено':'Участие не найдено в ответе'):quantity(item?.actual,actualCurrency)}</dd></div>
            {(row.operation?.startsWith('action_')||row.operation?.startsWith('auto_'))&&<div><dt>Участие после сверки</dt><dd>{item?.actual_member===true?'Участвует':item?.actual_member===false?'Не участвует':'Нет данных'}</dd></div>}
            <div><dt>Полнота сверки</dt><dd>{item?.readback_complete===true?'Полная':item?.readback_complete===false?'Данные получены не полностью':'Нет данных'}</dd></div>
            {row.operation!=='stocks'&&<div><dt>Валюта</dt><dd>{currency??'Не указана'}</dd></div>}
            {row.operation!=='stocks'&&item?.actual!==undefined&&<div><dt>Валюта после сверки</dt><dd>{actualCurrency??'Не указана'}</dd></div>}
            <div><dt>Ответ на отправку</dt><dd>{item?.accepted===true?'Принято':item?.accepted===false?'Отклонено':'Подтверждение не получено'}</dd></div>
            {item?.card_actual!=null&&<div><dt>Предельная цена карточки после сверки</dt><dd>{formatPrice(item.card_actual,item.card_currency??currency??null)}{item.card_changed===false?' · Не изменилась':item.card_changed===true?' · Изменилась':''}</dd></div>}
            <div><dt>Сверка</dt><dd>{item?statusText(item.status):'Данные сверки отсутствуют'}</dd></div>
          </dl>
          {item?.message&&<Text as="p" size="2">{item.message}</Text>}
          {item?.errors?.length?<ul className="operation-log-errors">{item.errors.map((error,errorIndex)=><li key={errorIndex} data-severity={error.severity}><VendorErrorDetails error={error}/></li>)}</ul>:null}
        </section>
      })}</div>:<Text as="p" size="2">Подробности по товарам для этой операции недоступны. Пустой лог не подтверждает успешное выполнение.</Text>}
    </Dialog.Content>
  </Dialog.Root>
}

export function OperationHistory(){
  const queryClient=useQueryClient()
  const query=useQuery({queryKey:['mutation-history'],queryFn:({signal})=>getMutationHistory(signal),retry:false,refetchOnWindowFocus:false})
  const accounts=useQuery({queryKey:['accounts'],queryFn:({signal})=>getAccounts(signal),retry:false,refetchOnWindowFocus:false})
  const [logId,setLogId]=useState<string|null>(null)
  const [rollbackSource,setRollbackSource]=useState<MutationResult|null>(null)
  const [rollbackPlan,setRollbackPlan]=useState<MutationPlan|null>(null)
  const [preparing,setPreparing]=useState(false)
  const [rollbackError,setRollbackError]=useState<string|null>(null)
  const [reconcilingId,setReconcilingId]=useState<string|null>(null)
  const [reconciliationError,setReconciliationError]=useState<string|null>(null)
  const reconciliationGeneration=useRef(0)
  const reconciliationBusy=useRef(false)
  useEffect(()=>{
    reconciliationGeneration.current+=1
    reconciliationBusy.current=false
    setReconcilingId(null);setReconciliationError(null);setLogId(null)
    return ()=>{reconciliationGeneration.current+=1}
  },[accounts.data?.active_id,accounts.data?.revision])
  const generation=useRef(0)
  useEffect(()=>()=>{generation.current+=1},[])
  const closeRollback=()=>{generation.current+=1;setRollbackSource(null);setRollbackPlan(null);setRollbackError(null);setPreparing(false)}
  const openRollback=async(row:MutationResult)=>{
    if(!row.can_rollback||preparing||reconciliationBusy.current)return
    const requestGeneration=++generation.current
    setRollbackSource(row);setRollbackPlan(null);setRollbackError(null);setPreparing(true)
    try{
      const plan=await prepareRollback(row.id)
      if(generation.current===requestGeneration)setRollbackPlan(plan)
    }catch(error){
      if(generation.current===requestGeneration)setRollbackError(error instanceof Error?error.message:'Не удалось подготовить откат. Обновите результаты и попробуйте снова.')
    }finally{
      if(generation.current===requestGeneration)setPreparing(false)
    }
  }
  const reconcileResult=async(row:MutationResult)=>{
    if(!row.can_reconcile||reconciliationBusy.current||preparing)return
    reconciliationBusy.current=true
    const requestGeneration=reconciliationGeneration.current
    setReconcilingId(row.id);setReconciliationError(null)
    try{
      const result=await reconcileHistory(row.id)
      if(reconciliationGeneration.current!==requestGeneration)return
      queryClient.setQueryData<{items:MutationResult[]}>(['mutation-history'],current=>({items:(current?.items??[]).map(item=>item.id===result.id?result:item)}))
      void queryClient.invalidateQueries({queryKey:['mutation-history']})
      void queryClient.invalidateQueries({queryKey:['catalog']})
    }catch(error){
      if(reconciliationGeneration.current===requestGeneration)setReconciliationError(error instanceof Error?error.message:'Не удалось сверить результат. Отправка в Ozon не повторялась.')
    }finally{
      if(reconciliationGeneration.current===requestGeneration){reconciliationBusy.current=false;setReconcilingId(null)}
    }
  }
  const filters=useTableFilters<MutationResult>([{key:'created_at',label:'Операция'},{key:'operation',label:'Действие'},{key:'status',label:'Результат'},{key:'details',label:'Построчная сверка'}])
  const rows=filters.apply(query.data?.items??[],(row,key)=>key==='details'?detailsText(row):key==='operation'?operationText(row):key==='status'?statusText(row.status):operationTime(row))
  const logRow=query.data?.items.find(row=>row.id===logId)??null
  const storeName=accounts.data?.items.find(item=>item.id===(rollbackPlan?.account_id??rollbackSource?.account_id))?.name??'Текущее подключение'
  return <section className="operation-history">
    <div className="section-heading"><div><Heading as="h2" size="4">Результаты отправок</Heading><Text as="p" size="2" color="gray">История хранится локально в зашифрованном виде. Сверка читает текущее состояние Ozon. Откат требует свежей проверки и вашего подтверждения.</Text></div><Button variant="outline" disabled={query.isFetching||reconcilingId!==null} onClick={()=>void query.refetch()}>Обновить результаты</Button></div>
    {query.error&&<Callout.Root color="red"><Callout.Text>{query.error.message}</Callout.Text></Callout.Root>}
    {reconciliationError&&!logId&&<Callout.Root color="red"><Callout.Text>{reconciliationError}</Callout.Text></Callout.Root>}
    <TableFilterChips filters={filters.activeFilters} onClear={filters.clear} onClearAll={filters.clearAll}/>
    <ScrollTable {...filters.surfaceProps} overlay={filters.menu} className="operation-table operation-history-table" aria-label="История отправок" role="region" tabIndex={0}>
      <table><thead><tr><th aria-label="Откат" title="Откат операции"><Icon name="undo"/></th><th {...filters.headerProps('created_at')}>Операция</th><th {...filters.headerProps('operation')}>Действие</th><th {...filters.headerProps('status')}>Результат</th><th {...filters.headerProps('details')}>Построчная сверка</th></tr></thead><tbody>
        {rows.map(row=><tr key={row.id}>
          <td><Button className="operation-rollback" color="green" variant="ghost" disabled={!row.can_rollback||preparing||reconcilingId!==null} aria-label={`Откатить: ${operationText(row)}, ${operationTime(row)}`} title={row.rollback_blocked_reason??'Подготовить план возврата исходных значений'} onClick={()=>void openRollback(row)}>Откатить</Button></td>
          <td {...filters.cellProps('created_at',operationTime(row))}>{operationTime(row)}</td>
          <td {...filters.cellProps('operation',operationText(row))}>{operationText(row)}</td>
          <td {...filters.cellProps('status',statusText(row.status))}><Badge color={row.status==='VERIFIED_SUCCESS'?'green':row.status==='REJECTED'||row.status==='DISCREPANCY'?'red':'amber'}>{statusText(row.status)}</Badge></td>
          <td {...filters.cellProps('details',detailsText(row))}><Button className="operation-log-link" variant="ghost" color="blue" onClick={()=>setLogId(row.id)}>Открыть лог</Button>{row.can_reconcile&&<Button variant="ghost" disabled={preparing||reconcilingId!==null} onClick={()=>void reconcileResult(row)}>{reconcilingId===row.id?'Сверяем…':'Сверить результат'}</Button>}</td>
        </tr>)}
        {rows.length===0&&<tr><td colSpan={5}>Отправок пока нет или они скрыты фильтром.</td></tr>}
      </tbody></table>
    </ScrollTable>
    <OperationLog row={logRow} storeName={accounts.data?.items.find(item=>item.id===logRow?.account_id)?.name??'Название недоступно'} onClose={()=>setLogId(null)} onReconcile={row=>void reconcileResult(row)} busy={preparing||reconcilingId!==null} error={reconciliationError}/>
    <OperationPanel rollback plan={rollbackPlan} operation={rollbackSource?.operation??null} preparing={preparing} error={rollbackError} storeName={storeName} onClose={closeRollback} onResult={()=>{void queryClient.invalidateQueries({queryKey:['mutation-history']});void queryClient.invalidateQueries({queryKey:['catalog']});void queryClient.invalidateQueries({queryKey:['drafts']})}}/>
    <LoadingOverlay active={query.isFetching||reconcilingId!==null} label={reconcilingId!==null?'Сверяем результат в Ozon без повторной отправки…':'Загружаем результаты операций…'}/>
  </section>
}
