"""Synthetic reader tests: never read credentials or contact Seller API."""
import asyncio
from copy import deepcopy
import pytest
from app.live_catalog import LiveCatalog, State, money_value, sku_value
from app.ozon_read import ReadError, ReadOperation as Op
from app.models import Catalog

BIG = "9007199254740993"
DATE = "2026-11-01T12:00:00Z"


def rub(value):
    return {"amount": value, "currency": "RUB"}


def action():
    return {"id": "123", "title": "Synthetic action", "date_start": "2020-01-01T00:00:00Z",
            "date_end": "2030-01-01T00:00:00Z", "auto_add_dates": [DATE],
            "potential_products_count": 13, "participating_products_count": 7}


class Fake:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def read(self, op, payload=None):
        self.calls.append((op, deepcopy(payload)))
        answer = self.responses[op]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if callable(answer):
            answer = answer(payload)
        if isinstance(answer, Exception):
            raise answer
        return deepcopy(answer)


def run(reader, **params):
    result = asyncio.run(reader.catalog(**params))
    Catalog.model_validate(result)
    return result


def product_fake(op, raw):
    return Fake({Op.ACTIONS: {"result": [action()]}, op: {"products": [raw], "total": 1},
                 Op.PRODUCT_INFO: {"items": [{"id": BIG, "offer_id": "synthetic-offer", "name": "Synthetic product", "sku": "999", "price": "1000.00", "currency_code": "RUB"}]},
                 Op.PRICES: {"items": [{"product_id": BIG, "offer_id": "price-offer", "price": {"price": "1000.00", "currency_code": "RUB"}}], "total_items": 1, "cursor": ""}})


def test_lazy_actions_dates_and_unknown_status():
    first = action()
    second = {"id": 124, "title": "Unknown dates", "auto_add_dates": []}
    fake = Fake({Op.ACTIONS: {"result": [first, second]}})
    catalog = run(LiveCatalog(fake))
    assert [op for op, payload in fake.calls] == [Op.ACTIONS]
    assert catalog["mode"] == "LIVE_READ"
    assert catalog["actions"][0]["auto_add_dates"] == [DATE]
    assert catalog["actions"][0]["member_count"] == 7
    assert catalog["actions"][1]["status"] == "unknown"
    assert catalog["actions"][1]["candidate_count"] is None


def test_ordinary_candidate_maximum_is_preserved_separately_from_elastic():
    fake = product_fake(Op.ACTION_CANDIDATES, {"id": BIG, "price": rub("1000"), "max_action_price": rub("900")})
    result = run(LiveCatalog(fake), scope="products", action_id="123", role="candidate", auto_add=False)
    row = result["products"][0]
    assert row["max_action_price"] == "900.00"
    assert row["price_min_elastic"] is None and row["price_max_elastic"] is None
    assert Op.AUTO_CANDIDATES not in [op for op, payload in fake.calls]


@pytest.mark.parametrize("role,op,id_field", [("member", Op.AUTO_PRODUCTS, "product_id"), ("candidate", Op.AUTO_CANDIDATES, "id")])
def test_auto_roles_exact_date_and_large_ids(role, op, id_field):
    fake = product_fake(op, {id_field: BIG, "price": rub("1000"), "action_price_to_auto_add": rub("900"), "min_seller_price": rub("800"), "max_discount_price": rub("950")})
    result = run(LiveCatalog(fake), scope="products", action_id="123", role=role, auto_add=True, auto_add_date=DATE)
    product = result["products"][0]
    assert product["product_id"] == BIG and product["sku"] == "999"
    assert product["auto_add_at"] == DATE and product["role"] == role
    assert product["action_price"] == "900.00"
    assert product["min_seller_price"] == "800.00" and product["max_discount_price"] == "950.00"
    assert product["minimum_discount"] is None and product["maximum_discount"] is None
    assert product["actual_discount"] is None
    assert product["auto_add_discount"] == "10.00"
    assert product["currency_assumed"] is False
    assert fake.calls[1] == (op, {"action_id": 123, "auto_add_date": DATE, "limit": 100, "offset": 0})
    assert fake.calls[2][1]["product_id"] == [BIG]
    assert result["complete"]


def test_auto_date_rejected_before_products_request():
    fake = Fake({Op.ACTIONS: {"result": [action()]}})
    with pytest.raises(ReadError):
        run(LiveCatalog(fake), scope="products", action_id="123", auto_add=True, auto_add_date="2026-11-01")
    assert len(fake.calls) == 1


def test_auto_thresholds_are_independent_even_when_seller_minimum_is_higher():
    raw = {"product_id": BIG, "price": rub("1000"), "action_price_to_auto_add": rub("900"),
           "min_seller_price": rub("1100"), "max_discount_price": rub("950")}
    result = run(LiveCatalog(product_fake(Op.AUTO_PRODUCTS, raw)), scope="products", action_id="123",
                 auto_add=True, auto_add_date=DATE)
    row = result["products"][0]
    assert row["min_seller_price"] == "1100.00" and row["max_discount_price"] == "950.00"
    assert row["min_price"] is None and row["max_price"] is None
    assert result["complete"] and not result["warnings"]


def test_ordinary_elastic_mapping_and_same_auto_action_allowed():
    raw = {"id": BIG, "price": rub("1000"), "price_min_elastic": rub("1100"), "price_max_elastic": rub("800")}
    result = run(LiveCatalog(product_fake(Op.ACTION_PRODUCTS, raw)), scope="products", action_id="123")
    product = result["products"][0]
    assert product["min_price"] == "800.00" and product["max_price"] == "1100.00"
    assert product["price_min_elastic"] == "1100.00"
    assert product["minimum_discount"] == "-10.00"
    assert product["maximum_discount"] == "20.00"
    assert product["actual_discount"] is None
    assert product["auto_add_at"] is None and product["currency"] == "RUB"


def test_inverted_bounds_are_not_reordered():
    raw = {"id": BIG, "price": rub("1000"), "price_min_elastic": rub("700"), "price_max_elastic": rub("800")}
    result = run(LiveCatalog(product_fake(Op.ACTION_PRODUCTS, raw)), scope="products", action_id="123")
    assert result["products"][0]["min_price"] == "800.00"
    assert result["products"][0]["max_price"] == "700.00"
    assert not result["complete"]
    assert result["products"][0]["minimum_discount"] is None
    assert result["products"][0]["maximum_discount"] is None


def test_price_and_offer_enrichment_fallback():
    fake = product_fake(Op.ACTION_CANDIDATES, {"id": BIG})
    fake.responses[Op.PRODUCT_INFO] = {"items": [{"id": BIG, "name": "Synthetic", "sources": [{"sku": "1234"}, {"sku": 1234}]}]}
    result = run(LiveCatalog(fake), scope="products", action_id="123", role="candidate")
    product = result["products"][0]
    assert product["offer_id"] == "price-offer"
    assert product["price"] == "1000.00" and product["currency"] == "RUB"
    assert product["sku"] == "1234"


def test_discount_enrichment_occurs_after_base_fallback_with_field_currency_gating(monkeypatch):
    from datetime import datetime, timezone
    class Before(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 9, tzinfo=timezone.utc)
    monkeypatch.setattr("app.live_catalog.datetime", Before)
    raw = {"id": BIG, "price_min_elastic": rub("1100"), "price_max_elastic": rub("800"),
           "action_price": rub("950")}
    row = run(LiveCatalog(product_fake(Op.ACTION_PRODUCTS, raw)), scope="products",action_id="123")["products"][0]
    assert row["price"] == "1000.00"
    assert (row["minimum_discount"],row["maximum_discount"],row["actual_discount"]) == ("-10.00","20.00","5.00")
    raw["action_price"] = {"amount":"950", "currency":""}
    row = run(LiveCatalog(product_fake(Op.ACTION_PRODUCTS, raw)), scope="products",action_id="123")["products"][0]
    assert row["actual_discount"] == "5.00"
    assert row["currency_assumed"] is True
    assert row["money_assumed_fields"] == ["action_price"]


@pytest.fixture
def legacy_display_clock(monkeypatch):
    from datetime import datetime, timezone
    class Before(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime(2026,10,9,tzinfo=timezone.utc)
    monkeypatch.setattr('app.live_catalog.datetime',Before)


def test_explicit_catalog_price_confirms_only_base_of_legacy_action(legacy_display_clock):
    raw={"id":BIG,"price":"1100","action_price":"900","max_action_price":"950"}
    row=run(LiveCatalog(product_fake(Op.ACTION_PRODUCTS,raw)),scope="products",action_id="123")["products"][0]
    assert row["price"]=="1000.00" and row["money_currencies"]["price"]=="RUB"
    assert row["currency_assumed"] and set(row["money_assumed_fields"])=={"action_price","max_action_price"}


def test_explicit_foreign_catalog_currency_replaces_no_assumption(legacy_display_clock):
    fake=product_fake(Op.ACTION_PRODUCTS,{"id":BIG,"price":"1100","action_price":"900"})
    fake.responses[Op.PRICES]["items"][0]["price"]["currency_code"]="USD"
    result=run(LiveCatalog(fake),scope="products",action_id="123")
    row=result["products"][0]
    assert row["price"] is None and row["currency"]=="USD" and row["money_currencies"]["price"]=="USD"
    assert row["money_assumed_fields"]==["action_price"] and not result["complete"]


@pytest.mark.parametrize("value", [rub("NaN"), rub("1.001"), {"amount": "10", "currency": "USD"}, True])
def test_malformed_or_foreign_money_nullable(value):
    state = State()
    assert money_value(value, state)[0] is None
    assert state.warnings and not state.complete


def test_scalar_transition_user_assumption_expires(monkeypatch):
    from datetime import datetime, timezone
    class Before(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 8, tzinfo=timezone.utc)
    monkeypatch.setattr("app.live_catalog.datetime", Before)
    state = State()
    assert money_value("1000.01", state) == ("1000.01", "RUB")
    assert state.complete and state.warnings == ["RUB задана пользователем временно"]
    class After(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 13, tzinfo=timezone.utc)
    monkeypatch.setattr("app.live_catalog.datetime", After)
    assert money_value("1000.01", State()) == (None, None)


@pytest.mark.parametrize("empty_currency", [None, ""])
def test_transition_missing_currency_uses_explicit_user_assumption(monkeypatch, empty_currency):
    from datetime import datetime, timezone
    class Before(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 8, tzinfo=timezone.utc)
    monkeypatch.setattr("app.live_catalog.datetime", Before)
    state = State()
    assert money_value({"amount": "1000.01", "currency": empty_currency}, state) == ("1000.01", "RUB")
    assert state.complete and state.warnings
    fake = product_fake(Op.ACTION_PRODUCTS, {"id": BIG, "price": rub("1000"),
                                            "action_price": {"amount": "900", "currency": empty_currency}})
    row = run(LiveCatalog(fake), scope="products", action_id="123")["products"][0]
    assert row["currency"] == "RUB" and row["money_currencies"]["price"] == "RUB"
    assert row["action_price"] == "900.00" and row["money_currencies"]["action_price"] == "RUB"
    assert row["currency_assumed"] is True and row["actual_discount"] == "10.00"
    class After(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 13, tzinfo=timezone.utc)
    monkeypatch.setattr("app.live_catalog.datetime", After)
    state = State()
    assert money_value({"amount": "1000.01", "currency": empty_currency}, state)[0] is None
    assert not state.complete


def test_foreign_primary_currency_is_not_replaced_by_rub_enrichment():
    raw = {"id": BIG, "price": {"amount":"1800", "currency":"USD"},
           "action_price": rub("1783"), "price_min_elastic": rub("1648"),
           "price_max_elastic": rub("1351")}
    result = run(LiveCatalog(product_fake(Op.ACTION_PRODUCTS, raw)), scope="products", action_id="123")
    row = result["products"][0]
    assert row["currency"] == "USD" and row["price"] is None and row["currency_assumed"] is False
    assert row["actual_discount"] is None and row["minimum_discount"] is None and row["maximum_discount"] is None
    assert not result["complete"]


@pytest.mark.parametrize("role,op,id_field", [("member", Op.AUTO_PRODUCTS, "product_id"),
                                              ("candidate", Op.AUTO_CANDIDATES, "id")])
def test_auto_requested_vector_preserves_negative_planned_percentage(role, op, id_field):
    raw = {id_field: BIG, "price": rub("1800"), "action_price_to_auto_add": rub("1827")}
    row = run(LiveCatalog(product_fake(op, raw)), scope="products", action_id="123", role=role,
              auto_add=True, auto_add_date=DATE)["products"][0]
    assert row["auto_add_discount"] == "-1.50" and row["actual_discount"] is None


def test_sku_ambiguity_nullable_and_top_level_authoritative():
    state = State()
    assert sku_value({"sources": [{"sku": 100}, {"sku": 101}]}, state) is None
    assert not state.complete
    assert sku_value({"sku": 102, "sources": [{"sku": 100}, {"sku": 101}]}, State()) == "102"


@pytest.mark.parametrize("pages", [
    [{"products": [{"id": 1}], "last_id": "same"}, {"products": [{"id": 2}], "last_id": "same"}],
    [{"products": [{"id": 1}], "total": 3, "last_id": "a"}, {"products": [{"id": 2}], "total": 2, "last_id": "b"}],
    [{"products": [{"id": 1}], "total": 2, "last_id": "a"}, {"products": [], "total": 2}],
    [{"products": [{"id": 1}], "last_id": "a"}, ReadError("NETWORK", "Synthetic read failure")],
    [{"products": [{"id": 1}], "last_id": "a"}, {"products": [{"id": 1}], "last_id": "b"}, {"products": []}],
])
def test_pagination_anomalies_retain_rows(pages):
    fake = Fake({Op.ACTION_PRODUCTS: pages})
    state = State()
    result = asyncio.run(LiveCatalog(fake).pages(Op.ACTION_PRODUCTS, {"limit": 1, "last_id": ""}, "products", state, lambda row: str(row["id"])))
    assert result[0]["id"] == 1
    assert not state.complete and state.warnings


def test_absent_total_is_not_zero():
    fake = Fake({Op.AUTO_PRODUCTS: [{"products": [{"product_id": 1}]}, {"products": []}]})
    state = State()
    result = asyncio.run(LiveCatalog(fake).pages(Op.AUTO_PRODUCTS, {"limit": 1, "offset": 0}, "products", state, lambda row: str(row["product_id"]), offset=True))
    assert len(result) == 1 and len(fake.calls) == 2 and state.complete


def test_bad_primary_schema_raises_safe_error_not_mock():
    fake = Fake({Op.ACTIONS: {"result": "bad array"}})
    with pytest.raises(ReadError):
        run(LiveCatalog(fake))


def test_stock_selector_and_nullable_authoritative_fields():
    fake = Fake({Op.PRODUCT_LIST: {"result": {"items": [{"product_id": BIG, "offer_id": "synthetic"}, {"product_id": "2"}], "total_items": 2}},
                 Op.PRODUCT_INFO: {"items": [{"id": BIG, "offer_id": "synthetic", "name": "One"}, {"id": "2", "sku": "22", "name": "Two"}]},
                 Op.WAREHOUSES: {"warehouses": [{"warehouse_id": "5", "name": "FBS"}], "has_next": False},
                 Op.STOCKS: [{"products": [{"product_id": BIG, "warehouse_id": "5", "free_stock": 4, "reserved": 6, "present": 20}], "has_next": False},
                             {"products": [{"product_id": "2", "warehouse_id": "5", "reserved": 0}], "has_next": False}]})
    result = run(LiveCatalog(fake), scope="stocks")
    calls = [payload for op, payload in fake.calls if op == Op.STOCKS]
    assert calls[0]["offer_id"] == ["synthetic"] and "sku" not in calls[0]
    assert calls[1]["sku"] == ["22"] and "offer_id" not in calls[1]
    assert next(payload for op, payload in fake.calls if op == Op.WAREHOUSES) == {"limit": 200, "cursor": ""}
    assert result["stocks"][0]["available"] == 4  # Do not derive 20-6.
    assert result["stocks"][1]["available"] is None and result["stocks"][1]["present"] is None
    assert not result["complete"]


def test_stock_catalog_and_card_batches_1000():
    items = [{"product_id": str(i), "offer_id": f"synthetic-{i}"} for i in range(1, 1002)]
    fake = Fake({Op.PRODUCT_LIST: [{"result": {"items": items[:1000], "total_items": 1001, "last_id": "next"}},
                                  {"result": {"items": items[1000:], "total_items": 1001, "last_id": ""}}],
                 Op.PRODUCT_INFO: lambda payload: {"items": [{"id": pid, "offer_id": f"synthetic-{pid}", "name": "Synthetic"} for pid in payload["product_id"]]},
                 Op.WAREHOUSES: {"warehouses": [], "has_next": False}, Op.STOCKS: {"products": [], "has_next": False}})
    result = run(LiveCatalog(fake), scope="stocks")
    assert result["complete"]
    assert [len(payload["offer_id"]) for op, payload in fake.calls if op == Op.STOCKS] == [1000, 1]
    assert [len(payload["product_id"]) for op, payload in fake.calls if op == Op.PRODUCT_INFO] == [1000, 1]


def test_deadline_retains_products_received_before_next_page(monkeypatch):
    monkeypatch.setattr("app.live_catalog.DEADLINE_SECONDS", 0.01)
    class Slow:
        async def read(self, op, payload=None):
            if op == Op.ACTIONS:
                return {"result": [action()]}
            if op == Op.ACTION_PRODUCTS and not payload["last_id"]:
                return {"products": [{"id": i, "price": rub("1000")} for i in range(1, 101)], "total": 101, "last_id": "next"}
            await asyncio.sleep(1)
    result = run(LiveCatalog(Slow()), scope="products", action_id="123")
    assert len(result["products"]) == 100
    assert not result["complete"] and result["warnings"]


def test_deadline_retains_stock_rows(monkeypatch):
    monkeypatch.setattr("app.live_catalog.DEADLINE_SECONDS", 0.01)
    class Slow:
        async def read(self, op, payload=None):
            if op == Op.PRODUCT_LIST:
                return {"result": {"items": [{"product_id": "1", "offer_id": "synthetic"}], "total_items": 1}}
            if op == Op.PRODUCT_INFO:
                return {"items": [{"id": "1", "offer_id": "synthetic", "name": "Synthetic"}]}
            if op == Op.WAREHOUSES:
                return {"warehouses": [], "has_next": False}
            if op == Op.STOCKS and not payload["cursor"]:
                return {"products": [{"product_id": "1", "warehouse_id": "5", "free_stock": 4, "reserved": 0, "present": 4}], "has_next": True, "cursor": "next"}
            await asyncio.sleep(1)
    result = run(LiveCatalog(Slow()), scope="stocks")
    assert len(result["stocks"]) == 1 and result["stocks"][0]["available"] == 4
    assert not result["complete"]


def test_enrichment_failures_leave_honest_partial_products():
    fake = product_fake(Op.ACTION_PRODUCTS, {"id": BIG, "price": rub("1000")})
    fake.responses[Op.PRODUCT_INFO] = ReadError("NETWORK", "Synthetic failure")
    fake.responses[Op.PRICES] = {"items": "not-an-array"}
    result = run(LiveCatalog(fake), scope="products", action_id="123")
    assert len(result["products"]) == 1
    assert result["products"][0]["price"] == "1000.00"
    assert result["products"][0]["offer_id"] == ""
    assert not result["complete"]


@pytest.mark.parametrize("cards_fail", [False, True])
def test_auto_primary_metadata_and_auto_price_survive_enrichment(cards_fail):
    raw = {"product_id": BIG, "price": rub("1000"), "action_price_to_auto_add": rub("899.9900"),
           "action_price": rub("777"), "offer_id": "native-offer", "name": "Native product", "sku": "456"}
    fake = product_fake(Op.AUTO_PRODUCTS, raw)
    if cards_fail:
        fake.responses[Op.PRODUCT_INFO] = ReadError("NETWORK", "Synthetic failure")
        fake.responses[Op.PRICES] = ReadError("NETWORK", "Synthetic failure")
    result = run(LiveCatalog(fake), scope="products", action_id="123", auto_add=True, auto_add_date=DATE)
    product = result["products"][0]
    assert product["action_price"] == "899.99"
    assert product["offer_id"] == "native-offer" and product["name"] == "Native product" and product["sku"] == "456"
    if cards_fail:
        assert not result["complete"]


@pytest.mark.parametrize("raw,expected", [("0", "0.00"), ("0.0000", "0.00"), ("19.9900", "19.99")])
def test_read_money_zero_and_lossless_trailing_zeros(raw, expected):
    state = State()
    assert money_value(rub(raw), state) == (expected, "RUB")
    assert state.complete and not state.warnings


def test_money_object_missing_amount_is_malformed():
    state = State()
    assert money_value({"currency": "RUB"}, state) == (None, None)
    assert not state.complete and state.warnings


def test_invalid_auto_dates_excluded_but_valid_exact_siblings_retained():
    raw = action()
    other = "2026-11-02T12:00:00+03:00"
    raw["auto_add_dates"] = [DATE, "2026-11-01", "not-a-date", other, None]
    result = run(LiveCatalog(Fake({Op.ACTIONS: {"result": [raw]}})))
    assert result["actions"][0]["auto_add_dates"] == [DATE, other]
    assert result["actions"][0]["auto_add"]
    assert not result["complete"]


@pytest.mark.parametrize("op,array,id_field", [(Op.STOCKS, "products", "product_id"), (Op.WAREHOUSES, "warehouses", "warehouse_id")])
@pytest.mark.parametrize("flag", [None, "false", 0])
def test_stock_has_next_requires_boolean_even_when_total_reached(op, array, id_field, flag):
    body = {array: [{id_field: 1}], "total": 1}
    if flag is not None:
        body["has_next"] = flag
    state = State()
    rows = asyncio.run(LiveCatalog(Fake({op: body})).pages(op, {"limit": 100, "cursor": ""}, array, state,
                            lambda row: str(row[id_field]), cursor="cursor"))
    assert len(rows) == 1 and not state.complete


def test_has_next_true_conflicts_with_reached_total():
    state = State()
    fake = Fake({Op.STOCKS: {"products": [{"product_id": 1}], "total": 1, "has_next": True, "cursor": "next"}})
    rows = asyncio.run(LiveCatalog(fake).pages(Op.STOCKS, {"limit": 100, "cursor": ""}, "products", state,
                                              lambda row: str(row["product_id"]), cursor="cursor"))
    assert len(rows) == 1 and not state.complete
    assert any("противоречит" in warning for warning in state.warnings)


def test_excessive_total_retains_known_first_page(monkeypatch):
    monkeypatch.setattr("app.live_catalog.MAX_ITEMS", 2)
    state = State()
    fake = Fake({Op.ACTION_PRODUCTS: {"products": [{"id": 1}], "total": 100, "last_id": "next"}})
    rows = asyncio.run(LiveCatalog(fake).pages(Op.ACTION_PRODUCTS, {"limit": 100, "last_id": ""}, "products", state, lambda row: str(row["id"])))
    assert rows == [{"id": 1}] and not state.complete


def test_stock_global_cap_across_offer_batches(monkeypatch):
    monkeypatch.setattr("app.live_catalog.MAX_ITEMS", 1002)
    items = [{"product_id": str(i), "offer_id": f"synthetic-{i}"} for i in range(1, 1002)]
    first = [{"product_id": "1", "warehouse_id": str(i), "free_stock": 0, "reserved": 0, "present": 0} for i in range(1, 1001)]
    second = [{"product_id": "1001", "warehouse_id": str(i), "free_stock": 0, "reserved": 0, "present": 0} for i in range(1, 4)]
    fake = Fake({Op.PRODUCT_LIST: [{"result": {"items": items[:1000], "total_items": 1001, "last_id": "next"}},
                                  {"result": {"items": items[1000:], "total_items": 1001}}],
                 Op.PRODUCT_INFO: lambda payload: {"items": [{"id": pid, "name": "Synthetic", "offer_id": f"synthetic-{pid}"} for pid in payload["product_id"]]},
                 Op.WAREHOUSES: {"warehouses": [], "has_next": False},
                 Op.STOCKS: [{"products": first, "has_next": False}, {"products": second, "has_next": False}]})
    result = run(LiveCatalog(fake), scope="stocks")
    assert len(result["stocks"]) == 1002 and not result["complete"]
    assert len([op for op, payload in fake.calls if op == Op.STOCKS]) == 2

@pytest.mark.parametrize('boost', ['0', '17.25'])
def test_current_boost_survives_read_without_using_discount(boost):
    fake = product_fake(Op.ACTION_PRODUCTS, {'id': BIG, 'price': rub('1000'), 'action_price': rub('950'), 'price_min_elastic': rub('990'), 'price_max_elastic': rub('800'), 'current_boost': boost})
    result = run(LiveCatalog(fake), scope='products', action_id='123', role='member', auto_add=False)
    assert result['products'][0]['current_boost'] == boost
    assert result['products'][0]['actual_discount'] == '5.00'

@pytest.mark.parametrize('raw', [None, True, 'NaN', 'Infinity', 'bad'])
def test_unknown_boost_is_not_zero(raw):
    from app.live_catalog import boost_value
    assert boost_value(raw) is None
