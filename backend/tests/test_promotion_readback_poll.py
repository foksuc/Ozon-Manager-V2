"""Delayed promotion reads never cause another write (synthetic transport)."""
import asyncio
from copy import deepcopy

import pytest

from app.ozon_write import WriteOperation as Op
from test_mutations import Fixture


@pytest.mark.parametrize('operation', [Op.ACTION_UPDATE, Op.ACTION_REMOVE])
@pytest.mark.parametrize('initial_read', ['incomplete', 'stale', 'duplicate'])
def test_promotion_waits_for_read_consistency(tmp_path, operation, initial_read):
    async def run():
        fixture = Fixture(tmp_path)
        original = deepcopy(fixture.items)
        reads_after_send = 0
        delays = []
        async def sleep(delay):
            assert fixture.accounts.busy and fixture.calls == 1
            delays.append(delay)
        async def fresh(op, scope, ids):
            nonlocal reads_after_send
            if fixture.calls:
                reads_after_send += 1
                if reads_after_send == 1:
                    if initial_read == 'incomplete':
                        return {'complete': False, 'items': []}
                    if initial_read == 'duplicate':
                        return {'complete': True, 'items': [original['a'], fixture.items['a']]}
                    return {'complete': True, 'items': list(original.values())}
            return await fixture.fresh(op, scope, ids)
        fixture.service.fresh = fresh
        fixture.service.readback_sleep = sleep
        plan = await fixture.service.preview(operation, {'action_id': '3'},
            [{'id': 'a', 'after': '12' if operation is Op.ACTION_UPDATE else None}])
        result = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        assert result['status'] == 'VERIFIED_SUCCESS'
        assert fixture.calls == 1 and reads_after_send == 2 and delays == [0.5]
    asyncio.run(run())


def test_unresolved_poll_is_bounded_and_rejections_are_not_polled(tmp_path):
    async def run(reject):
        fixture = Fixture(tmp_path / str(reject))
        fixture.apply = False
        if reject:
            fixture.reject.add('a')
        delays = []
        async def sleep(delay):
            delays.append(delay)
        fixture.service.readback_sleep = sleep
        plan = await fixture.service.preview(Op.ACTION_UPDATE, {'action_id': '3'}, [{'id': 'a', 'after': '12'}])
        result = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        assert fixture.calls == 1
        assert result['status'] == ('REJECTED' if reject else 'DISCREPANCY')
        assert delays == ([] if reject else [0.5, 1.0, 2.0])
    asyncio.run(run(False))
    asyncio.run(run(True))


@pytest.mark.parametrize('change_account', [False, True])
def test_missing_reads_remain_unknown_and_binding_change_stops_poll(tmp_path, change_account):
    async def run():
        fixture = Fixture(tmp_path)
        after_send_reads = 0
        delays = []
        async def fresh(op, scope, ids):
            nonlocal after_send_reads
            if fixture.calls:
                after_send_reads += 1
                return {'complete': False, 'items': []}
            return await fixture.fresh(op, scope, ids)
        async def sleep(delay):
            delays.append(delay)
            if change_account:
                # Simulate an external revision change despite the normal UI lock.
                fixture.accounts.revision += 1
        fixture.service.fresh = fresh
        fixture.service.readback_sleep = sleep
        plan = await fixture.service.preview(Op.ACTION_REMOVE, {'action_id': '3'}, [{'id': 'a', 'after': None}])
        result = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        assert result['status'] == 'UNKNOWN_RESULT' and fixture.calls == 1
        assert after_send_reads == (1 if change_account else 4)
        assert delays == ([0.5] if change_account else [0.5, 1.0, 2.0])
    asyncio.run(run())
