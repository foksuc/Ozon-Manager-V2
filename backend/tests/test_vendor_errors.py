import asyncio
from copy import deepcopy
from app.vendor_errors import normalize_vendor_error, translate_vendor_reason
from app.ozon_write import WriteOperation as Op
from test_mutations import Fixture


def test_discount_reason_translated_without_losing_original():
    reason = 'product ID 111: DiscountPercent must be greater or equal than 4.1622, but actual is 2.7027'
    value = normalize_vendor_error({'reason': reason})
    assert value['reason'] == reason
    assert value['reason_ru'] == value['message'] == 'Скидка должна быть не меньше 4.1622%; фактическая скидка — 2.7027%.'


def test_nested_reason_allowlist_bounds_and_redaction():
    value = normalize_vendor_error({'code': 'API', 'reason': {'message': 'synthetic-key 1', 'headers': 'secret', 'errors': ['x' * 3000] * 20}}, secrets=['synthetic-key'])
    assert 'synthetic-key' not in value['reason'] and 'headers' not in value['reason']
    assert len(value['reason']) <= 2000
    assert 'точного перевода' in normalize_vendor_error({'reason':'UNRECOGNIZED'})['reason_ru']
    assert translate_vendor_reason('Product not found') == 'Товар не найден.'
    assert normalize_vendor_error({'code':'NOT_FOUND', 'error':'Product not found'})['reason_ru'] == 'Товар не найден.'


def test_selected_reason_history_and_reconcile_no_resend(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        fixture.apply = False
        def writer(credentials):
            class Writer:
                async def send(self, op, body):
                    fixture.calls += 1
                    return {'rejected': [{'product_id':'111', 'reason':'Product not found'}, {'product_id':'222', 'reason':'Foreign target'}]}
                async def close(self):
                    pass
            return Writer()
        fixture.service.writer_factory = writer
        original_fresh = fixture.fresh
        async def fresh(op, scope, ids):
            if fixture.calls:
                received = fixture.service.journal.entries()[0]
                assert received['rows'][0]['errors'][0]['reason'] == 'Product not found'
                assert received['status'] == 'UNKNOWN_RESULT' and received['rows'][0]['accepted'] is False
            return await original_fresh(op, scope, ids)
        fixture.service.fresh = fresh
        plan = await fixture.service.preview(Op.ACTION_UPDATE, {'action_id':'3'}, [{'id':'a','after':'12'}])
        result = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        error = deepcopy(result['rows'][0]['errors'][0])
        assert result['status'] == 'REJECTED' and error['reason'] == 'Product not found'
        assert error['message'] == 'Товар не найден.' and len(result['rows'][0]['errors']) == 1
        result['rows'][0]['errors'][0]['reason'] = 'Tampered'
        assert fixture.service.history()[0]['rows'][0]['errors'][0]['reason'] == 'Product not found'
        await fixture.service.reconcile(plan['id'], plan['hash'])
        assert fixture.service.history()[0]['rows'][0]['errors'][0] == error and fixture.calls == 1
        from app.mutations import MutationService
        restored = MutationService(fixture.accounts, fixture.writer, fixture.fresh, root=tmp_path, path=tmp_path/'attempts')
        entry = restored.history()[0]
        assert entry['rows'][0]['errors'][0] == error and entry['can_rollback'] is False
    asyncio.run(run())


def test_journal_failure_prevents_network(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        class BrokenJournal:
            def upsert(self, event):
                raise OSError('synthetic storage failure')
        fixture.service.journal = BrokenJournal()
        plan = await fixture.service.preview(Op.PRICE, {}, [{'id':'a','after':'12'}])
        from app.ozon_write import WriteError
        import pytest
        with pytest.raises(WriteError) as failure:
            await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        assert failure.value.code == 'HISTORY_STORAGE' and fixture.calls == 0
    asyncio.run(run())


def test_selected_reason_survives_malformed_errors_and_redacts(tmp_path):
    fixture = Fixture(tmp_path, live=True)
    result = fixture.service._response_errors(Op.ACTION_UPDATE, {'rejected': [
        {'product_id': '111', 'errors': None, 'reason': 'synthetic-key Product not found'},
        {'product_id': '222', 'reason': 'foreign'}]}, {'source': {'product_id': '111'}})
    assert len(result) == 1 and 'synthetic-key' not in result[0]['reason']
    assert result[0]['reason_ru'] == 'Товар не найден.'


def test_unknown_restart_readonly_reconcile_never_resends(tmp_path):
    async def run():
        from app.mutations import MutationService
        from app.ozon_write import WriteError
        import pytest
        fixture = Fixture(tmp_path, live=True)
        fixture.send_timeout, fixture.apply = True, False
        plan = await fixture.service.preview(Op.PRICE, {}, [{'id':'a','after':'12'}])
        result = await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        assert result['status'] == 'UNKNOWN_RESULT'
        restored_context = []
        restored = MutationService(fixture.accounts, fixture.writer, fixture.fresh, root=tmp_path,
                                  path=tmp_path/'attempts', restore_context=restored_context.append)
        assert restored_context[0]['id'] == plan['id']
        assert restored.history()[0]['can_reconcile'] and '_reconciliation' not in restored.history()[0]
        assert 'synthetic-key' not in str(restored.journal.entries())
        fixture.accounts.revision += 10
        fixture.items['a']['current'] = '12.00'
        result = await restored.reconcile_history(plan['id'])
        assert result['status'] == 'VERIFIED_SUCCESS' and fixture.calls == 1
        assert restored.has_unresolved() is False and restored.history()[0]['can_rollback'] is False
        with pytest.raises(WriteError) as failure:
            await restored.execute(plan['id'], plan['hash'], confirmed=True)
        assert failure.value.code == 'ALREADY_ATTEMPTED' and fixture.calls == 1
    asyncio.run(run())


def test_recovered_snapshot_changed_account_digest_blocks_read(tmp_path):
    async def run():
        from app.mutations import MutationService
        from app.ozon_write import WriteError
        from app.connection import Credentials
        import pytest
        fixture = Fixture(tmp_path, live=True)
        fixture.send_timeout, fixture.apply = True, False
        plan = await fixture.service.preview(Op.PRICE, {}, [{'id':'a','after':'12'}])
        await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        restored = MutationService(fixture.accounts, fixture.writer, fixture.fresh, root=tmp_path, path=tmp_path/'attempts')
        fixture.accounts._accounts[fixture.accounts.active_id]['credentials'] = Credentials('2', 'synthetic-key')
        reads = fixture.reads
        with pytest.raises(WriteError) as failure:
            await restored.reconcile_history(plan['id'])
        assert failure.value.code == 'RECONCILIATION_BLOCKED' and fixture.reads == reads and fixture.calls == 1
    asyncio.run(run())


def test_tampered_reconciliation_hash_fails_closed(tmp_path):
    async def run():
        from app.mutations import MutationService
        from app.ozon_write import WriteError
        import pytest
        fixture = Fixture(tmp_path)
        plan = await fixture.service.preview(Op.PRICE, {}, [{'id':'a','after':'12'}])
        await fixture.service.execute(plan['id'], plan['hash'], confirmed=True)
        events = fixture.service.journal.entries()
        events[0]['_reconciliation']['document']['rows'][0]['after'] = '999.00'
        class BadJournal:
            def entries(self):
                return events
        with pytest.raises(WriteError) as failure:
            MutationService(fixture.accounts, fixture.writer, fixture.fresh, root=tmp_path, journal=BadJournal())
        assert failure.value.code == 'HISTORY_STORAGE' and fixture.calls == 1
    asyncio.run(run())
