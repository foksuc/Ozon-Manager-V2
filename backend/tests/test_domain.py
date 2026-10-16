import pytest
from decimal import Decimal
from app.domain import calculate_price, calculate_stock, decimal_value, discount_fields, signed_discount, stock_value


@pytest.mark.parametrize("price,rounding,result", [("1000", "0.01", "950.00"), ("1999", "0.01", "1899.05"), ("1999", "10", "1900.00"), ("1999", "100", "1900.00")])
def test_discount_vectors(price, rounding, result):
    assert calculate_price(price, "percent", "5", rounding, None) == result


def test_floor_after_rounding_and_signed_elastic():
    assert calculate_price("1000", "percent", "5", "100", "975.25") == "1000.00"
    assert calculate_price("1000", "manual", "949", "100", "975.25") == "975.25"
    assert signed_discount("1000", "1100") == Decimal("-10")
    assert calculate_price("1000", "percent", "-10", "0.01", None, "900", "1100") == "1100.00"
    with pytest.raises(ValueError):
        calculate_price("1000", "manual", "1100.01", "0.01", None, "900", "1100")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1.001", "1e3", "", "1000000000000", 1000])
def test_invalid_money(value):
    with pytest.raises(ValueError):
        decimal_value(value)


@pytest.mark.parametrize("mode,value", [("percent", "100"), ("fixed", "1001"), ("manual", "0"), ("manual", "-1")])
def test_nonpositive_price(mode, value):
    with pytest.raises(ValueError):
        calculate_price("1000", mode, value, "0.01", None)


@pytest.mark.parametrize("value", ["-1", "1.5", "NaN", "1000000000"])
def test_invalid_stock(value):
    with pytest.raises(ValueError):
        stock_value(value)


@pytest.mark.parametrize("before,mode,amount,after", [(9,"set","0",0),(9,"add","0",9),
    (9,"add","11",20),(0,"add","1",1),(999999998,"add","1",999999999)])
def test_available_stock_set_and_add_exact_integer(before, mode, amount, after):
    assert calculate_stock(before, mode, amount) == after


@pytest.mark.parametrize("before,mode,amount", [(None,"set","0"),(None,"add","1"),
    (True,"add","1"),(-1,"set","0"),(1.0,"add","1"),(999999999,"add","1"),
    (1000000000,"set","0"),(0,"unknown","1"),(1,"add","-1"),(1,"add","1.5")])
def test_stock_unknown_invalid_and_overflow_blocked(before, mode, amount):
    with pytest.raises(ValueError):
        calculate_stock(before, mode, amount)


def discount_entry(**changes):
    row = dict(role="member", price="1000", action_price="1050", currency="RUB",
               price_min_elastic="1100", price_max_elastic="800", auto_add_at=None)
    row.update(changes)
    return row


def test_signed_discounts_match_legacy_threshold_meaning():
    assert discount_fields(discount_entry()) == dict(minimum_discount="-10.00",
        maximum_discount="20.00", actual_discount="-5.00", auto_add_discount=None)


def test_recurring_discounts_display_round_half_up_only():
    result = discount_fields(discount_entry(price="6", price_min_elastic="5",
        price_max_elastic="4", action_price="5"))
    assert result == dict(minimum_discount="16.67", maximum_discount="33.33", actual_discount="16.67", auto_add_discount=None)
    assert discount_fields(discount_entry(price="200", action_price="199.99"))["actual_discount"] == "0.01"
    assert discount_fields(discount_entry(price="1000", action_price="1000.01"))["actual_discount"] == "0.00"


def test_auto_policies_are_not_inferred_as_discount_range():
    result = discount_fields(discount_entry(auto_add_at="2026-11-01T00:00:00Z",
        max_discount_price="950", min_seller_price="1100"))
    assert result["minimum_discount"] is None and result["maximum_discount"] is None
    assert result["actual_discount"] is None
    assert result["auto_add_discount"] == "-5.00"


def test_candidates_have_no_actual_discount_and_nonelastic_uses_action_threshold():
    result = discount_fields(discount_entry(role="candidate", price_min_elastic=None,
        price_max_elastic=None, max_action_price="900"))
    assert result == dict(minimum_discount="10.00", maximum_discount=None, actual_discount=None, auto_add_discount=None)


@pytest.mark.parametrize("changes", [{"currency":None}, {"price":None}, {"price":"0"},
    {"price":"NaN"}, {"money_currencies":{"price":None}}])
def test_missing_base_or_unknown_currency_keeps_discounts_unknown(changes):
    assert all(value is None for value in discount_fields(discount_entry(**changes)).values())


def test_per_field_currency_and_invalid_bounds_do_not_hide_unknown():
    result = discount_fields(discount_entry(money_currencies={"price_min_elastic":None,"action_price":"USD"}))
    assert result == dict(minimum_discount=None, maximum_discount="20.00", actual_discount=None, auto_add_discount=None)
    result = discount_fields(discount_entry(price_min_elastic="700", price_max_elastic="800"))
    assert result["minimum_discount"] is None and result["maximum_discount"] is None


@pytest.mark.parametrize("legacy", [False, True])
def test_requested_ordinary_price_display_vectors(legacy):
    bounds = dict(max_price="1648", min_price="1351") if legacy else dict(
        price_min_elastic="1648", price_max_elastic="1351")
    result = discount_fields(dict(role="member", price="1800", action_price="1783",
                                 currency="RUB", auto_add_at=None, **bounds))
    assert result == dict(minimum_discount="8.44", maximum_discount="24.94",
                         actual_discount="0.94", auto_add_discount=None)


@pytest.mark.parametrize("role", ["member", "candidate"])
def test_planned_auto_discount_is_separate_and_signed(role):
    result = discount_fields(discount_entry(price="1800", action_price="1827", role=role,
                                           auto_add_at="2026-11-01T00:00:00Z"))
    assert result == dict(minimum_discount=None, maximum_discount=None,
                         actual_discount=None, auto_add_discount="-1.50")


@pytest.mark.parametrize("changes", [{"price":None}, {"price":"0"}, {"action_price":None},
    {"money_currencies":{"price":"RUB", "action_price":"USD"}}])
def test_auto_unknown_or_mismatched_money_has_no_planned_percentage(changes):
    assert discount_fields(discount_entry(auto_add_at="2026-11-01T00:00:00Z", **changes))["auto_add_discount"] is None
