"""Synthetic live-calculation acceptance: fresh reads, exact results, no persistence."""
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from app.connection import Connection
from app.main import create_app
from app.models import Catalog, ReadPreviewRequest
from app.read_preview import calculate_read_preview


def source(**changes):
    row = dict(id="synthetic", action_id="synthetic-action", offer_id="synthetic-offer", name="Synthetic",
        role="member", price="1999.00", action_price="1800.00", min_price=None, max_price=None,
        elastic=False, auto_add_at=None, currency="RUB")
    row.update(changes)
    return Catalog.model_validate(dict(mode="LIVE_READ", fetched_at="2026-10-09T00:00:00Z", complete=True,
        warnings=[], actions=[], products=[row], stocks=[]))


def request(**changes):
    payload = dict(kind="prices", ids=["synthetic"], action_id="synthetic-action", mode="percent",
        value="5", rounding="0.01", floor=None)
    payload.update(changes)
    return ReadPreviewRequest.model_validate(payload)


@pytest.mark.parametrize("params,expected", [({},"1899.05"),({"value":"5,00"},"1899.05"),
    ({"rounding":"10"},"1900.00"),({"rounding":"100","floor":"1950"},"1950.00"),
    ({"mode":"fixed","value":"100"},"1899.00"),({"mode":"manual","value":"1100"},"1100.00"),
    ({"value":"-5"},"2098.95")])
def test_exact_calculation_uses_base_and_compares_existing_action(params, expected):
    result = calculate_read_preview(source(), request(**params))
    assert result.rows[0].after == expected
    assert result.rows[0].before == "1800.00"
    assert result.valid and result.hash == "" and result.source.mode == "LIVE_READ"


@pytest.mark.parametrize("changes", [{"action_price":None}, {"price":None},
    {"money_currencies":{"action_price":None}}, {"currency":"USD"},
    {"min_price":"1800", "money_currencies":{"min_price":None}},
    {"elastic":True}, {"max_action_price":"1500"}, {"min_price":"1950"}])
def test_unknown_data_and_out_of_bound_are_row_errors(changes):
    result = calculate_read_preview(source(**changes), request())
    assert not result.valid and result.rows[0].error and result.rows[0].after is None


def test_partial_cannot_be_called_verified():
    result = calculate_read_preview(source().model_copy(update={"complete":False}), request())
    assert not result.valid and not result.complete and result.errors


def test_stale_selection_rejected_and_mixed_scope_blocked():
    with pytest.raises(ValueError):
        calculate_read_preview(source(), request(ids=["disappeared"]))
    with pytest.raises(ValueError):
        calculate_read_preview(source(), request(role="candidate"))
    with pytest.raises(ValueError):
        calculate_read_preview(source(), request(auto_add=True, auto_add_date="2026-10-10T00:00:00Z"))


@pytest.mark.parametrize("available", [9,0,None])
def test_stock_zero_and_absent_are_distinct(available):
    data = source().model_dump()
    data["stocks"] = [dict(id="synthetic",offer_id="stock",name="Synthetic stock",warehouse_id="1",
        warehouse_name="Synthetic",available=available,reserved=2,present=12)]
    result = calculate_read_preview(Catalog.model_validate(data), request(kind="stocks", action_id=None,
        mode="manual", value="0", rounding="1"))
    if available is None:
        assert result.rows[0].before is None and result.rows[0].after is None and not result.valid
    else:
        assert result.rows[0].after == "0" and result.rows[0].diff == str(-available)


@pytest.mark.parametrize("available,amount,expected", [(9,"11","20"),(0,"0","0"),
    (None,"1",None),(999999999,"1",None)])
def test_increment_stock_uses_fresh_available_and_returns_new_quantity(available, amount, expected):
    data = source().model_dump()
    data["stocks"] = [dict(id="synthetic",offer_id="stock",name="Synthetic stock",warehouse_id="1",
        warehouse_name="Synthetic",available=available,reserved=2,present=12)]
    result = calculate_read_preview(Catalog.model_validate(data), request(kind="stocks",action_id=None,
        mode="manual",value=amount,rounding="1",stock_mode="add"))
    assert result.rows[0].after == expected
    assert result.valid == (expected is not None)
    if expected is not None:
        assert result.rows[0].diff == amount
    else:
        assert result.rows[0].diff is None and result.rows[0].error


def test_stock_add_disappeared_selection_and_partial_snapshot_blocked():
    with pytest.raises(ValueError):
        calculate_read_preview(source(), request(kind="stocks",action_id=None,stock_mode="add"))
    data = source().model_dump()
    data.update(complete=False,stocks=[dict(id="synthetic",offer_id="stock",name="Synthetic",
        warehouse_id="1",warehouse_name="Synthetic",available=9,reserved=2,present=11)])
    result = calculate_read_preview(Catalog.model_validate(data), request(kind="stocks",action_id=None,
        value="1",stock_mode="add"))
    assert result.rows[0].after == "10" and not result.valid


def test_endpoint_reads_again_and_never_uses_store_or_client_json_items():
    app = create_app(store=object(), connection=Connection("LIVE_READ"))
    class Reader:
        calls = []
        async def catalog(self, **params):
            self.calls.append(deepcopy(params))
            return source(price="1000.00").model_dump()
    reader = Reader()
    app.state.live_catalog = reader
    with TestClient(app, base_url="http://127.0.0.1") as client:
        payload = request().model_dump()
        response = client.post("/api/read-preview", json=payload)
        assert response.status_code == 200 and response.json()["rows"][0]["after"] == "950.00"
        assert reader.calls == [dict(scope="products",action_id="synthetic-action",role="member",auto_add=False,auto_add_date=None)]
        assert client.post("/api/read-preview",json={**payload,"items":[]}).status_code == 422
        assert len(reader.calls) == 1
        assert client.post("/api/drafts",json={"plan_id":response.json()["id"],"plan_hash":""}).status_code == 403
        assert client.post("/api/preview",json=request().model_dump(exclude={"role","auto_add","auto_add_date"})).status_code == 403


def test_wrong_context_rejected_before_read_and_mock_never_calls_live():
    app = create_app(store=object(), connection=Connection("LIVE_READ"))
    app.state.live_catalog = object()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/read-preview",json=request(ids=["synthetic","synthetic"]).model_dump()).status_code == 422
        assert client.post("/api/read-preview",json=request(kind="stocks").model_dump()).status_code == 422
    mock = create_app(store=object(),connection=Connection("MOCK"))
    with TestClient(mock,base_url="http://127.0.0.1") as client:
        assert client.post("/api/read-preview",json=request().model_dump()).status_code == 409
