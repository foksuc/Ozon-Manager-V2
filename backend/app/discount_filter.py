"""Pure read-only view calculation. No plan, snapshot storage, or Ozon transport."""
import re
from decimal import Decimal, localcontext
from .domain import decimal_value


def threshold_value(text: str) -> Decimal:
    value = text.strip().replace(",", ".")
    if len(value) > 32 or not re.fullmatch(r"-?\d{1,12}(?:\.\d{1,6})?", value):
        raise ValueError("Введите конечный процент, до 6 знаков после запятой")
    return Decimal(value)


def filter_members(items, threshold):
    limit = threshold_value(threshold)
    matched, unknown = [], 0
    for item in items:
        if item.role != "member":
            raise ValueError("Фильтр применяется только к участникам")
        currencies = item.money_currencies
        base_currency = currencies.get("price", item.currency)
        action_currency = currencies.get("action_price", item.currency)
        if not base_currency or base_currency != action_currency or item.price is None or item.action_price is None:
            unknown += 1
            continue
        try:
            base = decimal_value(item.price, positive=True)
            action = decimal_value(item.action_price)
            if action < 0:
                raise ValueError("Отрицательная цена")
        except ValueError:
            unknown += 1
            continue
        # Inclusive removal threshold, without rounding a recurring decimal percentage.
        with localcontext() as context:
            context.prec = 50
            if (base - action) * 100 >= limit * base:
                matched.append(item.id)
    return {"ids": matched, "matched": len(matched), "unknown": unknown, "total": len(items)}
