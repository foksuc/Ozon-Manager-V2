"""Current promotion profile safety; entirely synthetic transport and data."""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from app import ozon_write
from app.mutations import MutationService
from app.ozon_write import WriteOperation as Op, WriteError
from test_mutations import Fixture


@pytest.fixture(autouse=True)
def current_profile(monkeypatch):
    monkeypatch.setattr(ozon_write, '_utc_now', lambda: datetime(2026, 10, 13, tzinfo=timezone.utc))


def fixture_with_card(tmp_path, voucher=False):
    fixture = Fixture(tmp_path, live=True)
    fixture.items['a'].update(is_voucher_action=voucher, card_price='20.00', card_currency='RUB',
                              elastic=True, min_price='5.00', max_price='20.00', price_min_elastic='20.00', price_max_elastic='5.00',
                              currency_binding_fields=['action_price'])
    return fixture


def test_unknown_removal_never_sends(tmp_path):
    async def run():
        fixture = fixture_with_card(tmp_path, None)
        with pytest.raises(WriteError) as error:
            await fixture.service.preview(Op.ACTION_REMOVE, {'action_id': '3'}, [{'id': 'a', 'after': None}])
        assert error.value.code == 'ACTION_TYPE_UNKNOWN'
        assert fixture.calls == 0
    asyncio.run(run())


@pytest.mark.parametrize('card_known', [False, True])
def test_boost_v2_id_only_removal_and_card_readback(tmp_path, card_known):
    async def run():
        fixture = fixture_with_card(tmp_path)
        fixture.items['a']['currency'] = None
        if not card_known:
            fixture.items['a'].update(card_price=None, card_currency=None)
        plan = await fixture.service.preview(Op.ACTION_REMOVE, {'action_id': '3'}, [{'id': 'a', 'after': None}])
        assert plan['valid'] and plan['api_path'] == '/v2/actions/products/deactivate'
        assert bool(plan['effects'][0].get('card_unchanged')) == card_known
        result = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        assert result['status'] == 'VERIFIED_SUCCESS' and fixture.calls == 1
    asyncio.run(run())


def test_boost_removal_changed_card_not_success(tmp_path):
    async def run():
        fixture = fixture_with_card(tmp_path)
        original_writer = fixture.writer
        def writer(credentials):
            delegate = original_writer(credentials)
            class Writer:
                async def send(self, op, body):
                    result = await delegate.send(op, body)
                    fixture.items['a']['card_price'] = '19.00'
                    return result
                async def close(self):
                    await delegate.close()
            return Writer()
        fixture.service.writer_factory = writer
        plan = await fixture.service.preview(Op.ACTION_REMOVE, {'action_id': '3'}, [{'id': 'a', 'after': None}])
        result = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        assert result['status'] == 'DISCREPANCY' and fixture.calls == 1
    asyncio.run(run())


def test_voucher_and_auto_id_only_removal_no_currency_needed(tmp_path):
    async def run():
        for op, scope in [(Op.ACTION_REMOVE, {'action_id': '3'}),
                          (Op.AUTO_REMOVE, {'action_id': '3', 'auto_add_date': '2026-10-14T21:00:00Z'})]:
            fixture = fixture_with_card(tmp_path / op.name, True)
            fixture.items['a']['currency'] = None
            fixture.items['a']['member'] = True
            plan = await fixture.service.preview(op, scope, [{'id': 'a', 'after': None}])
            assert plan['valid'] and plan['effects'][0]['card_price'] is None
            result = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
            assert result['status'] == 'VERIFIED_SUCCESS'
            assert fixture.calls == 1
    asyncio.run(run())


@pytest.mark.parametrize('card_matches', [False, True])
def test_card_effect_immutable_and_readback_required(tmp_path, card_matches):
    async def run():
        fixture = fixture_with_card(tmp_path)
        original_writer = fixture.writer
        def writer(credentials):
            delegate = original_writer(credentials)
            class Writer:
                async def send(self, op, body):
                    result = await delegate.send(op, body)
                    if card_matches:
                        fixture.items['a']['card_price'] = body['products'][0]['action_price']['amount']
                    return result
                async def close(self):
                    await delegate.close()
            return Writer()
        fixture.service.writer_factory = writer
        plan = await fixture.service.preview(Op.ACTION_UPDATE, {'action_id': '3'}, [{'id': 'a', 'after': '12'}])
        assert plan['valid']
        effect = plan['effects'][0]
        assert effect['card_price'] == {'before': '20.00', 'after': '12.00', 'currency': 'RUB',
                                      'application': 'possible', 'accepted_values': ['20.00', '12.00']}
        assert effect['currency_binding_fields'] == ['action_price']
        effect['card_price']['after'] = '999.00'
        document = json.loads(fixture.service._plans[plan['id']]['document'])
        assert document['effects'][0]['card_price']['after'] == '12.00'
        result = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        assert result['status'] == 'VERIFIED_SUCCESS'
        assert result['rows'][0]['card_changed'] is card_matches
        assert result['rows'][0]['card_actual'] == ('12.00' if card_matches else '20.00')
        assert fixture.calls == 1
        assert 'полный атомарный откат' in fixture.service._rollback_reason(plan['id'])
    asyncio.run(run())


def test_changed_card_snapshot_blocks_before_send(tmp_path):
    async def run():
        fixture = fixture_with_card(tmp_path)
        plan = await fixture.service.preview(Op.ACTION_UPDATE, {'action_id': '3'}, [{'id': 'a', 'after': '12'}])
        fixture.items['a']['card_price'] = '21.00'
        with pytest.raises(WriteError) as error:
            await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        assert error.value.code == 'FRESH_CHANGED' and fixture.calls == 0
    asyncio.run(run())


@pytest.mark.parametrize('card_price,card_currency', [('19.00', 'RUB'), (None, 'RUB'), ('12.00', None), ('12.00', 'USD')])
def test_possible_card_effect_rejects_unrelated_or_unconfirmed_readback(tmp_path, card_price, card_currency):
    async def run():
        fixture = fixture_with_card(tmp_path)
        original_writer = fixture.writer
        def writer(credentials):
            delegate = original_writer(credentials)
            class Writer:
                async def send(self, op, body):
                    result = await delegate.send(op, body)
                    fixture.items['a'].update(card_price=card_price, card_currency=card_currency)
                    return result
                async def close(self):
                    await delegate.close()
            return Writer()
        fixture.service.writer_factory = writer
        plan = await fixture.service.preview(Op.ACTION_UPDATE, {'action_id': '3'}, [{'id': 'a', 'after': '12'}])
        result = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        assert result['status'] == 'DISCREPANCY' and fixture.calls == 1
    asyncio.run(run())


def test_scheduled_member_auto_update_remains_blocked(tmp_path):
    async def run():
        fixture = fixture_with_card(tmp_path)
        fixture.items['a'].update(action_price='10.00', money_currencies={'price':'RUB', 'action_price':'RUB'})
        with pytest.raises(WriteError) as error:
            await fixture.service.preview(Op.AUTO_UPDATE,
                {'action_id': '3', 'auto_add_date': '2026-10-14T21:00:00Z'}, [{'id': 'a', 'after': '12'}])
        assert error.value.code == 'AUTO_RETURN_ONLY' and fixture.calls == 0
    asyncio.run(run())


@pytest.mark.parametrize('after,missing', [('21', False), ('12', True)])
def test_boost_upper_limit_or_missing_bounds_never_send(tmp_path, after, missing):
    async def run():
        fixture = fixture_with_card(tmp_path)
        if missing:
            fixture.items['a']['max_price'] = None
        with pytest.raises(WriteError):
            await fixture.service.preview(Op.ACTION_UPDATE, {'action_id': '3'}, [{'id': 'a', 'after': after}])
        assert fixture.calls == 0
    asyncio.run(run())


def test_update_response_reports_removal_as_failure():
    row = {'source': {'product_id': '111'}}
    assert MutationService._accepted(Op.ACTION_UPDATE,
        {'active_product_ids': ['111'], 'deactivated_product_ids': ['111']}, row) is False


@pytest.mark.parametrize('op,member,after', [(Op.ACTION_REMOVE, True, None),
    (Op.AUTO_REMOVE, True, None), (Op.ACTION_UPDATE, False, '9'), (Op.ACTION_UPDATE, True, '11')])
def test_filled_freeze_date_rejects_list_changes_and_raises(tmp_path, op, member, after):
    async def run():
        fixture = fixture_with_card(tmp_path, True)
        fixture.items['a'].update(member=member, freeze_date='2026-12-01T00:00:00Z')
        scope = {'action_id': '3'}
        if op is Op.AUTO_REMOVE:
            scope['auto_add_date'] = '2026-10-14T21:00:00Z'
        with pytest.raises(WriteError) as error:
            await fixture.service.preview(op, scope, [{'id': 'a', 'after': after}])
        assert error.value.code == 'ACTION_FROZEN' and fixture.calls == 0
    asyncio.run(run())


def test_filled_future_freeze_date_allows_member_price_reduction(tmp_path):
    async def run():
        fixture = fixture_with_card(tmp_path, True)
        fixture.items['a']['freeze_date'] = '2026-12-01T00:00:00Z'
        plan = await fixture.service.preview(Op.ACTION_UPDATE, {'action_id': '3'}, [{'id': 'a', 'after': '9'}])
        assert plan['valid']
        result = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        assert result['status'] == 'VERIFIED_SUCCESS' and fixture.calls == 1
    asyncio.run(run())
