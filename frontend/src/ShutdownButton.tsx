import {useState} from 'react'
import {Button} from '@radix-ui/themes'
import {Icon} from './Icon'

export function ShutdownButton(){
  const [state,setState]=useState<'idle'|'stopping'|'stopped'>('idle')
  const [error,setError]=useState('')
  const stop=async()=>{
    setState('stopping');setError('')
    try{
      const status=await fetch('/api/lifecycle',{cache:'no-store'})
      const lifecycle=await status.json() as {supported?:boolean;token?:string;detail?:string}
      if(!status.ok||!lifecycle.supported||!lifecycle.token)throw new Error(lifecycle.detail??'Для выключения запустите приложение через START.bat.')
      const response=await fetch('/api/lifecycle/stop',{method:'POST',headers:{'Content-Type':'application/json','X-Lifecycle-Token':lifecycle.token},body:'{}'})
      const result=await response.json() as {detail?:string}
      if(!response.ok)throw new Error(result.detail??'Не удалось выключить приложение.')
      setState('stopped')
    }catch(cause){setError(cause instanceof Error?cause.message:'Не удалось выключить приложение.');setState('idle')}
  }
  return <div className="shutdown-control"><Button className="shutdown-button" variant="soft" color="red" disabled={state!=='idle'} onClick={()=>void stop()}><Icon name="power"/>{state==='stopping'?'Выключаем…':'Выключить'}</Button>{error&&<p role="alert">{error}</p>}{state==='stopped'&&<div className="shutdown-screen" role="status"><Icon name="power"/><h1>Приложение выключено</h1><p>Для запуска откройте START.bat в папке приложения.</p></div>}</div>
}
