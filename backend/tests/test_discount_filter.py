from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from app.catalog import catalog
from app.connection import Connection
from app.discount_filter import filter_members
from app.main import create_app
from app.models import Product


def member(base="1000.00", action="950.00", **changes):
    source = deepcopy(next(row for row in catalog("ready")["products"] if row["role"] == "member"))
    source.update(price=base, action_price=action)
    source.update(changes)
    return Product.model_validate(source)


def test_inclusive_threshold_and_recurring_decimal_are_not_rounded():
    item = member()
    assert filter_members([item], "5")["matched"] == 1
    assert filter_members([member("10000", "9500.01")], "5")["matched"] == 0
    assert filter_members([member("10000", "9499.99")], "5")["matched"] == 1
    assert filter_members([item], "4,999999")["matched"] == 1
    assert filter_members([member("3.00", "2.00")], "33.333333")["matched"] == 1


def test_signed_boost_is_compared_without_absolute_value():
    item = member("100.00", "110.00")
    assert filter_members([item], "-11")["matched"] == 1
    assert filter_members([item], "-10")["matched"] == 1


def test_zero_negative_and_unknown_inputs_keep_existing_behavior():
    assert filter_members([member("100", "100")], "0")["matched"] == 1
    assert filter_members([member("100", "110")], "0")["matched"] == 0
    assert filter_members([member("100", "110")], "-10")["matched"] == 1
    unknown = member("100", "95", money_currencies={"price": "RUB", "action_price": None})
    assert filter_members([unknown], "5") == {"ids": [], "matched": 0, "unknown": 1, "total": 1}


@pytest.mark.parametrize("changes", [{"price":None},{"action_price":None},{"price":"0.00"},
    {"money_currencies":{"price":"RUB","action_price":None}},
    {"money_currencies":{"price":"RUB","action_price":"USD"}}])
def test_missing_or_incomparable_prices_count_as_unknown(changes):
    item = member(**changes)
    assert filter_members([item], "5") == {"ids":[],"matched":0,"unknown":1,"total":1}


@pytest.mark.parametrize("value", ["NaN","Infinity","5e3","","1.0000001"])
def test_invalid_threshold_is_rejected(value):
    with pytest.raises(ValueError):
        filter_members([member()], value)


def test_read_only_endpoint_has_no_ozon_or_store_operations(monkeypatch):
    async def no_http(*args, **kwargs):
        raise AssertionError("Read-only local calculation must not call Ozon")
    monkeypatch.setattr("app.ozon_read.ReadClient.read", no_http)
    app = create_app(store=object(), connection=Connection("LIVE_READ"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/discount-filter", json={"items":[member().model_dump(mode="json")],"threshold":"4"})
        assert response.status_code == 200 and response.json()["matched"] == 1
        assert client.post("/api/preview", json={"kind":"prices","scenario":"ready","ids":["synthetic"],
            "action_id":"synthetic","mode":"percent","value":"5","rounding":"0.01","floor":None}).status_code == 403
