from decimal import Decimal
import pytest
from app.models import Product
from app.promotion_review import PromotionReviewQuery,review


def item(role='member',**updates):
    data=dict(id='synthetic',action_id='1',offer_id='SYN',name='Synthetic',role=role,price='100',action_price='95',min_price=None,max_price=None,elastic=False,auto_add_at=None,max_action_price='95',max_discount_price='95')
    data.update(updates);return Product(**data)
def request(role='member',threshold='5',**changes):return PromotionReviewQuery(action_id='1',role=role,threshold=threshold,**changes)


def test_inclusive_member_and_strict_candidate_boundaries():
    assert len(review([item()],request())[0])==1
    assert not review([item(action_price='95.001')],request())[0]
    assert len(review([item()],request(threshold='4.999999'))[0])==1
    assert not review([item('candidate')],request('candidate'))[0]
    assert len(review([item('candidate')],request('candidate','5.000001'))[0])==1


def test_unknown_currency_or_absent_target_not_zero():
    assert review([item(money_currencies={'price':None,'action_price':None})],request())[1]==1
    assert review([item(action_price=None)],request())[1]==1
    assert review([item('candidate',max_action_price=None)],request('candidate'))[1]==1


def test_auto_candidate_uses_independent_max_discount_policy():
    date='2026-10-13T21:00:00Z'
    entry=item('candidate',auto_add_at=date,min_seller_price='200',max_discount_price='96')
    assert review([entry],request('candidate','5',auto_add=True,auto_add_date=date))[0]==[entry]


def test_wrong_scope_not_mixed():
    with pytest.raises(ValueError):review([item(action_id='2')],request())


@pytest.mark.parametrize('amount,matched',[('95.00',True),('94.99',True),('95.01',False),('100',False),('120.19',False)])
def test_member_threshold_with_missing_participation_unit_is_inclusive_and_preserves_source(amount,matched):
    entry=item(action_price=amount,money_currencies={'price':'RUB','action_price':None})
    original=entry.model_dump()
    matches,unknown=review([entry],request())
    assert bool(matches) is matched and unknown==0
    assert entry.model_dump()==original


@pytest.mark.parametrize('changes',[
    {'money_currencies':{'price':'RUB','action_price':'USD'}},
    {'money_currencies':{'price':'USD','action_price':None}},
    {'money_currencies':{'price':None,'action_price':None}},
    {'price':'0'}, {'action_price':'0'}, {'action_price':'95.001'},
])
def test_member_missing_or_conflicting_data_remains_unknown(changes):
    assert review([item(**changes)],request())==([],1)


def test_auto_member_missing_participation_unit_remains_unknown():
    date='2026-10-13T21:00:00Z'
    entry=item(auto_add_at=date,money_currencies={'price':'RUB','action_price':None})
    assert review([entry],request(auto_add=True,auto_add_date=date))==([],1)


@pytest.mark.parametrize('amount',['120.19','100.00','99.97','95.01'])
def test_ordinary_candidates_with_missing_bound_currency_are_compared_without_assumption(amount):
    entry=item('candidate',max_action_price=None,max_price=amount,price_min_elastic=amount,
        money_currencies={'price':'RUB','max_price':None,'price_min_elastic':None})
    original=entry.model_dump()
    matches,unknown=review([entry],request('candidate','5'))
    assert matches==[entry] and unknown==0
    assert entry.model_dump()==original and entry.money_currencies['max_price'] is None


@pytest.mark.parametrize('amount,matched',[('95.00',False),('94.99',False),('95.01',True)])
def test_candidate_threshold_is_strict_and_uses_exact_vendor_amount(amount,matched):
    matches,unknown=review([item('candidate',max_price=amount,max_action_price=None)],request('candidate','5'))
    assert bool(matches) is matched and unknown==0


def test_conflicting_known_upper_limits_use_same_restrictive_plan_price():
    entry=item('candidate',max_price='99.97',max_action_price='94.00')
    assert review([entry],request('candidate','5'))==([],0)


@pytest.mark.parametrize('changes',[
    {'money_currencies':{'price':'RUB','max_action_price':'USD'}},
    {'money_currencies':{'price':None,'max_action_price':None}},
    {'money_currencies':{'price':'USD','max_action_price':None}},
    {'price':'0'}, {'min_price':'96'},
])
def test_missing_base_or_conflicting_data_is_unknown_not_no_matching_goods(changes):
    assert review([item('candidate',**changes)],request('candidate'))==([],1)
