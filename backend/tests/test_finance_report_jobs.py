import asyncio
import base64
import json
import pytest
from app.finance import ReportQuery
from app.finance_report_jobs import FinanceReportJobs
from app.operation_history import EncryptedHistory, HistoryError
from app.ozon_read import FinanceReport, ReadError


def synthetic_cipher(raw, *, decrypt=False):
    # Test transport only; production uses Windows DPAPI.
    return bytes(value ^ 73 for value in base64.b64decode(raw)) if decrypt else base64.b64encode(bytes(value ^ 73 for value in raw))


def journal(root):
    return EncryptedHistory(root, 'finance-report-test', cipher=synthetic_cipher)


QUERY = ReportQuery(kind='compensation', month='2026-09')


def test_unknown_restart_blocks_same_and_other_job_without_second_send(tmp_path):
    calls = []
    async def creator(kind, month):
        calls.append((kind, month))
        raise ReadError('UNKNOWN_REPORT_RESULT', 'Синтетический разрыв')
    first = FinanceReportJobs(journal(tmp_path))
    with pytest.raises(ReadError): asyncio.run(first.create(QUERY, 'account-one', creator, mode='LIVE_READ'))
    restored = FinanceReportJobs(journal(tmp_path))
    for query in (QUERY, ReportQuery(kind='mutual', month='2026-08')):
        with pytest.raises(ReadError, match='Сначала сверьте'):
            asyncio.run(restored.create(query, 'account-one', creator, mode='LIVE_READ'))
    assert calls == [(FinanceReport.COMPENSATION, '2026-09')]
    assert restored.has_unresolved('account-one') and not restored.has_unresolved('account-two')


def test_completed_restart_reuses_code_and_keeps_history_encrypted(tmp_path):
    calls = []
    async def creator(kind, month):
        calls.append((kind, month))
        return {'result': {'code': 'SYNTHETIC_REPORT_123'}}
    first = FinanceReportJobs(journal(tmp_path))
    result = asyncio.run(first.create(QUERY, 'account-one', creator, mode='LIVE_READ'))
    restored = FinanceReportJobs(journal(tmp_path))
    repeated = asyncio.run(restored.create(QUERY, 'account-one', creator, mode='LIVE_READ'))
    assert not result['reused'] and repeated['reused'] and len(calls) == 1
    assert result['code'] == repeated['code']
    assert restored.history('account-two') == []
    data = journal(tmp_path).path.read_bytes()
    assert b'SYNTHETIC_REPORT_123' not in data and b'account-one' not in data
    entries = restored.history('account-one')
    entries[0]['code'] = 'changed'
    assert restored.history('account-one')[0]['code'] == result['code']


def test_definite_rejection_is_recorded_and_only_explicit_next_call_retries(tmp_path):
    calls = []
    async def creator(kind, month):
        calls.append(month)
        if len(calls) == 1:
            raise ReadError('READ_REJECTED', 'Ozon отказал', vendor_error={'reason': 'Access denied', 'headers': 'NEVER_STORED'})
        return {'result': {'code': 'REPORT_OK'}}
    jobs = FinanceReportJobs(journal(tmp_path))
    with pytest.raises(ReadError): asyncio.run(jobs.create(QUERY, 'account-one', creator, mode='LIVE_READ'))
    assert len(calls) == 1 and jobs.history()[0]['status'] == 'REJECTED'
    error = jobs.history()[0]['errors'][0]
    assert error['reason'] == 'Access denied' and error['reason_ru'] == 'Доступ к операции запрещён.'
    assert 'NEVER_STORED' not in json.dumps(jobs.history())
    restored = FinanceReportJobs(journal(tmp_path))
    assert asyncio.run(restored.create(QUERY, 'account-one', creator, mode='LIVE_READ'))['code'] == 'REPORT_OK'
    assert len(calls) == 2


@pytest.mark.parametrize('response', [None, {}, {'result': {}}, {'result': {'code': '../invalid'}}, {'result': {'code': 123}}])
def test_invalid_success_response_remains_unknown(tmp_path, response):
    async def creator(*args): return response
    jobs = FinanceReportJobs(journal(tmp_path))
    with pytest.raises(ReadError): asyncio.run(jobs.create(QUERY, 'account-one', creator, mode='LIVE_READ'))
    assert FinanceReportJobs(journal(tmp_path)).has_unresolved('account-one')


def test_failed_history_write_forbids_network():
    class FailedJournal:
        def entries(self): return []
        def upsert(self, event): raise HistoryError('Синтетический отказ диска')
    calls = []
    async def creator(*args): calls.append(args)
    with pytest.raises(HistoryError):
        asyncio.run(FinanceReportJobs(FailedJournal()).create(QUERY, 'account-one', creator, mode='LIVE_READ'))
    assert calls == []


def test_concurrent_identical_jobs_send_once():
    async def scenario():
        jobs = FinanceReportJobs()
        calls = []
        async def creator(*args):
            calls.append(args)
            await asyncio.sleep(0)
            assert jobs.lock.locked()
            return {'result': {'code': 'REPORT_OK'}}
        results = await asyncio.gather(*(jobs.create(QUERY, 'account-one', creator, mode='MOCK') for _ in range(2)))
        assert len(calls) == 1 and sorted(result['reused'] for result in results) == [False, True]
    asyncio.run(scenario())


def test_other_account_does_not_reuse_or_reveal_report():
    calls = []
    async def creator(*args):
        calls.append(args)
        return {'result': {'code': 'REPORT_' + str(len(calls))}}
    jobs = FinanceReportJobs()
    first = asyncio.run(jobs.create(QUERY, 'account-one', creator, mode='MOCK'))
    second = asyncio.run(jobs.create(QUERY, 'account-two', creator, mode='MOCK'))
    assert first['code'] != second['code'] and len(calls) == 2
    assert len(jobs.history('account-one')) == len(jobs.history('account-two')) == 1
