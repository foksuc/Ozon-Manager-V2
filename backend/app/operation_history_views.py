"""Read-only unified views over the encrypted operation journals."""
from copy import deepcopy
from .vendor_errors import normalize_vendor_error

STATUS={'OK':'VERIFIED_SUCCESS','ERROR':'REJECTED','TIMEOUT':'UNKNOWN_REPORT_RESULT','CANCEL':'REJECTED'}
FINANCE={'mutual':'Акт сверки','compensation':'Компенсации','decompensation':'Декомпенсации'}
LABELS={'STRATEGY_CREATE':'Создать стратегию','STRATEGY_UPDATE':'Изменить стратегию','STRATEGY_ADD_PRODUCTS':'Добавить товары в стратегию','STRATEGY_REMOVE_PRODUCTS':'Удалить товары из стратегии','STRATEGY_DELETE':'Удалить стратегию','STRATEGY_STATUS':'Изменить статус стратегии','PRICE_UPDATE':'Изменить цены товаров','PRODUCT_IMPORT':'Импорт товаров','PRODUCT_ARCHIVE':'Архивировать товары','PRODUCT_UNARCHIVE':'Вернуть товары из архива','REPORT_CREATE':'Создать отчёт'}


def item_view(item,index,event_id,status):
    errors=item.get('errors',[])
    if item.get('reason') is not None:errors=[*errors,{'reason':item['reason']}]
    if item.get('error') is not None:errors=[*errors,item['error']]
    normalized=[normalize_vendor_error(error) for error in errors if isinstance(error,(str,dict))]
    scalar=lambda key:item.get(key) if type(item.get(key)) in (str,int,float) else None
    return {'id':str(item.get('id',item.get('product_id',f'{event_id}:{index}'))),
        'name':str(item.get('name',item.get('offer_id',f'Строка {index+1}'))),'sku':item.get('sku'),
        'offer_id':str(item.get('offer_id','')),'status':item.get('status',status),
        'before':scalar('before'),'after':scalar('after'),'actual':scalar('actual'),
        'currency':item.get('currency'),'errors':normalized}


def event_view(event,source):
    event=deepcopy(event)
    identity=event.get('id',event.get('plan_id'))
    status=STATUS.get(event.get('status'),event.get('status','UNKNOWN_RESULT'))
    operation=event.get('operation','')
    if source=='finance':label='Финансы — '+FINANCE.get(event.get('kind'),'Документ')
    elif source=='performance':label='Статистика Performance — отчёт'
    else:label=('Склад — ' if source=='warehouse' else 'Стратегии — ')+LABELS.get(operation,'Операция')
    rows=[item_view(item,index,identity,status) for index,item in enumerate(event.get('items',event.get('rows',[]))) if isinstance(item,dict)]
    errors=event.get('errors',[])
    if event.get('error') is not None:errors=[*errors,event['error']]
    if errors:rows.append(item_view({'errors':errors,'name':'Ответ Ozon'},len(rows),identity,status))
    reference=event.get('report_uuid',event.get('code'))
    message=event.get('message',label)
    if isinstance(reference,str):message+=' · '+('UUID отчёта: ' if source=='performance' else 'Код документа: ')+reference
    return {'id':identity,'operation':None,'operation_label':label,'account_id':event.get('account_id'),
        'mode':event.get('mode'),'status':status,'created_at':event.get('created_at'),
        'attempted_at':event.get('attempted_at'),'message':message,
        'rows':rows,'transport_error':event.get('transport_error'),'can_rollback':False,
        'rollback_blocked_reason':'Откат этой операции не предусмотрен в общей истории'}
