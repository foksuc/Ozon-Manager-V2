"""Minimal discount selects a vendor money bound, never a rounded percent."""
import pytest
from app.domain import minimum_discount_price


@pytest.mark.parametrize('entry,expected',[
    ({'price':'3700.00','min_price':'3000.00','max_price':'3546.00','price_min_elastic':'3546.00'},'3546.00'),
    ({'price':'1999.00','max_action_price':'1899.05'},'1899.05'),
    ({'max_price':'100.99','max_action_price':'100.95','min_price':'90.00'},'100.95'),
    ({'price':'100.00','max_price':'120.50','min_price':'100.00'},'120.50'),
])
def test_preserves_exact_highest_eligible_amount(entry,expected):
    assert minimum_discount_price(entry)==expected


@pytest.mark.parametrize('entry',[{}, {'max_price':'0'}, {'max_price':'nan'},
    {'max_price':'100.123'}, {'max_price':'100','min_price':'101'},
    {'max_price':'100','price_max_elastic':'110'}])
def test_absent_invalid_or_inverted_bounds_never_guess_price(entry):
    with pytest.raises(ValueError):minimum_discount_price(entry)
