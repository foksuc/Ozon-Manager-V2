"""Only synthetic RAM state and synthetic writer; no Seller network/real keys."""
import asyncio
from copy import deepcopy
from datetime import datetime
import json
import pytest
from app.mutations import MutationService
from app.ozon_write import WriteError, WriteOperation as Op
from test_mutations import Fixture


def scope_for(op):
    if op in (Op.PRICE, Op.STOCK):
        return {}
    scope = {'action_id': '321'}
    if op in (Op.AUTO_UPDATE, Op.AUTO_REMOVE):
        scope['auto_add_date'] = '2026-10-09T00:00:00Z'
    return scope


@pytest.mark.parametrize('op', list(Op))
def test_full_inverse_uses_original_all_rows_and_separate_confirmation(tmp_path, op):
    async def run():
        fixture = Fixture(tmp_path)
        for index, row in enumerate(fixture.items.values()):
            row.update(name=f'Synthetic {index}', sku=str(900 + index), offer_id=f'SYN-{index}')
            if op is Op.STOCK:
                row.update(current=10 + index, reserved=2, present=12 + index, warehouse_id='555')
        before = deepcopy(fixture.items)
        changes = [{'id': key, 'after': None if op in (Op.ACTION_REMOVE, Op.AUTO_REMOVE)
                    else 30 if op is Op.STOCK else '30.00'} for key in fixture.items]
        original = await fixture.service.preview(op, scope_for(op), changes)
        result = await fixture.service.execute(original['id'], original['hash'], confirmed=True)
        assert result['status'] == 'VERIFIED_SUCCESS'
        assert fixture.service.history()[0]['can_rollback']
        fixture.now += 31  # Stock per-target rate limit is also kept for inverse.
        inverse = await fixture.service.rollback_preview(original['id'])
        assert fixture.calls == 1 and inverse['rollback_of'] == original['id']
        assert {row['id'] for row in inverse['rows']} == set(before)
        with pytest.raises(WriteError) as error:
            await fixture.service.execute(inverse['id'], inverse['hash'])
        assert error.value.code == 'CONFIRMATION_REQUIRED' and fixture.calls == 1
        inverse['rows'][0]['after'] = '999'  # UI payload cannot replace saved inverse.
        reverted = await fixture.service.execute(inverse['id'], inverse['hash'], confirmed=True)
        assert reverted['status'] == 'VERIFIED_SUCCESS' and fixture.calls == 2
        for key, row in before.items():
            assert fixture.items[key]['current'] == row['current']
            assert fixture.items[key]['member'] == row['member']
            if op is Op.STOCK:
                assert fixture.items[key]['reserved'] == row['reserved']
                assert fixture.items[key]['present'] == row['present']
        history = fixture.service.history()
        assert history[0]['rollback_of'] == original['id']
        assert not next(item for item in history if item['id'] == original['id'])['can_rollback']
    asyncio.run(run())


@pytest.mark.parametrize('op', [Op.ACTION_UPDATE, Op.AUTO_UPDATE])
def test_new_members_inverse_removes_membership(tmp_path, op):
    async def run():
        fixture = Fixture(tmp_path)
        fixture.items['a'].update(member=False, current=None)
        plan = await fixture.service.preview(op, scope_for(op), [{'id': 'a', 'after': '15'}])
        await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        inverse = await fixture.service.rollback_preview(plan['id'])
        assert inverse['operation'] == ('action_remove' if op is Op.ACTION_UPDATE else 'auto_remove')
        result = await fixture.service.execute(inverse['id'], inverse['hash'], confirmed=True)
        assert result['status'] == 'VERIFIED_SUCCESS' and fixture.items['a']['member'] is False
    asyncio.run(run())


@pytest.mark.parametrize('stage', ['preview', 'execute'])
def test_concurrent_price_change_blocks_inverse_before_any_send(tmp_path, stage):
    async def run():
        fixture = Fixture(tmp_path)
        plan = await fixture.service.preview('prices', {}, [{'id': 'a', 'after': '12'}])
        await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        inverse = await fixture.service.rollback_preview(plan['id']) if stage == 'execute' else None
        fixture.items['a']['current'] = '13.00'
        with pytest.raises(WriteError) as error:
            if inverse:
                await fixture.service.execute(inverse['id'], inverse['hash'], confirmed=True)
            else:
                await fixture.service.rollback_preview(plan['id'])
        assert error.value.code == 'FRESH_CHANGED' and fixture.calls == 1
        assert fixture.items['a']['current'] == '13.00'
    asyncio.run(run())


def test_stock_reserve_change_blocks_full_restore(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        fixture.items['a'].update(current=10, reserved=2, present=12, warehouse_id='555')
        plan = await fixture.service.preview('stocks', {}, [{'id': 'a', 'after': 20}])
        await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        fixture.items['a'].update(reserved=3, present=23)
        with pytest.raises(WriteError) as error:
            await fixture.service.rollback_preview(plan['id'])
        assert error.value.code == 'FRESH_CHANGED' and fixture.calls == 1
    asyncio.run(run())


def test_later_reconcile_does_not_replace_original_post_operation_snapshot(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        fixture.items['a'].update(current=10, reserved=2, present=12, warehouse_id='555')
        plan = await fixture.service.preview('stocks', {}, [{'id': 'a', 'after': 20}])
        await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        fixture.items['a'].update(reserved=3, present=23)
        result = await fixture.service.reconcile(plan['id'], plan['hash'])
        assert result['status'] == 'VERIFIED_SUCCESS'  # Available20 still matches.
        with pytest.raises(WriteError) as error:
            await fixture.service.rollback_preview(plan['id'])
        assert error.value.code == 'FRESH_CHANGED' and fixture.calls == 1
    asyncio.run(run())


def test_reconcile_retains_rejection_and_errors_instead_of_claiming_external_change(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        fixture.reject.add('a')
        plan = await fixture.service.preview('prices', {}, [{'id': 'a', 'after': '12'}])
        rejected = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        assert rejected['status'] == 'REJECTED'
        fixture.items['a']['current'] = '12.00'  # Another actor changed the value.
        result = await fixture.service.reconcile(plan['id'], plan['hash'])
        assert result['status'] == 'DISCREPANCY'
        assert result['rows'][0]['accepted'] is False
        assert result['rows'][0]['errors'] == rejected['rows'][0]['errors']
        assert fixture.calls == 1 and fixture.service.history()[0]['can_rollback'] is False
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['partial', 'unknown', 'account', 'revision', 'missing', 'mixed'])
def test_unsafe_original_never_prepares_full_inverse(tmp_path, failure):
    async def run():
        fixture = Fixture(tmp_path)
        op = Op.ACTION_UPDATE if failure == 'mixed' else Op.PRICE
        if failure == 'mixed':
            fixture.items['a'].update(member=False, current=None)
        if failure == 'partial':
            fixture.reject.add('b')
        if failure == 'unknown':
            fixture.apply = False
            fixture.send_timeout = True
        original = await fixture.service.preview(op, scope_for(op), [{'id': 'a', 'after': '12'}, {'id': 'b', 'after': '22'}])
        await fixture.service.execute(original['id'], original['hash'], confirmed=True)
        if failure == 'account':
            fixture.accounts.add('Second synthetic')
            fixture.accounts.select(next(key for key in fixture.accounts._accounts if key != fixture.accounts.active_id))
        if failure == 'revision':
            fixture.accounts.revision += 1
        if failure == 'missing':
            fixture.service._plans.pop(original['id'])
        with pytest.raises(WriteError) as error:
            await fixture.service.rollback_preview(original['id'])
        assert error.value.code == 'ROLLBACK_BLOCKED' and fixture.calls == 1
        assert fixture.service.history()[0]['can_rollback'] is False
        assert fixture.service.history()[0]['rollback_blocked_reason']
    asyncio.run(run())


def test_two_inverse_previews_cannot_send_twice(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        original = await fixture.service.preview('prices', {}, [{'id': 'a', 'after': '12'}])
        await fixture.service.execute(original['id'], original['hash'], confirmed=True)
        first = await fixture.service.rollback_preview(original['id'])
        second = await fixture.service.rollback_preview(original['id'])
        await fixture.service.execute(first['id'], first['hash'], confirmed=True)
        with pytest.raises(WriteError) as error:
            await fixture.service.execute(second['id'], second['hash'], confirmed=True)
        assert error.value.code == 'ROLLBACK_BLOCKED' and fixture.calls == 2
    asyncio.run(run())


def test_live_auto_remove_cannot_bypass_expired_restore_contract(tmp_path,monkeypatch):
    from datetime import datetime, timezone
    from app import ozon_write
    moment=[datetime(2026,10,9,tzinfo=timezone.utc)]
    monkeypatch.setattr(ozon_write,'_utc_now',lambda:moment[0])
    async def run():
        fixture = Fixture(tmp_path, live=True)
        original = await fixture.service.preview('auto_remove', scope_for(Op.AUTO_REMOVE), [{'id': 'a', 'after': None}])
        await fixture.service.execute(original['id'], original['hash'], confirmed=True)
        assert fixture.service.history()[0]['can_rollback']
        moment[0]=ozon_write.LEGACY_VALID_UNTIL
        history = fixture.service.history()[0]
        assert not history['can_rollback'] and 'период старого API завершён' in history['rollback_blocked_reason']
        with pytest.raises(WriteError) as error:
            await fixture.service.rollback_preview(original['id'])
        assert error.value.code == 'ROLLBACK_BLOCKED' and fixture.calls == 1
    asyncio.run(run())


def test_history_log_ram_only_timestamps_and_redacted_per_target_errors(tmp_path):
    async def run():
        fixture = Fixture(tmp_path, live=True)
        factory = fixture.writer
        def writer(credentials):
            value = factory(credentials)
            async def send(op, body):
                fixture.calls += 1
                return {'result': [{'product_id': 111, 'updated': False, 'errors': [
                    {'code': 'SYNTHETIC_ERROR', 'message': 'synthetic-key rejected', 'api_key': 'synthetic-key'}]}],
                    'headers': {'Api-Key': 'synthetic-key'}}
            value.send = send
            return value
        fixture.service.writer_factory = writer
        fixture.items['a'].update(name='Synthetic example', sku='9900', offer_id='SYN-1')
        plan = await fixture.service.preview('prices', {}, [{'id': 'a', 'after': '12'}])
        result = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        history = fixture.service.history()[0]
        datetime.fromisoformat(history['created_at'])
        datetime.fromisoformat(history['attempted_at'])
        assert result['rows'][0]['actual'] == '10.00'
        assert result['rows'][0]['errors'][0]['code'] == 'SYNTHETIC_ERROR'
        assert history['plan_rows'][0]['name'] == 'Synthetic example'
        assert 'synthetic-key' not in json.dumps(history) and 'headers' not in json.dumps(history)
        marker = json.loads(next((tmp_path / 'attempts').glob('*.json')).read_text())
        assert set(marker) == {'id', 'status'}
        restarted = MutationService(fixture.accounts, fixture.writer, fixture.fresh, root=tmp_path, path=tmp_path / 'attempts')
        with pytest.raises(WriteError) as error:
            await restarted.rollback_preview(plan['id'])
        assert error.value.code == 'ROLLBACK_BLOCKED'
    asyncio.run(run())


def test_mock_route_full_flow_and_client_value_override_rejected(tmp_path):
    from fastapi.testclient import TestClient
    from app.connection import Connection
    from app.main import create_app
    from app.storage import Store
    application = create_app(Store(tmp_path / 'mock.sqlite3', root=tmp_path), connection=Connection('MOCK'))
    with TestClient(application, base_url='http://localhost:8000') as client:
        response = client.post('/api/mutations/preview', json={'operation': 'prices', 'request': {
            'kind': 'prices', 'ids': ['mock-product-1-1'], 'action_id': 'mock-action-1',
            'mode': 'manual', 'value': '1200', 'rounding': '1'}})
        assert response.status_code == 200, response.text
        original = response.json()
        execute = client.post('/api/mutations/execute', json={'plan_id': original['id'], 'plan_hash': original['hash'], 'confirmed': True})
        assert execute.status_code == 200 and execute.json()['status'] == 'VERIFIED_SUCCESS'
        assert client.post('/api/mutations/rollback-preview', json={'operation_id': original['id'], 'after': '9999'}).status_code == 422
        inverse_response = client.post('/api/mutations/rollback-preview', json={'operation_id': original['id']})
        assert inverse_response.status_code == 200, inverse_response.text
        inverse = inverse_response.json()
        assert inverse['rows'][0]['after'] == '1000.00'
        result = client.post('/api/mutations/execute', json={'plan_id': inverse['id'], 'plan_hash': inverse['hash'], 'confirmed': True})
        assert result.status_code == 200 and result.json()['status'] == 'VERIFIED_SUCCESS'
        history = client.get('/api/mutations/history').json()['items']
        assert history[0]['rollback_of'] == original['id'] and history[0]['rows'][0]['actual'] == '1000.00'
