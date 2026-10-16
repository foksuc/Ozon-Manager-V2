import {Callout,Text} from '@radix-ui/themes'
import {type VendorError} from './api'

export function VendorErrorNotice({error}:{error:VendorError|undefined|null}){
  if(!error)return null
  return <Callout.Root color="red" role="alert"><Callout.Text>{error.reason_ru??error.message}{error.reason&&<details><summary>Оригинал ответа Ozon</summary><Text as="p" size="2">{error.reason}</Text></details>}</Callout.Text></Callout.Root>
}

export function isVendorError(value:unknown):value is VendorError{
  return typeof value==='object'&&value!==null&&'message' in value&&typeof value.message==='string'&&'reason_ru' in value&&typeof value.reason_ru==='string'
}
