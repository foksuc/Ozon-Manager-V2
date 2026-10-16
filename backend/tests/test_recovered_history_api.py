"""Restart-to-readback API integration; synthetic account/catalog/transport only."""
from copy import deepcopy
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.accounts import AccountStore
from app.connection import Connection
from app.main import create_app
from app.storage import Store
from test_accounts import cipher


def test_recovered_history_api_restores_product_alias_and_never_resends(tmp_path, monkeypatch):
    monkeypatch.setattr('app.ozon_write._utc_now', lambda: datetime(2026, 10, 13, tzinfo=timezone.utc))
    accounts = AccountStore('LIVE_READ', root=tmp_path, path=tmp_path/'accounts.bin', cipher=cipher)
    accounts.add('Synthetic', '1', 'synthetic-key')
    monkeypatch.setattr('app.main.AccountStore', lambda *args, **kwargs: accounts)
    state = {'sends': 0, 'readback_unavailable': False, 'removed': False, 'reads': []}
    row = dict(id='member-row', action_id='10', offer_id='SYNTHETIC', name='Synthetic',
               product_id='101', sku='201', role='member', price='3700.00', action_price='3546.00',
               min_price=None, max_price=None, elastic=True, auto_add_at=None, currency='RUB',
               currency_assumed=False, money_currencies={'price':'RUB', 'action_price':None},
               is_voucher_action=False, freeze_date=None, card_price='3700.00', card_currency='RUB')

    class Reader:
        async def read(self, *args, **kwargs):
            raise AssertionError('No network reads allowed in synthetic test')
        async def close(self):
            pass

    class Catalogue:
        async def catalog(self, **scope):
            state['reads'].append(deepcopy(scope))
            current = deepcopy(row)
            if state['removed']:
                current.update(id='candidate-row-after-restart', role='candidate', action_price=None)
            return {'mode':'LIVE_READ', 'fetched_at':'2026-10-13T00:00:00Z',
                    'complete':not state['readback_unavailable'], 'warnings':[], 'actions':[], 'stocks':[],
                    'products':[current] if scope.get('role') == current['role'] else []}

    class Writer:
        def __init__(self, credentials):
            pass
        async def send(self, operation, body):
            state['sends'] += 1
            assert body == {'action_id':10, 'product_ids':['101']}
            state.update(readback_unavailable=True, removed=True)
            raise TimeoutError('Synthetic unknown send result')
        async def close(self):
            pass

    monkeypatch.setattr('app.operation_routes.WriteClient', Writer)
    def application():
        app = create_app(Store(tmp_path/'synthetic.sqlite3', root=tmp_path),
                         connection=Connection('LIVE_READ'), read_client=Reader())
        app.state.live_catalog = Catalogue()
        return app

    first = application()
    with TestClient(first, base_url='http://localhost') as api:
        response = api.post('/api/mutations/preview', json={'operation':'action_remove', 'request':{
            'kind':'prices', 'ids':['member-row'], 'action_id':'10', 'role':'member', 'auto_add':False,
            'auto_add_date':None, 'mode':'manual', 'value':'0', 'rounding':'1', 'scenario':'ready'}})
        assert response.status_code == 200, response.text
        plan = response.json()
        result = api.post('/api/mutations/execute', json={'plan_id':plan['id'], 'plan_hash':plan['hash'], 'confirmed':True})
        assert result.status_code == 200, result.text
        assert result.json()['status'] == 'UNKNOWN_RESULT' and state['sends'] == 1

    state['readback_unavailable'] = False
    accounts.revision += 10  # A restarted process need not keep an in-memory revision.
    restarted = application()
    with TestClient(restarted, base_url='http://localhost') as api:
        history = api.get('/api/operations/history').json()['items']
        assert history[0]['id'] == plan['id'] and history[0]['can_reconcile'] is True
        assert '_reconciliation' not in str(history) and 'document' not in str(history)
        assert 'synthetic-key' not in str(history)
        result = api.post('/api/mutations/reconcile-history', json={'operation_id':plan['id']})
        assert result.status_code == 200, result.text
        assert result.json()['status'] == 'VERIFIED_SUCCESS' and state['sends'] == 1
        assert result.json()['rows'][0]['actual_member'] is False
        assert result.json()['rows'][0]['card_changed'] is False
        assert any(scope.get('role') == 'candidate' and scope.get('action_id') == '10' for scope in state['reads'])
        result = api.post('/api/mutations/execute', json={'plan_id':plan['id'], 'plan_hash':plan['hash'], 'confirmed':True})
        assert result.status_code == 409 and result.json()['code'] == 'ALREADY_ATTEMPTED'
        assert state['sends'] == 1
