"""Synthetic transport proofs; never authenticated Seller acceptance."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
import httpx
import pytest
from app.connection import Credentials, Connection, load_connection, parse_credentials
from app.ozon_read import ReadClient, ReadError, ReadOperation, validate_request, retry_delay
from app.main import create_app
from app.storage import Store
from fastapi.testclient import TestClient

SYNTHETIC = Credentials("10001", "SYNTHETIC-TRANSPORT-TEST-KEY")


def run_read(handler, operation=ReadOperation.ACTIONS, payload=None):
    async def run():
        client = ReadClient(SYNTHETIC, transport=httpx.MockTransport(handler))
        try:
            return await client.read(operation, payload)
        finally:
            await client.close()
    return asyncio.run(run())


@pytest.mark.parametrize("operation", ["/v2/products/stocks", "/v1/actions/products/update", "/v1/actions/products/activate", "/v2/actions/products/deactivate", "/v2/actions/auto-add/products/delete", "/v2/actions/auto-add/products/update", "https://evil.invalid/v1/actions", "ACTIONS", None])
def test_mutation_and_arbitrary_operations_never_send(operation):
    calls = []
    with pytest.raises(ReadError) as err:
        run_read(lambda request: calls.append(request), operation, {})
    assert err.value.code == "READ_ONLY" and calls == []


def test_fixed_https_host_headers_and_decimal_lossless():
    def reply(request):
        assert str(request.url) == "https://api-seller.ozon.ru/v1/actions"
        assert request.method == "GET"
        assert request.headers["Client-Id"] == "10001"
        assert request.headers["Api-Key"] == SYNTHETIC.api_key
        return httpx.Response(200, content=b'{"id":9223372036854775807,"price":123456789012.99}')
    result = run_read(reply)
    assert result["id"] == 9223372036854775807
    assert result["price"] == Decimal("123456789012.99")


def test_redirect_does_not_forward_secret():
    calls = []
    def reply(request):
        calls.append(request)
        return httpx.Response(307, headers={"Location": "https://evil.invalid/collect"})
    with pytest.raises(ReadError) as err:
        run_read(reply)
    assert err.value.code == "REDIRECT_BLOCKED" and len(calls) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
def test_rejected_read_not_retried_or_raw_error_exposed(status):
    calls = []
    def reply(request):
        calls.append(request)
        return httpx.Response(status, json={"message": SYNTHETIC.api_key + " personal-data"})
    with pytest.raises(ReadError) as err:
        run_read(reply)
    assert len(calls) == 1
    assert SYNTHETIC.api_key not in str(err.value) and "personal-data" not in str(err.value)


def test_read_retry_429_500_success():
    calls = []
    def reply(request):
        calls.append(request)
        if len(calls) <= 2:
            return httpx.Response(429 if len(calls) == 1 else 500, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"result": []})
    assert run_read(reply) == {"result": []}
    assert len(calls) == 3


def test_long_retry_after_is_not_shortened_or_retried():
    calls = []
    def reply(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"})
    with pytest.raises(ReadError):
        run_read(reply)
    assert len(calls) == 1


def test_three_attempts_maximum():
    calls = []
    def reply(request):
        calls.append(request)
        return httpx.Response(503, headers={"Retry-After": "0"})
    with pytest.raises(ReadError):
        run_read(reply)
    assert len(calls) == 3


@pytest.mark.parametrize("response", [b'[]', b'{bad', b'{"price":NaN}', b'{"price":Infinity}', b'\xff'])
def test_malformed_response_safe_failure(response):
    with pytest.raises(ReadError) as err:
        run_read(lambda request: httpx.Response(200, content=response))
    assert err.value.code == "RESPONSE_CONTRACT"


def test_size_limit():
    with patch("app.ozon_read.MAX_BYTES", 8):
        with pytest.raises(ReadError) as err:
            run_read(lambda request: httpx.Response(200, json={"result": []}))
    assert err.value.code == "RESPONSE_LIMIT"


@pytest.mark.parametrize("op,payload", [
    (ReadOperation.ACTIONS, {}),
    (ReadOperation.ROLES, {"url": "https://evil.invalid"}),
    (ReadOperation.ACTION_PRODUCTS, {"action_id": True, "limit": 100}),
    (ReadOperation.ACTION_PRODUCTS, {"action_id": 10001, "limit": 101}),
    (ReadOperation.AUTO_PRODUCTS, {"action_id": 10001, "limit": 100, "auto_add_date": "2026-10-08"}),
    (ReadOperation.STOCKS, {"limit": 100}),
    (ReadOperation.STOCKS, {"limit": 100, "offer_id": ["SYN-001"], "sku": ["20001"]}),
    (ReadOperation.PRODUCT_INFO, {"product_id": []}),
    (ReadOperation.PRICES, {"limit": 100, "filter": {}, "Api-Key": "override"}),
    (ReadOperation.WAREHOUSES, {"limit": 201}),
])
def test_invalid_request_never_sends(op, payload):
    calls = []
    with pytest.raises(ReadError):
        run_read(lambda request: calls.append(request), op, payload)
    assert not calls


def test_valid_selectors_caps_and_aware_date():
    validate_request(ReadOperation.STOCKS, {"limit": 1000, "offer_id": ["SYN-001"]})
    validate_request(ReadOperation.PRODUCT_INFO, {"product_id": ["9223372036854775807"]})
    validate_request(ReadOperation.WAREHOUSES, {"limit": 200})
    validate_request(ReadOperation.AUTO_CANDIDATES, {"action_id": 10001, "auto_add_date": "2026-10-14T00:00:00Z", "limit": 100, "offset": 0})


def test_retry_after_http_date():
    assert retry_delay("Thu, 01 Jan 1970 00:00:00 GMT", 0) == 0
    assert retry_delay("120", 0) == 120
    assert retry_delay("invalid", 1) == 2


def test_mock_never_reads_credentials_even_if_configured():
    with patch("app.connection.confined", side_effect=AssertionError("Secret file touched")):
        assert load_connection("MOCK").credentials is None
    assert SYNTHETIC.api_key not in repr(SYNTHETIC)
    assert SYNTHETIC.api_key not in repr(Connection("LIVE_READ", SYNTHETIC))


@pytest.mark.parametrize("text", ["OZON_CLIENT_ID=10001\nOZON_API_KEY=", "OZON_CLIENT_ID=10001\nOZON_API_KEY=SYN\nBASE_URL=https://evil.invalid", "OZON_CLIENT_ID=10001\nOZON_API_KEY=SYN\nOZON_API_KEY=SYN2", "OZON_CLIENT_ID=not-id\nOZON_API_KEY=SYN", "OZON_CLIENT_ID=10001\nOZON_API_KEY=SYN\x00SECRET"])
def test_credentials_validation_errors_hide_values(text):
    with pytest.raises(ValueError) as err:
        parse_credentials(text)
    assert "SECRET" not in str(err.value) and "https://evil" not in str(err.value)


def test_live_without_credentials_never_sends():
    async def run():
        calls = []
        client = ReadClient(None, transport=httpx.MockTransport(lambda request: calls.append(request)))
        try:
            with pytest.raises(ReadError) as err:
                await client.read(ReadOperation.ACTIONS)
            assert err.value.code == "NOT_CONFIGURED" and not calls
        finally:
            await client.close()
    asyncio.run(run())


def test_live_routes_block_local_plans_and_no_mock_fallback(tmp_path):
    calls = []
    client = ReadClient(None, transport=httpx.MockTransport(lambda request: calls.append(request)))
    app = create_app(Store(tmp_path / "live-test.sqlite3"), connection=Connection("LIVE_READ", None, "Not configured"), read_client=client)
    with TestClient(app, base_url="http://127.0.0.1:8000") as api:
        assert api.get("/api/health").json()["mode"] == "LIVE_READ"
        assert api.get("/api/connection").json() == {"mode": "LIVE_READ", "configured": False, "read_only": True, "message": "Not configured", "manual_execution":True,"account_id":None,"account_revision":0}
        assert api.get("/api/catalog").status_code == 503
        assert api.get("/api/catalog?scenario=partial").status_code == 422
        assert api.post("/api/preview", json={"kind": "stocks", "ids": ["SYN-001"], "mode": "manual", "value": "0", "rounding": "1"}).status_code == 403
        assert api.post("/api/drafts", json={"plan_id": "synthetic-plan", "plan_hash": "synthetic-hash"}).status_code == 403
        assert api.get("/api/connection/check").status_code == 503
        assert api.get("/api/connection", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert not calls and app.state.store.history() == []


def test_mock_check_cannot_connect(tmp_path):
    app = create_app(Store(tmp_path / "mock-check.sqlite3"), connection=Connection("MOCK"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as api:
        assert api.get("/api/connection/check").status_code == 409
        assert api.get("/api/catalog").json()["mode"] == "MOCK"
