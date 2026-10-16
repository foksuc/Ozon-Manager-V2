"""Local operation history, encrypted before every disk write (Windows user DPAPI)."""
from copy import deepcopy
import json
import hashlib
import os
from pathlib import Path
import re
import threading
from uuid import UUID,uuid4
from .accounts import dpapi
from .storage import confined

MAX_BYTES=16*1024*1024
FORBIDDEN_KEYS={'api_key','apikey','api-key','client_id','client-id','client_secret','access_token','refresh_token','credentials','authorization','headers','password','cookie','set-cookie'}


class HistoryError(ValueError):
    pass


def check_payload(value,depth=0):
    if depth>32:raise HistoryError('История содержит слишком глубокие данные')
    if isinstance(value,dict):
        for key,item in value.items():
            if not isinstance(key,str) or key.lower() in FORBIDDEN_KEYS:
                raise HistoryError('Секретные поля запрещены в истории операций')
            check_payload(item,depth+1)
    elif isinstance(value,list):
        for item in value:check_payload(item,depth+1)
    elif value is not None and type(value) not in (str,int,float,bool):
        raise HistoryError('Неподдерживаемое значение истории операций')


class EncryptedHistory:
    def __init__(self,root,namespace,cipher=dpapi,*,directory=None):
        if not re.fullmatch('[a-z][a-z0-9-]{0,31}',namespace):raise HistoryError('Некорректный раздел истории')
        self.root=Path(root)
        folder=Path(directory) if directory is not None else self.root/'.cache'/'history'
        self.path=confined(folder/(namespace+'.bin'),self.root)
        self.cipher=cipher
        self.lock=threading.RLock()
        self._events={}
        try:
            if self.path.exists():
                if self.path.stat().st_size>MAX_BYTES:raise HistoryError('Файл истории превышает допустимый размер')
                decoded=cipher(self.path.read_bytes(),decrypt=True)
                if len(decoded)>MAX_BYTES:raise HistoryError('История превышает допустимый размер')
                document=json.loads(decoded)
                if not isinstance(document,dict) or set(document)!={'version','events'} or document['version']!=1 or not isinstance(document['events'],list):
                    raise HistoryError('Неизвестный формат истории')
                for event in document['events']:
                    self._validate(event)
                    if event['id'] in self._events:raise HistoryError('Повтор записи истории')
                    self._events[event['id']]=event
        except (OSError,ValueError,TypeError,KeyError,UnicodeError):
            raise HistoryError('Не удалось открыть зашифрованную историю; отправка запрещена') from None

    @staticmethod
    def _validate(event):
        if not isinstance(event,dict) or not isinstance(event.get('id'),str):raise HistoryError('Некорректная запись истории')
        try:
            if str(UUID(event['id']))!=event['id']:raise ValueError
        except ValueError:raise HistoryError('Некорректный идентификатор истории') from None
        check_payload(event)

    def entries(self):
        with self.lock:return deepcopy(list(self._events.values()))

    def upsert(self,event):
        self._validate(event)
        with self.lock:
            pending={**self._events,event['id']:deepcopy(event)}
            try:
                plaintext=json.dumps({'version':1,'events':list(pending.values())},ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
                if len(plaintext)>MAX_BYTES:raise HistoryError('История заполнена; освободите место через архивирование перед отправкой')
                encrypted=self.cipher(plaintext)
                if not isinstance(encrypted,bytes) or not encrypted or len(encrypted)>MAX_BYTES:
                    raise HistoryError('Не удалось зашифровать историю')
                directory=confined(self.path.parent,self.root)
                directory.mkdir(parents=True,exist_ok=True)
                temporary=confined(directory/(uuid4().hex+'.tmp'),self.root)
                with temporary.open('xb') as stream:
                    stream.write(encrypted)
                    stream.flush()
                    os.fsync(stream.fileno())
                confined(self.path,self.root)
                os.replace(temporary,self.path)
            except (OSError,ValueError,TypeError):
                raise HistoryError('Не удалось сохранить зашифрованную историю; повторная отправка запрещена') from None
            self._events=pending


def account_history(accounts,namespace,*,root=None):
    """Keep custom/test account stores and their history in the SAME directory."""
    base=Path(root if root is not None else accounts.root)
    prefix='mock-' if accounts.mode=='MOCK' else ''
    account_path=Path(getattr(accounts,'path',base/'.cache/accounts.bin'))
    suffix=hashlib.sha256(str(account_path).encode()).hexdigest()[:12]
    return EncryptedHistory(base,prefix+namespace+'-'+suffix,getattr(accounts,'cipher',dpapi),
        directory=account_path.parent/'history')
