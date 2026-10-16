"""Fresh local review. Member removal threshold is inclusive; candidate add is strict."""
from decimal import localcontext
from .models import StrictDTO
from .discount_filter import threshold_value
from .domain import decimal_value,minimum_discount_price
from typing import Literal


class PromotionReviewQuery(StrictDTO):
    action_id: str
    role: Literal['member','candidate']
    auto_add: bool = False
    auto_add_date: str | None = None
    threshold: str


def review(items,request):
    limit=threshold_value(request.threshold)
    matches=[];unknown=0
    for item in items:
        if item.action_id!=request.action_id or item.role!=request.role or (request.auto_add and item.auto_add_at!=request.auto_add_date):
            raise ValueError('Несовпадение области просмотра')
        field='action_price' if request.role=='member' else 'max_discount_price' if request.auto_add else 'max_action_price'
        currency=item.money_currencies.get('price',item.currency)
        target_currency=item.money_currencies.get(field,item.currency)
        try:
            if request.role=='candidate' and not request.auto_add:
                # Use the same exact eligible amount as the immutable plan,
                # without rounding the displayed percentage. Missing action
                # units permit local comparison against a confirmed RUB base;
                # they remain missing and are validated again by preview/send.
                upper_fields=('price_min_elastic','max_price','max_action_price')
                units=[item.money_currencies.get(key,item.currency) for key in upper_fields if getattr(item,key) is not None]
                if not currency or any(unit and unit!=currency for unit in units):raise ValueError('Валюты не сопоставимы')
                if any(not unit for unit in units) and currency!='RUB':raise ValueError('Базовая RUB не подтверждена')
                target=decimal_value(minimum_discount_price(item.model_dump()),positive=True)
            else:
                # Ordinary removal is ID-only. A missing participation unit
                # permits local percentage selection against confirmed RUB,
                # without changing the source currency or the removal plan.
                missing_member_unit=request.role=='member' and not request.auto_add and currency=='RUB' and not target_currency
                if not currency or (currency!=target_currency and not missing_member_unit):raise ValueError('Валюта неизвестна')
                target=decimal_value(getattr(item,field),positive=True)
            base=decimal_value(item.price,positive=True)
            with localcontext() as ctx:
                ctx.prec=50
                numerator=(base-target)*100
                matched=numerator>=limit*base if request.role=='member' else numerator<limit*base
            if matched:matches.append(item)
        except ValueError:unknown+=1
    return matches,unknown
