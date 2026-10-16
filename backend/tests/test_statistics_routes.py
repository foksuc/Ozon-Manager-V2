from fastapi.testclient import TestClient
from app.main import create_app
from app.connection import Connection
from app.storage import Store

def test_statistics_routes_closed_mock(tmp_path):
    with TestClient(create_app(connection=Connection('MOCK'),store=Store(tmp_path/'synthetic.sqlite3')),base_url='http://127.0.0.1') as client:
        methods=client.get('/api/statistics/methods').json()
        assert all(m['source']=='performance' for m in methods)
        assert len([m for m in methods if m['source']=='performance'])==22
        result=client.post('/api/statistics/performance',json={'operation':'campaigns'})
        assert result.status_code==200 and result.json()['data']['list'][0]['id']=='101'
        assert client.post('/api/statistics/statistics',json={'operation':'stock','skus':['1000001']}).status_code in (404,405)
        assert client.post('/api/statistics/performance',json={'operation':'activate'}).status_code==422
        assert client.post('/api/statistics/performance',json={'operation':'all_orders','date_from':'2026-10-01','date_to':'2026-10-02'}).status_code==422
        assert client.post('/api/statistics/performance',json={'operation':'all_orders','date_from':'2026-10-01','date_to':'2026-10-02','confirmed':True}).status_code==200
        assert client.post('/api/statistics/performance',json={'operation':'campaigns'},headers={'origin':'https://untrusted.invalid'}).status_code==403
