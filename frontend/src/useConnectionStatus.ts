import {useQuery,useQueryClient} from '@tanstack/react-query'
import {checkConnection,getAccounts,type AccountList} from './api'
import {canCheckConnection,verifyConnection} from './connectionStatus'

export type ConnectionStatus='unknown'|'checking'|'ok'|'error'

// A manual check belongs to one account revision, never to the next active store.
export function useConnectionStatus(accountId:string|null|undefined,revision:number|undefined,mode:string|undefined,automatic=false) {
  const queryClient=useQueryClient()
  const queryKey=['connection-check',accountId??null,revision??null,mode??null] as const
  const available=canCheckConnection(accountId,revision,mode)
  const queryFn=({signal}:{signal:AbortSignal})=>verifyConnection({accountId,revision,mode,signal,getAccounts,checkConnection,currentAccounts:()=>queryClient.getQueryData<AccountList>(['accounts'])})
  const query=useQuery({queryKey,queryFn,enabled:automatic&&available,staleTime:Infinity,retry:false,refetchOnWindowFocus:false})
  const status:ConnectionStatus=!available?'unknown':query.isFetching?'checking':query.isError?'error':query.data?.status??'unknown'
  const check=()=>{
    if(!available||query.isFetching)return
    void queryClient.fetchQuery({queryKey,queryFn,retry:false,staleTime:0}).catch(()=>{})
  }
  const error=status==='error'?(query.error instanceof Error?query.error.message:'Не удалось проверить подключение. Повторите проверку.'):null
  return {status,check,error}
}
