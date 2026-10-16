"""Single-send financial report jobs with encrypted restart-safe outcomes."""
import asyncio
from copy import deepcopy
import re
from uuid import UUID, uuid4
from .catalog import timestamp
from .finance_contracts import report_request
from .operation_history import HistoryError
from .ozon_read import FinanceReport, ReadError
from .vendor_errors import normalize_vendor_error

KINDS = {'mutual': FinanceReport.MUTUAL, 'compensation': FinanceReport.COMPENSATION,
         'decompensation': FinanceReport.DECOMPENSATION}
DEFINITE_REJECTIONS = {'READ_REJECTED', 'AUTHORIZATION', 'NOT_CONFIGURED', 'INVALID_READ_REQUEST'}
CODE = re.compile(r'[A-Za-z0-9_-]{1,512}')


class FinanceReportJobs:
    def __init__(self, journal=None):
        self.journal = journal
        self.lock = asyncio.Lock()
        self._events = {}
        for event in journal.entries() if journal is not None else []:
            try:
                if event.get('operation') != 'finance_report': raise ValueError()
                if str(UUID(event['id'])) != event['id']: raise ValueError()
                if not isinstance(event['account_id'], str) or not event['account_id']: raise ValueError()
                if event['kind'] not in KINDS: raise ValueError()
                report_request(event['month'])
                if event['status'] not in {'UNKNOWN_REPORT_RESULT', 'REJECTED', 'VERIFIED_SUCCESS'}: raise ValueError()
                if event['status'] == 'VERIFIED_SUCCESS' and not CODE.fullmatch(event.get('code', '')): raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise HistoryError('История финансовых отчётов повреждена; создание запрещено') from None
            self._events[event['id']] = deepcopy(event)

    def _save(self, event):
        if self.journal is not None: self.journal.upsert(event)
        self._events[event['id']] = deepcopy(event)

    def history(self, account_id=None):
        return deepcopy([event for event in reversed(list(self._events.values()))
                         if account_id is None or event['account_id'] == account_id])

    def has_unresolved(self, account_id=None):
        return any(event['status'] == 'UNKNOWN_REPORT_RESULT' for event in self.history(account_id))

    async def create(self, query, account_id, creator, *, mode):
        if query.kind not in KINDS: raise ValueError('Неизвестный финансовый отчёт')
        report_request(query.month)
        if not isinstance(account_id, str) or not account_id:
            raise ReadError('NOT_CONFIGURED', 'Выберите магазин для создания документа', 409)
        async with self.lock:
            if self.has_unresolved(account_id):
                raise ReadError('UNKNOWN_REPORT_RESULT', 'Сначала сверьте незавершённый финансовый документ в Seller; повторная отправка запрещена.', 409)
            completed = next((event for event in self.history(account_id)
                              if event['kind'] == query.kind and event['month'] == query.month
                              and event['status'] == 'VERIFIED_SUCCESS'), None)
            if completed:
                return {'code': completed['code'], 'mode': mode, 'reused': True,
                        'operation_id': completed['id'], 'status': completed['status']}
            if len(self._events) >= 1000: raise ReadError('REPORT_LIMIT', 'История содержит 1000 запросов документов; создание нового запрещено.', 409)
            event = {'id': str(uuid4()), 'operation': 'finance_report', 'account_id': account_id,
                     'kind': query.kind, 'month': query.month, 'mode': mode,
                     'created_at': timestamp(), 'status': 'UNKNOWN_REPORT_RESULT', 'errors': []}
            self._save(event)  # Durable and encrypted before any network call.
            try:
                response = await creator(KINDS[query.kind], query.month)
            except ReadError as exc:
                if exc.code in DEFINITE_REJECTIONS:
                    event['status'] = 'REJECTED'
                    event['errors'] = [normalize_vendor_error(exc.vendor_error or {'code': exc.code, 'reason': exc.message})]
                    self._save(event)
                raise
            except Exception:
                raise ReadError('UNKNOWN_REPORT_RESULT', 'Ответ создания документа не подтверждён. Сверьте документы Seller; повтор запрещён.', 409) from None
            envelope = response.get('result') if isinstance(response, dict) else None
            code = envelope.get('code') if isinstance(envelope, dict) else None
            if not isinstance(code, str) or not CODE.fullmatch(code):
                raise ReadError('UNKNOWN_REPORT_RESULT', 'Документ мог быть создан, но номер не подтверждён. Сверьте документы Seller.', 409)
            event.update(status='VERIFIED_SUCCESS', code=code)
            self._save(event)
            return {'code': code, 'mode': mode, 'reused': False,
                    'operation_id': event['id'], 'status': event['status']}
