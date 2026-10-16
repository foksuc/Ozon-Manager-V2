"""Return from encrypted original removal, including crash/restart guards."""
import asyncio
from copy import deepcopy
from datetime import datetime,timezone
import pytest
from app.mutations import MutationService
from app.ozon_write import WriteOperation as Op,WriteError
from test_mutations import Fixture


def setup(tmp_path,monkeypatch):
    monkeypatch.setattr('app.ozon_write._utc_now',lambda:datetime(2026,10,13,tzinfo=timezone.utc))
    fixture=Fixture(tmp_path,live=True)
    fixture.items={'a':{'id':'a','product_id':'111','current':'1899.05','action_price':'1899.05',
        'price':'1999.00','currency':'RUB','member':True,
        'money_currencies':{'price':'RUB','action_price':'RUB'}}}
    async def fresh(op,scope,ids):
        rows=deepcopy(list(fixture.items.values()))
        for row in rows:
            row['role']='member' if row['member'] else 'candidate'
            if not row['member']:row['current']=None
        return {'complete':True,'items':rows}
    fixture.fresh=fresh
    def service():
        return MutationService(fixture.accounts,fixture.writer,fresh,root=tmp_path,
            path=tmp_path/'attempts',clock=lambda:fixture.now)
    fixture.service=service()
    return fixture,service


def test_original_price_return_after_restart_and_second_return_blocked(tmp_path,monkeypatch):
    async def run():
        fixture,restart=setup(tmp_path,monkeypatch)
        original=await fixture.service.preview(Op.AUTO_REMOVE,
            {'action_id':'321','auto_add_date':'2026-10-20T00:00:00Z'},[{'id':'a','after':None}])
        assert (await fixture.service.execute(original['id'],original['hash'],confirmed=True))['status']=='VERIFIED_SUCCESS'
        fixture.accounts.revision+=3
        fixture.service=restart()
        inverse=await fixture.service.rollback_preview(original['id'])
        assert fixture.calls==1 and inverse['rows'][0]['after']=='1899.05'
        assert inverse['effects'][0]['scheduled_card_price']['at']=='2026-10-20T00:00:00Z'
        returned=await fixture.service.execute(inverse['id'],inverse['hash'],confirmed=True)
        assert returned['status']=='VERIFIED_SUCCESS' and fixture.calls==2
        fixture.service=restart()
        with pytest.raises(WriteError) as error:await fixture.service.rollback_preview(original['id'])
        assert error.value.code=='ROLLBACK_BLOCKED' and fixture.calls==2
    asyncio.run(run())


def test_changed_baseline_after_removal_never_recalculates_discount(tmp_path,monkeypatch):
    async def run():
        fixture,restart=setup(tmp_path,monkeypatch)
        original=await fixture.service.preview(Op.AUTO_REMOVE,
            {'action_id':'321','auto_add_date':'2026-10-20T00:00:00Z'},[{'id':'a','after':None}])
        await fixture.service.execute(original['id'],original['hash'],confirmed=True)
        fixture.service=restart()
        fixture.items['a']['price']='2100.00'
        with pytest.raises(WriteError) as error:await fixture.service.rollback_preview(original['id'])
        assert error.value.code=='FRESH_CHANGED' and fixture.calls==1
    asyncio.run(run())


def test_other_seller_cannot_return_from_recovered_snapshot(tmp_path,monkeypatch):
    async def run():
        fixture,restart=setup(tmp_path,monkeypatch)
        original=await fixture.service.preview(Op.AUTO_REMOVE,
            {'action_id':'321','auto_add_date':'2026-10-20T00:00:00Z'},[{'id':'a','after':None}])
        await fixture.service.execute(original['id'],original['hash'],confirmed=True)
        fixture.service=restart()
        fixture.service._account_digest=lambda:'another-seller'
        with pytest.raises(WriteError) as error:await fixture.service.rollback_preview(original['id'])
        assert error.value.code=='ROLLBACK_BLOCKED' and fixture.calls==1
    asyncio.run(run())
