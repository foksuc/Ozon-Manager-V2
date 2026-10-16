import json
from uuid import uuid4
import pytest
from app.operation_history import EncryptedHistory,HistoryError
from test_accounts import cipher


def test_encrypted_roundtrip_reason_and_fail_closed_secrets(tmp_path):
    history=EncryptedHistory(tmp_path,'test',cipher)
    event={'id':str(uuid4()),'status':'REJECTED','reason':'Synthetic vendor message','reason_ru':'Тестовая причина'}
    history.upsert(event)
    assert b'Synthetic vendor message' not in history.path.read_bytes()
    assert EncryptedHistory(tmp_path,'test',cipher).entries()==[event]
    with pytest.raises(HistoryError):history.upsert(event|{'api_key':'SYNTHETIC'})
    assert history.entries()==[event]
    event['reason']='changed client result'
    assert history.entries()[0]['reason']=='Synthetic vendor message'


def test_corrupt_encrypted_history_cannot_be_ignored(tmp_path):
    history=EncryptedHistory(tmp_path,'test',cipher)
    history.path.parent.mkdir(parents=True)
    history.path.write_bytes(b'invalid-ciphertext')
    with pytest.raises(HistoryError):EncryptedHistory(tmp_path,'test',cipher)


def test_failed_replace_preserves_old_history(tmp_path,monkeypatch):
    history=EncryptedHistory(tmp_path,'test',cipher)
    event={'id':str(uuid4()),'status':'UNKNOWN_RESULT'}
    history.upsert(event)
    old=history.path.read_bytes()
    def denied(*args):raise OSError('Synthetic write failure')
    monkeypatch.setattr('app.operation_history.os.replace',denied)
    with pytest.raises(HistoryError):history.upsert(event|{'status':'VERIFIED_SUCCESS'})
    assert history.path.read_bytes()==old
    assert history.entries()==[event]


def test_real_windows_dpapi_history_roundtrip(tmp_path):
    history=EncryptedHistory(tmp_path,'dpapi')
    event={'id':str(uuid4()),'status':'REJECTED','reason':'SYNTHETIC_HISTORY_CONTENT'}
    history.upsert(event)
    assert b'SYNTHETIC_HISTORY_CONTENT' not in history.path.read_bytes()
    assert EncryptedHistory(tmp_path,'dpapi').entries()==[event]
