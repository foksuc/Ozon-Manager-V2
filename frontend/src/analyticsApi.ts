import type {SellerRecord,SellerReadResult} from './sellerApi'

export type AnalyticsProduct={sku:string;name:string;offer_id:string}
export type AnalyticsCatalogPage={rows:AnalyticsProduct[];complete:boolean;next_cursor:string|null;warnings:string[]}
export type AnalyticsBatchResult=SellerReadResult&{processed_skus:number;remaining_skus:string[];continue_automatically:boolean}
async function read<T>(path:string,body:object,signal:AbortSignal):Promise<T>{
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal})
  if(!response.ok){let message='Не удалось загрузить аналитические данные.';try{const value=await response.json() as {message?:string;detail?:string};message=value.message??value.detail??message}catch{/* Keep bounded local fallback. */}throw new Error(message)}
  return response.json() as Promise<T>
}
export const analyticsCatalogPage=(last_id:string,signal:AbortSignal)=>read<AnalyticsCatalogPage>('/api/analytics/catalog',{last_id},signal)
export const analyticsStocksBatch=(skus:string[],filters:SellerRecord,signal:AbortSignal)=>read<AnalyticsBatchResult>('/api/analytics/stocks-batch',{skus,filters},signal)
export function waitForAnalyticsPage(milliseconds:number,signal:AbortSignal):Promise<void>{return new Promise((resolve,reject)=>{
  if(signal.aborted){reject(new DOMException('Остановлено','AbortError'));return}
  const timer=window.setTimeout(()=>{signal.removeEventListener('abort',abort);resolve()},milliseconds)
  function abort(){window.clearTimeout(timer);reject(new DOMException('Остановлено','AbortError'))}
  signal.addEventListener('abort',abort,{once:true})
})}
