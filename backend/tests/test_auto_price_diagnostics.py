"""Price diagnostics are not fabricated vendor rejections; no LIVE transport."""
import asyncio
import json
import pytest
from app.ozon_write import WriteOperation as Op
from test_mutations import Fixture

SCOPE = {'action_id': '3', 'auto_add_date': '2026-10-20T00:00:00Z'}


@pytest.mark.parametrize('field', ['below_min_price', 'extremely_low_price', 'failed_price'])
@pytest.mark.parametrize('accepted', [True, False, None])
def test_diagnostic_preserves_vendor_pair_and_exact_readback(tmp_path, field, accepted):
    async def run():
        fixture = Fixture(tmp_path)
        delegate_factory = fixture.writer
        def writer(credentials):
            delegate = delegate_factory(credentials)
            class Writer:
                async def send(self, op, body):
                    response = await delegate.send(op, body)
                    response[field] = [{'key': 111, 'value': 12.5}, {'key': 222, 'value': 99}]
                    if accepted is False:
                        response['rejected'] = [{'product_id': 111, 'reason': 'Product not found'}]
                    elif accepted is None:
                        response.pop('product_ids')
                    return response
                async def close(self):
                    await delegate.close()
            return Writer()
        fixture.service.writer_factory = writer
        plan = await fixture.service.preview(Op.AUTO_UPDATE, SCOPE, [{'id': 'a', 'after': '12'}])
        result = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        row = result['rows'][0]
        assert result['status'] == ('DISCREPANCY' if accepted is False else 'VERIFIED_SUCCESS')
        assert row['accepted'] is accepted and fixture.calls == 1
        diagnostic = next(error for error in row['errors'] if error['code'] == field.upper())
        assert json.loads(diagnostic['reason']) == {'key': 111, 'value': 12.5}
        assert diagnostic['severity'] == 'warning' and 'точного перевода' not in diagnostic['reason_ru']
        if accepted is False:
            assert row['errors'][0]['reason'] == 'Product not found'
    asyncio.run(run())


def test_auto_update_readback_lag_retries_reads_only(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        original_fresh = fixture.fresh
        lag_reads = 0
        async def fresh(op, scope, ids):
            nonlocal lag_reads
            result = await original_fresh(op, scope, ids)
            if fixture.calls and lag_reads < 2:
                lag_reads += 1
                result['items'][0]['current'] = '10.00'
            return result
        async def no_wait(delay):
            pass
        fixture.service.fresh = fresh
        fixture.service.readback_sleep = no_wait
        plan = await fixture.service.preview(Op.AUTO_UPDATE, SCOPE, [{'id': 'a', 'after': '12'}])
        result = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        assert result['status'] == 'VERIFIED_SUCCESS' and fixture.calls == 1 and lag_reads == 2
    asyncio.run(run())


def test_legacy_fabricated_price_rejection_reconciles_without_acceptance_claim(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        plan = await fixture.service.preview(Op.AUTO_UPDATE, SCOPE, [{'id': 'a', 'after': '12'}])
        await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        fixture.service._history[plan['id']]['rows'][0].update(accepted=False, errors=[{
            'code': 'BELOW_MIN_PRICE', 'reason': 'Ozon сообщил проблему проверки цены; проверьте построчную сверку'}])
        result = await fixture.service.reconcile(plan['id'], plan['hash'])
        assert result['status'] == 'VERIFIED_SUCCESS'
        assert result['rows'][0]['accepted'] is None and fixture.calls == 1
        diagnostic=result['rows'][0]['errors'][0]
        assert diagnostic['severity']=='warning' and diagnostic['original_unavailable'] is True
        assert 'точного перевода' not in diagnostic['reason_ru']
    asyncio.run(run())
