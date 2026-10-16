"""Pure Decimal calculations. No network transport or inferred API permissions."""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext


def decimal_value(raw: str, *, positive: bool = False) -> Decimal:
    if not isinstance(raw, str) or len(raw) > 32 or not re.fullmatch(r"-?\d{1,12}(?:\.\d{1,2})?", raw):
        raise ValueError("Нужно конечное денежное значение строкой с точностью до 2 знаков")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Некорректное число") from exc
    if not value.is_finite() or (positive and value <= 0):
        raise ValueError("Цена должна быть конечной и больше нуля")
    return value


def money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), ".2f")


def signed_discount(price: str, threshold: str) -> Decimal:
    with localcontext() as context:
        context.prec = 40
        base = decimal_value(price, positive=True)
        return (base - decimal_value(threshold, positive=True)) / base * 100


def minimum_discount_price(entry: dict) -> str:
    """Highest eligible amount: the smallest discount, without percent rounding."""
    upper=[decimal_value(entry[field],positive=True) for field in
           ('price_min_elastic','max_price','max_action_price') if entry.get(field) is not None]
    if not upper:
        raise ValueError('Ozon не вернул цену для минимальной скидки участия')
    result=min(upper)
    lower=[decimal_value(entry[field],positive=True) for field in
           ('price_max_elastic','min_price') if entry.get(field) is not None]
    if lower and result<max(lower):
        raise ValueError('Денежные границы участия противоречат друг другу')
    return money(result)


def calculate_price(price: str, mode: str, value: str, rounding: str, floor: str | None,
                    minimum: str | None = None, maximum: str | None = None) -> str:
    with localcontext() as context:
        context.prec = 40
        base = decimal_value(price, positive=True)
        change = decimal_value(value)
        if mode == "percent":
            result = base * (1 - change / 100)
        elif mode == "fixed":
            result = base - change
        elif mode == "manual":
            result = change
        else:
            raise ValueError("Неизвестный способ расчёта")
        quantum = Decimal(rounding)
        if rounding not in ("0.01", "1", "10", "100"):
            raise ValueError("Неизвестный шаг округления")
        result = (result / quantum).quantize(Decimal(1), rounding=ROUND_HALF_UP) * quantum
        if floor is not None:
            result = max(result, decimal_value(floor, positive=True))
        if result <= 0 or result > Decimal("999999999999.99"):
            raise ValueError("Итоговая цена вне допустимого денежного диапазона")
        if minimum is not None and result < decimal_value(minimum, positive=True):
            raise ValueError("Цена ниже полученной денежной границы")
        if maximum is not None and result > decimal_value(maximum, positive=True):
            raise ValueError("Цена выше полученной денежной границы")
        return money(result)


def stock_value(raw: str) -> int:
    if not isinstance(raw, str) or not re.fullmatch(r"\d{1,9}", raw):
        raise ValueError("Остаток должен быть целым неотрицательным числом")
    return int(raw)


def calculate_stock(available: int | None, mode: str, raw: str) -> int:
    """Resolve a requested available stock against the freshly read quantity."""
    if type(available) is not int or not 0 <= available <= 999999999:
        raise ValueError("Доступный остаток неизвестен или вне допустимого диапазона")
    amount = stock_value(raw)
    if mode == "set":
        result = amount
    elif mode == "add":
        result = available + amount
    else:
        raise ValueError("Неизвестный способ изменения остатка")
    if result > 999999999:
        raise ValueError("Новый остаток вне допустимого диапазона")
    return result


def discount_fields(entry: dict, *, allow_unknown_currency: bool = False) -> dict[str, str | None]:
    """Informational signed percentages; monetary thresholds remain authoritative.

    Auto-add policies are independent: its min_seller_price is not a maximum
    discount. Unknown currency is strict by default. The display-only opt-in
    permits an informational ratio without assigning a currency to either field.
    """
    currencies = entry.get("money_currencies", {})
    base_currency = currencies.get("price", entry.get("currency"))

    def percentage(field):
        unit = currencies.get(field, entry.get("currency"))
        if base_currency and unit and unit != base_currency:
            return None
        if (not base_currency or not unit) and not allow_unknown_currency:
            return None
        try:
            value = signed_discount(entry.get("price"), entry.get(field))
            with localcontext() as context:
                context.prec = 50
                rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            # Avoid a misleading negative zero after rounding a small increase.
            return format(Decimal(0) if rounded == 0 else rounded, ".2f")
        except ValueError:
            return None

    auto_add = entry.get("auto_add_at") is not None
    minimum_field = next((field for field in ("price_min_elastic", "max_price", "max_action_price")
                          if entry.get(field) is not None), "price_min_elastic")
    maximum_field = "price_max_elastic" if entry.get("price_max_elastic") is not None else "min_price"
    minimum = None if auto_add else percentage(minimum_field)
    maximum = None if auto_add else percentage(maximum_field)
    # An inverted pair cannot be represented as a valid discount interval.
    if minimum is not None and maximum is not None:
        try:
            if decimal_value(entry[maximum_field], positive=True) > decimal_value(entry[minimum_field], positive=True):
                minimum = maximum = None
        except ValueError:
            minimum = maximum = None
    return dict(minimum_discount=minimum, maximum_discount=maximum,
                actual_discount=percentage("action_price") if entry.get("role") == "member" and not auto_add else None,
                auto_add_discount=percentage("action_price") if auto_add else None)
