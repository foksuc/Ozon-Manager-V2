"""One-off diagnostic/recovery endpoints must not ship in the regular app."""
from fastapi.testclient import TestClient
from test_action_currency_guards import isolated_api


def test_diagnostic_routes_absent_and_cannot_acknowledge_lost_plan(tmp_path,monkeypatch):
    app,_,_=isolated_api(tmp_path,monkeypatch)
    service=app.state.mutations
    service._recovered=[{'id':'00000000-0000-4000-8000-000000000099','status':'UNKNOWN_RESULT','recovered':True}]
    with TestClient(app) as api:
        for path in ('/api/mutations/recovered-review','/api/promotion-probe/preview','/api/promotion-probe/execute'):
            assert api.post(path,json={}).status_code==404
        assert service.has_unresolved()
