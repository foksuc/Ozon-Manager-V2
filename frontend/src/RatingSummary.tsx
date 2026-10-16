import {useQuery} from '@tanstack/react-query'
import {useState} from 'react'
import {Button} from '@radix-ui/themes'
import {sellerRead,sellerRecord,type SellerValue} from './sellerApi'
function value(item:Record<string,SellerValue>):string {
  const raw=item.current_value
  if(raw==null)return 'Нет данных'
  const text=String(raw).replace('.',',')
  return item.value_type==='PERCENT'?`${text} %`:text
}
export function RatingSummary({accountId,revision,mode}:{accountId:string;revision:number;mode:string}) {
  const [expanded,setExpanded]=useState(false)
  const query=useQuery({queryKey:['seller-rating',mode,accountId,revision],queryFn:({signal})=>sellerRead('RATING_SUMMARY',{},signal),retry:false,refetchOnWindowFocus:false,staleTime:60000})
  const data=query.data?.data
  const groups=Array.isArray(data?.groups)?data.groups:[]
  const productRating=groups.flatMap(raw=>{const group=sellerRecord(raw);return Array.isArray(group.items)?group.items.map(sellerRecord):[]}).find(item=>typeof item.name==='string'&&item.name.trim().toLocaleLowerCase('ru')==='оценка товаров')
  const rawLocal=data?.localization_index
  const local=Array.isArray(rawLocal)?rawLocal:rawLocal?[rawLocal]:[]
  return <section className="rating-summary" aria-label="Рейтинги активного магазина">
    <div className="rating-heading"><button type="button" className="rating-disclosure" aria-expanded={expanded} aria-controls={`ratings-${accountId}`} onClick={()=>setExpanded(current=>!current)}><strong>Рейтинг продавца (Оценка товаров) · {productRating?value(productRating):query.isPending?'Загрузка…':'Нет данных'}</strong><span>{expanded?'Свернуть':'Развернуть'} <span aria-hidden="true">{expanded?'−':'+'}</span></span></button></div>
    <div id={`ratings-${accountId}`} hidden={!expanded}>
    <div className="rating-refresh"><Button size="1" variant="ghost" disabled={query.isFetching} onClick={()=>void query.refetch()}>{query.isFetching?'Загрузка…':'Обновить'}</Button></div>
    {query.isError&&<p role="alert">Рейтинги недоступны: {query.error.message}</p>}
    {query.isPending&&<p role="status">Загружаем показатели…</p>}
    {data&&<>
      {(data.premium===true||data.premium_plus===true)&&<span className="rating-subscription">{data.premium_plus?'Premium Plus':'Premium'}</span>}
      {data.penalty_score_exceeded===true&&<p className="rating-warning">Превышен баланс штрафных баллов</p>}
      {groups.map((raw,index)=>{const group=sellerRecord(raw);const items=Array.isArray(group.items)?group.items:[];return <div className="rating-group" key={index}><span>{typeof group.group_name==='string'?group.group_name:'Показатели магазина'}</span><dl>{items.map((rawItem,i)=>{const item=sellerRecord(rawItem);return <div key={i}><dt>{typeof item.name==='string'?item.name:'Показатель'}</dt><dd data-rating-status={String(item.status??'UNKNOWN_STATUS')} title={item.status==='CRITICAL'?'Критическое значение':item.status==='WARNING'?'Требует внимания':item.status==='OK'?'Всё хорошо':'Статус не определён'}>{value(item)}{(item.status==='CRITICAL'||item.status==='WARNING')&&<span className="rating-status">{item.status==='CRITICAL'?'Критично':'Внимание'}</span>}</dd></div>})}</dl></div>})}
      {local.map((raw,index)=>{const item=sellerRecord(raw);return item.localization_percentage==null?null:<dl className="rating-localization" key={index}><div><dt>Индекс локализации</dt><dd>{String(item.localization_percentage).replace('.',',')} %</dd></div></dl>})}
      {!groups.length&&!local.length&&<p>Ozon ещё не рассчитал показатели.</p>}
    </>}
    </div></section>
}
