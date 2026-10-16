from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import Store
from app.operation_history_views import event_view


def test_statistics_busy_protects_shutdown(tmp_path):
    app=create_app(Store(tmp_path/'mock.sqlite3',root=tmp_path))
    reader=app.state.performance_statistics
    import asyncio
    asyncio.run(reader.lock.acquire())
    app.state.lifecycle_exit=lambda:None
    headers={'Origin':'http://127.0.0.1:8000','X-Lifecycle-Token':app.state.lifecycle_token}
    try:
        with TestClient(app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',123)) as api:
            assert api.post('/api/lifecycle/shutdown',json={},headers=headers).status_code==409
    finally:reader.lock.release()


def test_finance_generation_respects_shop_unknown(tmp_path):
    app=create_app(Store(tmp_path/'mock.sqlite3',root=tmp_path))
    app.state.mutations._recovered.append({'id':str(uuid4()),'status':'UNKNOWN_RESULT'})
    with TestClient(app,base_url='http://localhost:8000') as api:
        response=api.post('/api/finance/reports',json={'kind':'mutual','month':'2026-09'})
        assert response.status_code==409
        assert app.state.finance_report_jobs.history()==[]


def test_unified_history_preserves_family_reason():
    event={'id':str(uuid4()),'operation':'PRICE_UPDATE','status':'REJECTED','items':[{'offer_id':'SYNTHETIC','errors':[{'reason':'DiscountPercent must be greater or equal than 5, but actual is 2'}]}]}
    view=event_view(event,'warehouse')
    assert view['rows'][0]['errors'][0]['reason'].endswith('actual is 2')
    assert 'Скидка' in view['rows'][0]['errors'][0]['reason_ru']
    assert view['can_rollback'] is False
