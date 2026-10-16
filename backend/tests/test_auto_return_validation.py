"""LIVE current-profile auto return accepts only an exact vendor proposal."""
from datetime import datetime, timezone

import pytest

from app import ozon_write
from app.mutations import MutationService
from app.ozon_write import WriteOperation as Op, WriteError


@pytest.fixture
def candidate(monkeypatch):
    monkeypatch.setattr(ozon_write, '_utc_now', lambda: datetime(2026, 10, 13, tzinfo=timezone.utc))
    return {'member': False, 'role': 'candidate', 'currency': 'RUB', 'current': None,
        'action_price': '3546.27', 'money_currencies': {'price': 'RUB', 'action_price': 'RUB'}}


def test_exact_candidate_price_is_preserved(candidate):
    assert MutationService._validate(Op.AUTO_UPDATE, candidate, '3546.27', live=True) == '3546.27'


@pytest.mark.parametrize('update,after', [({'member': True}, '3546.27'),
    ({'role': 'member'}, '3546.27'), ({}, '3546'),
    ({'action_price': '0'}, '0'), ({'action_price': None}, '3546.27')])
def test_custom_discount_and_member_updates_blocked(candidate, update, after):
    candidate.update(update)
    with pytest.raises(WriteError) as error:
        MutationService._validate(Op.AUTO_UPDATE, candidate, after, live=True)
    assert error.value.code == 'AUTO_RETURN_ONLY'


@pytest.mark.parametrize('currencies', [{}, {'price': 'RUB', 'action_price': None},
    {'price': 'CNY', 'action_price': 'RUB'}, {'price': 'RUB', 'action_price': 'CNY'}])
def test_missing_or_other_currency_is_not_assumed(candidate, currencies):
    candidate['money_currencies'] = currencies
    with pytest.raises(WriteError) as error:
        MutationService._validate(Op.AUTO_UPDATE, candidate, '3546.27', live=True)
    assert error.value.code == 'CURRENCY_UNKNOWN'


def test_mock_keeps_existing_arbitrary_price_workflow(candidate):
    assert MutationService._validate(Op.AUTO_UPDATE, candidate, '3500') == '3500.00'
