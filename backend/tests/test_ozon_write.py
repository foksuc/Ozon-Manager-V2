import asyncio
import httpx
import pytest
from app.connection import Credentials
from app.ozon_read import ReadOperation
from app.ozon_write import WriteClient, WriteOperation as Op, WriteError, validate_request


BODY = {"prices": [{"product_id": 111, "price": "10.00", "currency_code": "RUB"}]}


def test_closed_host_headers_no_retry_or_secret_error():
    async def run():
        calls = []
        def handler(request):
            calls.append(request)
            assert request.url.host == "api-seller.ozon.ru"
            assert request.url.path == "/v1/product/import/prices"
            assert request.headers["api-key"] == "synthetic"
            return httpx.Response(429, json={"message": "synthetic-sensitive-body"})
        client = WriteClient(Credentials("123", "synthetic"), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(WriteError) as error:
                await client.send(Op.PRICE, BODY)
            assert error.value.code == "UNKNOWN_RESULT"
            assert "synthetic-sensitive-body" not in str(error.value)
            assert len(calls) == 1
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("reply", [httpx.Response(302, headers={"location": "https://other.example"}), httpx.Response(200, content=b"broken"), httpx.Response(503)])
def test_redirect_malformed_and_server_error_never_retry(reply):
    async def run():
        calls = []
        def handler(request):
            calls.append(request)
            return reply
        client = WriteClient(Credentials("1", "synthetic"), transport=httpx.MockTransport(handler))
        with pytest.raises(WriteError, match="исход|Исход|подтвердил"):
            await client.send(Op.PRICE, BODY)
        assert len(calls) == 1
        await client.close()
    asyncio.run(run())


def test_read_enum_fails_before_network():
    async def run():
        def handler(request):
            raise AssertionError("Must not send")
        client = WriteClient(Credentials("1", "synthetic"), transport=httpx.MockTransport(handler))
        with pytest.raises(WriteError):
            await client.send(ReadOperation.PRICES, BODY)
        await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("body", [{"prices": []}, {"prices": BODY["prices"] * 2}, {"prices": [{"product_id": 1, "offer_id": "x", "price": "1.00", "currency_code": "RUB"}]}, {"prices": [{"product_id": 1, "price": 1.0, "currency_code": "RUB"}]}, {"prices": [{"product_id": 1, "price": "-1", "currency_code": "RUB"}]}, {"prices": [{"product_id": True, "price": "1", "currency_code": "RUB"}]}])
def test_strict_money_selector_duplicate_validation(body):
    with pytest.raises(WriteError):
        validate_request(Op.PRICE, body)


def test_exact_contracts_caps_and_auto_delete_string_id():
    validate_request(Op.ACTION_REMOVE, {"action_id": 3, "product_ids": ["1"]})
    with pytest.raises(WriteError):
        validate_request(Op.ACTION_REMOVE, {"action_id": 3, "product_ids": [1]})
    validate_request(Op.ACTION_UPDATE, {"action_id": 3, "products": [{"product_id": 1, "action_price": {"amount": "10.00", "currency": "RUB"}}]})
    validate_request(Op.AUTO_UPDATE, {"action_id": 3, "auto_add_date": "2026-10-09T00:00:00Z", "products": [{"id": 1, "action_price": {"amount": "10.00", "currency": "RUB"}}]})
    validate_request(Op.AUTO_REMOVE, {"action_id": 3, "auto_add_date": "2026-10-09T00:00:00Z", "product_ids": ["1"]})
    with pytest.raises(WriteError):
        validate_request(Op.STOCK, {"stocks": [{"product_id": i + 1, "warehouse_id": 1, "stock": 0} for i in range(101)]})
    with pytest.raises(WriteError):
        validate_request(Op.PRICE, {"prices": [{"product_id": 2**63, "price": "1.00", "currency_code": "RUB"}]})
