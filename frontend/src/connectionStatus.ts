import type {AccountList,ConnectionCheck} from './api'

export type CheckResult={status:'ok'|'unknown'}
type Verification={
  accountId:string|null|undefined
  revision:number|undefined
  mode:string|undefined
  signal:AbortSignal
  getAccounts:(signal?:AbortSignal)=>Promise<AccountList>
  checkConnection:(signal?:AbortSignal)=>Promise<ConnectionCheck>
  currentAccounts?:()=>AccountList|undefined
}

export function canCheckConnection(accountId:string|null|undefined,revision:number|undefined,mode:string|undefined) {
  return Boolean(accountId)&&revision!==undefined&&mode==='LIVE_READ'
}

// The result is valid only for the same store revision before and after the check.
export async function verifyConnection({accountId,revision,mode,signal,getAccounts,checkConnection,currentAccounts}:Verification):Promise<CheckResult> {
  if(!canCheckConnection(accountId,revision,mode))return {status:'unknown'}
  signal.throwIfAborted()
  const matches=(accounts:AccountList)=>accounts.active_id===accountId&&accounts.revision===revision&&accounts.mode===mode
  const before=await getAccounts(signal)
  if(!matches(before))return {status:'unknown'}
  let failed=false
  let failure:unknown
  try {await checkConnection(signal)} catch(error){failed=true;failure=error}
  signal.throwIfAborted()
  const after=await getAccounts(signal)
  if(!matches(after))return {status:'unknown'}
  const cached=currentAccounts?.()
  if(cached&&!matches(cached))return {status:'unknown'}
  if(failed)throw failure
  return {status:'ok'}
}
