"""Dated official AUTO_REMOVE permission. Every send is synthetic only."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import httpx
import pytest
from app import ozon_write
from app.ozon_write import WriteClient, WriteOperation as Op, WriteError, AUTO_REMOVE_VALID_UNTIL as CUTOFF
from app.connection import Credentials
from test_mutations import Fixture
from test_action_currency_guards import isolated_api
from fastapi.testclient import TestClient

BODY={'action_id':3,'auto_add_date':'2026-10-13T21:00:00Z','product_ids':['111']}


def test_auto_remove_before_cutoff_and_exact_boundary(monkeypatch):
    monkeypatch.setattr(ozon_write,'_utc_now',lambda:CUTOFF-timedelta(microseconds=1))
    assert ozon_write.blocked_reason(Op.AUTO_REMOVE) is None
    assert ozon_write.blocked_reason(Op.ACTION_REMOVE) is None
    assert ozon_write.blocked_reason(Op.ACTION_UPDATE) is None
    assert ozon_write.blocked_reason(Op.AUTO_UPDATE) is None
    monkeypatch.setattr(ozon_write,'_utc_now',lambda:CUTOFF)
    assert ozon_write.blocked_reason(Op.AUTO_REMOVE)


def test_auto_remove_synthetic_transport_uses_only_ids(monkeypatch):
    monkeypatch.setattr(ozon_write,'_utc_now',lambda:datetime(2026,10,9,tzinfo=timezone.utc))
    async def run():
        calls=[]
        def handler(request):
            calls.append(request)
            assert request.url.path=='/v2/actions/auto-add/products/delete'
            assert json.loads(request.content)==BODY
            return httpx.Response(200,json={'product_ids':['111']})
        client=WriteClient(Credentials('1','SYNTHETIC'),transport=httpx.MockTransport(handler))
        try:
            assert (await client.send(Op.AUTO_REMOVE,BODY))['product_ids']==['111']
            assert len(calls)==1
        finally:await client.close()
    asyncio.run(run())


def test_auto_remove_assumed_currency_can_preview_without_creating_writer(tmp_path,monkeypatch):
    monkeypatch.setattr(ozon_write,'_utc_now',lambda:datetime(2026,10,9,tzinfo=timezone.utc))
    app,payload,_=isolated_api(tmp_path,monkeypatch,auto=True,assumed=True)
    with TestClient(app) as api:
        response=api.post('/api/mutations/preview',json={'operation':'auto_remove','request':payload})
        assert response.status_code==200,response.text
        assert response.json()['valid'] and response.json()['blocked_reason'] is None
        assert response.json()['rows'][0]['after'] is None


@pytest.mark.parametrize('expire_during_fresh',[False,True])
def test_auto_remove_permission_rechecked_at_execute_and_after_fresh(tmp_path,monkeypatch,expire_during_fresh):
    moment=[CUTOFF-timedelta(minutes=1)]
    monkeypatch.setattr(ozon_write,'_utc_now',lambda:moment[0])
    async def run():
        fixture=Fixture(tmp_path,live=True)
        scope={'action_id':'3','auto_add_date':BODY['auto_add_date']}
        plan=await fixture.service.preview('auto_remove',scope,[{'id':'a','after':None}])
        assert plan['valid']
        if expire_during_fresh:
            original=fixture.service.fresh
            async def expire(*args):
                result=await original(*args);moment[0]=CUTOFF;return result
            fixture.service.fresh=expire
        else:moment[0]=CUTOFF
        with pytest.raises(WriteError) as error:
            await fixture.service.execute(plan['id'],plan['hash'],confirmed=True)
        assert error.value.code=='CONTRACT_BLOCKED' and fixture.calls==0
        assert not fixture.service._plans[plan['id']]['attempted']
    asyncio.run(run())


def test_transport_rechecks_permission_after_lock_before_network(monkeypatch):
    moment=[CUTOFF-timedelta(minutes=1)]
    monkeypatch.setattr(ozon_write,'_utc_now',lambda:moment[0])
    async def run():
        calls=[]
        client=WriteClient(Credentials('1','SYNTHETIC'),transport=httpx.MockTransport(lambda request:calls.append(request)))
        await client._lock.acquire()
        task=asyncio.create_task(client.send(Op.AUTO_REMOVE,BODY))
        await asyncio.sleep(0) # initial check passed; request is waiting for lock
        moment[0]=CUTOFF;client._lock.release()
        try:
            with pytest.raises(WriteError) as error:await task
            assert error.value.code=='CONTRACT_BLOCKED' and not calls
        finally:await client.close()
    asyncio.run(run())


def test_known_pre_send_contract_refusal_is_not_ambiguous(tmp_path,monkeypatch):
    monkeypatch.setattr(ozon_write,'_utc_now',lambda:CUTOFF-timedelta(minutes=1))
    async def run():
        fixture=Fixture(tmp_path,live=True)
        scope={'action_id':'3','auto_add_date':BODY['auto_add_date']}
        plan=await fixture.service.preview('auto_remove',scope,[{'id':'a','after':None}])
        class Refusal:
            async def send(self,*args):raise WriteError('CONTRACT_BLOCKED','Synthetic pre-network expiry',409)
            async def close(self):pass
        fixture.service.writer_factory=lambda credentials:Refusal()
        with pytest.raises(WriteError):await fixture.service.execute(plan['id'],plan['hash'],confirmed=True)
        assert fixture.calls==0 and not fixture.service._unresolved()
        assert fixture.service.history()[0]['status']=='REJECTED'
        assert fixture.service._plans[plan['id']]['attempted']
    asyncio.run(run())
