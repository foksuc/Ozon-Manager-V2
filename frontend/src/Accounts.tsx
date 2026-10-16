import {useState} from 'react'
import {Badge,Button,Callout,Dialog,Flex,Heading,Text,TextField} from '@radix-ui/themes'
import {useMutation,useQuery} from '@tanstack/react-query'
import {accountAction,getAccounts,type AccountItem} from './api'
import {RatingSummary} from './RatingSummary'
import {LoadingOverlay} from './LoadingOverlay'
import {useConnectionStatus,type ConnectionStatus} from './useConnectionStatus'

const connectionLabels:Record<ConnectionStatus,string>={unknown:'Не проверено',checking:'Проверяем подключение…',ok:'Подключено',error:'Ошибка подключения'}

function AccountCard({item,active,revision,mode,pending,onEdit,onSelect,onDelete}:{item:AccountItem;active:boolean;revision:number;mode:string;pending:boolean;onEdit:()=>void;onSelect:()=>void;onDelete:()=>void}) {
  const connection=useConnectionStatus(active?item.id:null,revision,mode)
  const label=mode==='MOCK'?'MOCK · без подключения':connectionLabels[connection.status]
  return <section className={`account-card ${active?'is-active':''}`}>
    <div className="account-card-heading"><span className="account-avatar" aria-hidden="true">{item.name.slice(0,1).toLocaleUpperCase('ru')}</span><div className="account-card-identity">
      <div className="account-title-line"><Button className="account-name" variant="ghost" disabled={pending} onClick={onEdit}>{item.name}</Button><span className={`connection-dot is-${connection.status}`} aria-hidden="true"/></div>
      {active&&<Badge color="blue">Активный магазин</Badge>}
      <Text className="account-connection-status" size="2" aria-live="polite">{label}</Text>
    </div></div>
    {active&&<RatingSummary key={`${mode}:${item.id}:${revision}`} accountId={item.id} revision={revision} mode={mode}/>}
    <div className="account-card-actions">{active?<Button variant="soft" disabled={pending||connection.status==='checking'||mode==='MOCK'} onClick={connection.check} title={mode==='MOCK'?'Проверка доступна для подключённого магазина Ozon':undefined}>{connection.status==='checking'?'Проверяем…':'Проверить подключение'}</Button>:<Button variant="soft" disabled={pending} onClick={onSelect}>Переключиться</Button>}<Button variant="outline" disabled={pending} onClick={onEdit}>Изменить</Button><Button color="red" variant="ghost" disabled={pending} onClick={onDelete}>Удалить</Button></div>
    {connection.error&&<Callout.Root color="red"><Callout.Text>{connection.error}</Callout.Text></Callout.Root>}
  </section>
}

export function Accounts({open,onClose,onChanged}:{open:boolean;onClose:()=>void;onChanged:()=>Promise<void>}) {
  const query=useQuery({queryKey:['accounts'],queryFn:({signal})=>getAccounts(signal),enabled:open,retry:false,refetchOnWindowFocus:false})
  const [editing,setEditing]=useState<AccountItem|null|undefined>(undefined)
  const [name,setName]=useState(''),[clientId,setClientId]=useState(''),[key,setKey]=useState('')
  const [deleteId,setDeleteId]=useState<string|null>(null)
  const mock=query.data?.mode==='MOCK'
  const mutation=useMutation({mutationFn:({action,id,data}:{action:string;id?:string;data?:Record<string,string>})=>accountAction(action,id,data),retry:false,onSuccess:async()=>{setKey('');setClientId('');setEditing(undefined);setDeleteId(null);await query.refetch();await onChanged()}})
  const edit=(item:AccountItem|null)=>{setEditing(item);setName(item?.name??'');setClientId(item?.client_id??'');setKey('');setDeleteId(null);mutation.reset()}
  const close=()=>{if(mutation.isPending)return;setKey('');setClientId('');setEditing(undefined);setDeleteId(null);mutation.reset();onClose()}
  return <Dialog.Root open={open} onOpenChange={value=>{if(!value)close()}}><Dialog.Content className="preview-panel accounts-panel" maxWidth="780px">
    <div className="panel-header"><Dialog.Title>Ваши магазины</Dialog.Title><Button variant="ghost" disabled={mutation.isPending} onClick={close}>Закрыть</Button></div>
    <Dialog.Description>Переключайте магазины и управляйте подключениями.</Dialog.Description>
    <LoadingOverlay active={query.isFetching||mutation.isPending} label={mutation.isPending?'Сохраняем подключение…':'Загружаем аккаунты…'}/>
    {query.isError&&<Callout.Root color="red"><Callout.Text>{query.error.message}</Callout.Text></Callout.Root>}
    {mutation.isError&&<Callout.Root color="red"><Callout.Text>{mutation.error.message}</Callout.Text></Callout.Root>}
    {query.data&&<>
      {query.data.mode==='MOCK'&&<Badge color="amber">Демонстрационные магазины</Badge>}
      <div className="account-grid">{query.data.items.map(item=><AccountCard key={item.id} item={item} active={query.data?.active_id===item.id} revision={query.data!.revision} mode={query.data!.mode} pending={mutation.isPending} onEdit={()=>edit(item)} onSelect={()=>mutation.mutate({action:'select',id:item.id})} onDelete={()=>setDeleteId(item.id)}/>)}</div>
      {query.data.items.length===0&&<div className="empty-state"><Heading size="4">Добавьте первый магазин</Heading><Text color="gray">Подключение будет доступно только на этом компьютере.</Text></div>}
      {deleteId&&<Callout.Root color="amber"><Callout.Text>Удалить подключение «{query.data.items.find(x=>x.id===deleteId)?.name}» с этого компьютера? Данные магазина в Ozon останутся.</Callout.Text><Flex gap="2" wrap="wrap"><Button color="red" disabled={mutation.isPending} onClick={()=>mutation.mutate({action:'delete',id:deleteId})}>Подтвердить удаление</Button><Button variant="outline" onClick={()=>setDeleteId(null)}>Отмена</Button></Flex></Callout.Root>}
      <div className="dialog-actions"><Button onClick={()=>edit(null)}>Добавить магазин</Button>{query.data.legacy_available&&<Button variant="outline" disabled={mutation.isPending} onClick={()=>mutation.mutate({action:'import',data:{name:'Мой магазин'}})}>Добавить текущее подключение</Button>}</div>
    </>}
    {editing!==undefined&&<form className="account-editor" autoComplete="off" onSubmit={event=>{event.preventDefault();mutation.mutate({action:editing?'update':'add',id:editing?.id,data:mock?{name}:{name,client_id:clientId,api_key:key}})}}>
      <div className="section-heading"><Heading size="4">{editing?'Изменить подключение':'Новый магазин'}</Heading><Text size="2" color="gray">{mock?'Укажите название демонстрационного магазина.':editing?'Пустые поля доступа сохранят прежние значения.':'Введите данные из личного кабинета Ozon Seller.'}</Text></div>
      <label className="search-label">Название магазина<TextField.Root required maxLength={100} value={name} onChange={e=>setName(e.target.value)} placeholder="Например, Основной магазин"/></label>
      {!mock&&<div className="account-credentials"><label className="search-label">{editing?'Новый Client-Id':'Client-Id'}<TextField.Root required={!editing} inputMode="numeric" value={clientId} onChange={e=>setClientId(e.target.value)} autoComplete="off"/></label>
      <label className="search-label">{editing?'Новый API-ключ':'API-ключ'}<TextField.Root type="password" required={!editing} value={key} onChange={e=>setKey(e.target.value)} autoComplete="new-password" maxLength={4096}/></label></div>}
      <div className="dialog-actions"><Button disabled={mutation.isPending} type="submit">{mock?'Сохранить магазин':'Сохранить подключение'}</Button><Button type="button" variant="outline" onClick={()=>{setEditing(undefined);setKey('');setClientId('')}}>Отмена</Button></div>
    </form>}
  </Dialog.Content></Dialog.Root>
}
