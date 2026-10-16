"""Volatile calculation over a newly read catalog. No storage or mutation plan."""
from decimal import Decimal
from uuid import uuid4
from .catalog import timestamp
from .domain import calculate_price, calculate_stock, money
from .models import Catalog, Preview, PreviewSource, ReadPreviewRequest


def currency(entry, field):
    return entry.money_currencies.get(field, entry.currency)


def calculate_read_preview(source: Catalog, request: ReadPreviewRequest) -> Preview:
    if source.mode != "LIVE_READ":
        raise ValueError("Расчёт требует источник LIVE_READ")
    if len(set(request.ids)) != len(request.ids):
        raise ValueError("Повторные идентификаторы запрещены")
    entries = source.products if request.kind == "prices" else source.stocks
    lookup = {entry.id: entry for entry in entries}
    if any(identifier not in lookup for identifier in request.ids):
        raise ValueError("Выбранная строка отсутствует в свежем составе. Обновите таблицу и выбор.")
    if request.kind == "prices" and any(lookup[i].action_id != request.action_id or
        lookup[i].role != request.role or (request.auto_add and lookup[i].auto_add_at != request.auto_add_date)
        for i in request.ids):
        raise ValueError("Свежий состав не соответствует выбранной акции, роли или дате")
    rows = []
    errors = [] if source.complete else ["Свежие данные загружены не полностью; результат нельзя считать проверенным."]
    for identifier in request.ids:
        entry = lookup[identifier]
        before = entry.action_price if request.kind == "prices" and entry.role == "member" else entry.price if request.kind == "prices" else (str(entry.available) if entry.available is not None else None)
        after = diff = error = None
        unit = currency(entry, "action_price" if entry.role == "member" else "price") if request.kind == "prices" else None
        try:
            if before is None:
                raise ValueError("Исходное значение отсутствует; ноль не подставляется")
            if request.kind == "stocks":
                after = str(calculate_stock(entry.available, request.stock_mode, request.value))
                diff = str(int(after) - int(before))
            else:
                if unit != "RUB" or currency(entry, "price") != unit:
                    raise ValueError("Валюта неизвестна или отличается от RUB; денежный расчёт заблокирован")
                if entry.elastic and (entry.min_price is None or entry.max_price is None):
                    raise ValueError("Для Elastic отсутствует денежная граница; расчёт заблокирован")
                for field in ("min_price", "max_price", "max_action_price"):
                    if getattr(entry, field) is not None and currency(entry, field) != unit:
                        raise ValueError("Валюта ценового ограничения неизвестна или не совпадает")
                after = calculate_price(entry.price, request.mode, request.value.replace(",", "."), request.rounding,
                                        request.floor.replace(",", ".") if request.floor else None,
                                        entry.min_price, entry.max_price)
                if entry.max_action_price is not None and Decimal(after) > Decimal(entry.max_action_price):
                    raise ValueError("Цена выше максимальной цены для участия в акции")
                diff = money(Decimal(after) - Decimal(before))
        except ValueError as exc:
            error = str(exc)
            after = diff = None
            errors.append(error)
        rows.append(dict(id=identifier, offer_id=entry.offer_id, name=entry.name, before=before,
                         after=after, diff=diff, error=error, currency=unit))
    # UUID is display identity only. Empty hash cannot identify a stored executable plan.
    snapshot = PreviewSource(mode="LIVE_READ", scenario="ready", fetched_at=source.fetched_at,
        action_id=request.action_id, action=next((a for a in source.actions if a.id == request.action_id), None),
        items=[lookup[i] for i in request.ids])
    return Preview(id=str(uuid4()), hash="", kind=request.kind, created_at=timestamp(),
        complete=source.complete, valid=not errors, source=snapshot, rows=rows, errors=list(dict.fromkeys(errors)))
