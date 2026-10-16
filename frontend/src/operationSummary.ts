import type {MutationResult} from './api'
export function operationResultSummary(result:MutationResult):string {
  if(result.status==='VERIFIED_SUCCESS')return result.operation==='auto_add'?'Возврат в автодобавление подтверждён':'Успешно'
  const success=result.rows?.filter(row=>row.status==='VERIFIED_SUCCESS').length??0
  const errors=(result.rows?.length??0)-success
  if(result.status==='UNKNOWN_RESULT')return `Успешно: ${success}, результат не определён: ${errors}`
  return `Успешно: ${success}, Ошибки: ${errors}`
}
