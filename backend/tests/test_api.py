import socket
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import Store, plan_hash


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Store(tmp_path / "data/mock.sqlite3", root=tmp_path)), base_url="http://localhost:8000")


def price_request(**changes):
    body = dict(kind="prices", ids=["mock-product-1-1"], action_id="mock-action-1", mode="percent", value="5", rounding="0.01", scenario="ready")
    body.update(changes)
    return body


def test_catalog_no_network(client, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network must remain unused in MOCK")
    monkeypatch.setattr(socket, "create_connection", blocked)
    response = client.get("/api/catalog").json()
    assert response["mode"] == "MOCK"
    assert {a["status"] for a in response["actions"] if a["auto_add"]} == {"active", "upcoming", "ended"}
    assert len([a for a in response["actions"] if not a["auto_add"]]) >= 2
    for action in response["actions"]:
        assert {p["role"] for p in response["products"] if p["action_id"] == action["id"]} == {"member", "candidate"}
    assert client.get("/api/catalog?scenario=error").status_code == 503
    assert client.get("/api/catalog?scenario=empty").json()["stocks"] == []


def test_plan_server_binding_and_idempotence(client):
    plan = client.post("/api/preview", json=price_request()).json()
    assert plan["rows"][0]["before"] == "950.00"
    assert plan["rows"][0]["after"] == "950.00"
    snapshot = plan["source"]
    assert snapshot["mode"] == "MOCK" and snapshot["scenario"] == "ready"
    assert snapshot["action_id"] == "mock-action-1" and snapshot["fetched_at"]
    assert snapshot["action"]["name"] == "Автодобавление — Осень"
    assert snapshot["items"][0]["action_price"] == "950.00"
    assert snapshot["items"][0]["auto_add_at"] == "2026-10-01T00:00:00Z"
    changed = deepcopy(plan)
    changed["source"]["items"][0]["action_id"] = "mock-action-2"
    assert plan_hash(changed) != plan["hash"]
    body = dict(plan_id=plan["id"], plan_hash=plan["hash"])
    draft = client.post("/api/drafts", json=body)
    assert draft.status_code == 200
    assert client.post("/api/drafts", json=body).json() == draft.json()
    assert client.post("/api/drafts", json={**body, "plan_hash": "forged"}).status_code == 409
    assert client.post("/api/drafts", json={**body, "targets": []}).status_code == 422
    assert client.post("/api/drafts", json={**body, "value": "1"}).status_code == 422
    assert len(client.get("/api/drafts").json()["items"]) == 1
    original = client.app.state.store
    restarted = TestClient(create_app(Store(original.path, root=original.root)), base_url="http://localhost:8000")
    assert restarted.get("/api/drafts").json()["items"][0] == draft.json()


@pytest.mark.parametrize("changes", [{"ids": ["unknown"]}, {"ids": ["mock-product-1-1", "mock-product-1-1"]}, {"action_id": "mock-action-2"}, {"unexpected": 1}, {"value": 5}, {"ids": ["mock-product-1-1", "mock-product-2-1"]}])
def test_reject_forged_scope_and_dto(client, changes):
    assert client.post("/api/preview", json=price_request(**changes)).status_code == 422


def test_partial_and_mixed_errors_block_entire_draft(client):
    for changes in [{"scenario": "partial"}, {"ids": ["mock-product-1-1", "mock-product-1-3"], "mode": "manual", "value": "450"}]:
        plan = client.post("/api/preview", json=price_request(**changes)).json()
        assert not plan["valid"]
        assert client.post("/api/drafts", json={"plan_id": plan["id"], "plan_hash": plan["hash"]}).status_code == 409
    assert client.get("/api/drafts").json()["items"] == []


@pytest.mark.parametrize("value", ["799", "2201"])
def test_non_elastic_visible_money_bounds_enforced(client, value):
    plan = client.post("/api/preview", json=price_request(mode="manual", value=value)).json()
    assert not plan["valid"] and plan["rows"][0]["error"]
    assert client.post("/api/drafts", json={"plan_id": plan["id"], "plan_hash": plan["hash"]}).status_code == 409


def test_stock_preview(client):
    body = dict(kind="stocks", ids=["mock-stock-1"], mode="manual", value="0", rounding="0.01")
    plan = client.post("/api/preview", json=body).json()
    assert plan["valid"] and plan["rows"][0]["diff"] == "-10"
    assert plan["source"]["action_id"] is None
    assert plan["source"]["items"][0]["warehouse_id"] == "mock-warehouse-1"
    assert plan["source"]["items"][0]["reserved"] == 1
    assert plan["source"]["items"][0]["present"] == 11
    draft = client.post("/api/drafts", json={"plan_id": plan["id"], "plan_hash": plan["hash"]}).json()
    assert draft["plan"]["source"] == plan["source"]
    assert not client.post("/api/preview", json={**body, "value": "-1"}).json()["valid"]
    assert client.post("/api/preview", json={**body, "action_id": "mock-action-1"}).status_code == 422


@pytest.mark.parametrize("missing", ["min_price", "max_price"])
def test_missing_elastic_bounds_block_row_and_entire_save(client, monkeypatch, missing):
    from app.catalog import catalog as original_catalog

    def incomplete_catalog(scenario):
        source = original_catalog(scenario)
        selected = next(product for product in source["products"] if product["id"] == "mock-product-1-3")
        selected[missing] = None
        return source

    monkeypatch.setattr("app.main.catalog", incomplete_catalog)
    response = client.post("/api/preview", json=price_request(ids=["mock-product-1-1", "mock-product-1-3"], mode="manual", value="900"))
    assert response.status_code == 200
    plan = response.json()
    assert not plan["valid"] and plan["complete"]
    assert plan["rows"][0]["after"] == "900.00"
    assert plan["rows"][1]["after"] is None
    assert "Elastic" in plan["rows"][1]["error"]
    assert plan["source"]["items"][1][missing] is None
    assert client.post("/api/drafts", json={"plan_id": plan["id"], "plan_hash": plan["hash"]}).status_code == 409
    assert client.get("/api/drafts").json()["items"] == []


def test_local_boundary(client):
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
    assert client.post("/api/preview", json=price_request(), headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/preview", json=price_request(), headers={"origin": "null"}).status_code == 403
    assert client.get("/api/health", headers={"origin": "http://localhost:5173"}).status_code == 200
    assert client.get("/api/execute").status_code == 404
    assert client.post("/api/execute", json={}).status_code in (404, 405)
    assert client.get("/data/mock.sqlite3").status_code == 404
    assert client.get("/.env").status_code == 404
