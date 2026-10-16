"""In-memory Seller read mapping. No writes, credentials or MOCK fallback."""
import asyncio
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from .ozon_read import ReadError, ReadOperation as Op
from .domain import discount_fields

MAX_PAGES = 500
MAX_ITEMS = 50_000
DEADLINE_SECONDS = 120
TRANSITION = datetime(2026, 10, 13, tzinfo=timezone.utc)


def boost_value(raw):
    if raw is None or isinstance(raw, bool):
        return None
    try:
        amount = Decimal(str(raw))
        return format(amount, 'f') if amount.is_finite() else None
    except (ValueError, InvalidOperation):
        return None


def identifier(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ReadError("SCHEMA", "Некорректная структура идентификатора")
    raw = str(value)
    if not re.fullmatch(r"[0-9]{1,20}", raw) or not 0 < int(raw) <= 2**64 - 1:
        raise ReadError("SCHEMA", "Некорректная структура идентификатора")
    return str(int(raw))


def date_value(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


class State:
    def __init__(self):
        self.complete = True
        self.warnings = []
        self.currency_assumptions = 0

    def warn(self, message, *, incomplete=True):
        if message not in self.warnings:
            self.warnings.append(message)
        if incomplete:
            self.complete = False


def count(value):
    return value if type(value) is int and value >= 0 else None


def money_value(value, state, *, currency=None, allow_unknown_currency=False):
    if value is None:
        return None, None
    if isinstance(value, dict):
        currency = value.get("currency")
        value = value.get("amount")
        if currency in (None, "") and datetime.now(timezone.utc) < TRANSITION:
            currency = "RUB"
            state.currency_assumptions += 1
            state.warn("RUB задана пользователем временно", incomplete=False)
        elif currency in (None, "") and allow_unknown_currency:
            currency = None
        elif currency != "RUB":
            state.warn("Денежное значение с неподтверждённой или неподдерживаемой валютой не показано")
            return None, currency if isinstance(currency, str) else None
    elif currency in (None, ""):
        if datetime.now(timezone.utc) >= TRANSITION and not allow_unknown_currency:
            state.warn("Scalar-цена после перехода 13.10 не соответствует подтверждённой v2-схеме")
            return None, None
        if datetime.now(timezone.utc) < TRANSITION:
            currency = "RUB"
            state.currency_assumptions += 1
            state.warn("RUB задана пользователем временно", incomplete=False)
        else:
            currency = None
    elif currency != "RUB":
        state.warn("Цена с неподдерживаемой валютой не показана")
        return None, currency if isinstance(currency, str) else None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        state.warn("Некорректная структура денежного значения")
        return None, None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > Decimal("999999999999.99") or amount != amount.quantize(Decimal("0.01")):
            raise InvalidOperation
        return format(amount, ".2f"), currency
    except (InvalidOperation, ValueError):
        state.warn("Некорректное денежное значение не показано")
        return None, None


def sku_value(card, state):
    if card.get("sku") is not None:
        try:
            return identifier(card["sku"])
        except ReadError:
            state.warn("Некорректный SKU карточки")
            return None
    sources = card.get("sources")
    if not isinstance(sources, list):
        return None
    values = set()
    for source in sources:
        if not isinstance(source, dict) or source.get("sku") is None:
            continue
        try:
            values.add(identifier(source["sku"]))
        except ReadError:
            state.warn("Некорректный SKU источника карточки")
    if len(values) > 1:
        state.warn("Несколько SKU в источниках карточки: выбор неоднозначен")
    return next(iter(values)) if len(values) == 1 else None


class LiveCatalog:
    def __init__(self, client):
        self.client = client

    async def pages(self, op, payload, array, state, key, *, cursor="last_id", offset=False, required=True, nested=False, receive=None):
        result, seen, cursors = [], set(), set()
        expected_total = None
        payload = dict(payload)
        for _ in range(MAX_PAGES):
            try:
                body = await self.client.read(op, payload)
                if not isinstance(body, dict):
                    raise ReadError("SCHEMA", "Ожидался объект ответа чтения")
                if nested:
                    body = body.get("result")
                elif op in (Op.ACTION_PRODUCTS, Op.ACTION_CANDIDATES) and isinstance(body.get("result"), dict) and array not in body:
                    body = body["result"]
                if not isinstance(body, dict) or not isinstance(body.get(array), list):
                    raise ReadError("SCHEMA", "В ответе чтения отсутствует обязательный список")
                page = body[array]
            except ReadError:
                if required and not result:
                    raise
                state.warn("Чтение страницы прервано; показаны только полученные строки")
                break
            raw_total = body.get("total_items", body.get("total"))
            invalid_total = False
            if raw_total is not None:
                total = count(raw_total)
                if total is None or total > MAX_ITEMS:
                    state.warn("Недопустимое общее количество строк")
                    invalid_total = True
                elif expected_total is not None and total != expected_total:
                    state.warn("Общее количество изменилось во время чтения")
                if total is not None:
                    expected_total = total
            for row in page:
                try:
                    if not isinstance(row, dict):
                        raise ReadError("SCHEMA", "Некорректная строка списка")
                    identity = key(row)
                    if identity in seen:
                        state.warn("Повторные идентификаторы на страницах; полнота не подтверждена")
                        continue
                    seen.add(identity)
                    if receive is not None and receive(row) is False:
                        return result
                    result.append(row)
                except ReadError:
                    state.warn("Строка с некорректным идентификатором пропущена")
                if len(result) >= MAX_ITEMS:
                    state.warn("Достигнут предел количества строк")
                    return result
            if expected_total is not None and len(result) > expected_total:
                state.warn("Получено больше строк, чем указано в total")
            has_next = body.get("has_next")
            if op in (Op.STOCKS, Op.WAREHOUSES) and type(has_next) is not bool:
                state.warn("Отсутствует или некорректен признак полноты страниц складского чтения")
                break
            if "has_next" in body and type(has_next) is not bool:
                state.warn("Некорректный признак следующей страницы")
                break
            if invalid_total:
                break
            if not page or len(page) < payload["limit"]:
                if expected_total is not None and len(result) < expected_total:
                    state.warn("Короткая или пустая страница при незавершённом total")
                    break
            if expected_total is not None and len(result) >= expected_total:
                if has_next is True:
                    state.warn("has_next противоречит достигнутому total; полнота не подтверждена")
                break
            if offset:
                if len(page) < payload["limit"]:
                    break
                payload["offset"] = payload.get("offset", 0) + payload["limit"]
                continue
            if has_next is False:
                if expected_total is not None and len(result) < expected_total:
                    state.warn("Следующая страница отсутствует при незавершённом total")
                break
            next_cursor = body.get(cursor)
            if next_cursor in (None, ""):
                if has_next is True or len(page) >= payload["limit"]:
                    state.warn("Нет cursor для продолжения полной страницы")
                break
            if not isinstance(next_cursor, str) or next_cursor in cursors or next_cursor == payload.get(cursor):
                state.warn("Cursor не продвигается; чтение остановлено")
                break
            if not page:
                state.warn("Пустая страница с cursor продолжения")
                break
            cursors.add(next_cursor)
            payload[cursor] = next_cursor
        else:
            state.warn("Достигнут предел числа страниц")
        return result

    async def actions(self, state):
        body = await self.client.read(Op.ACTIONS)
        if not isinstance(body, dict) or not isinstance(body.get("result"), list):
            raise ReadError("SCHEMA", "В списке акций отсутствует обязательный массив")
        rows, seen = [], set()
        for raw in body["result"][:MAX_ITEMS]:
            try:
                if not isinstance(raw, dict):
                    raise ReadError("SCHEMA", "Некорректная строка акции")
                aid = identifier(raw.get("id"))
            except ReadError:
                state.warn("Акция с некорректным идентификатором пропущена")
                continue
            if aid in seen:
                state.warn("Повторный идентификатор акции")
                continue
            seen.add(aid)
            dates = raw.get("auto_add_dates", [])
            if not isinstance(dates, list):
                state.warn("Список дат автодобавления не подтверждён")
                dates = []
            else:
                confirmed_dates = [date for date in dates if date_value(date) is not None]
                if len(confirmed_dates) != len(dates):
                    state.warn("Некорректные даты автодобавления исключены; сохранены подтверждённые даты")
                dates = confirmed_dates
            start, end = date_value(raw.get("date_start")), date_value(raw.get("date_end"))
            now = datetime.now(timezone.utc)
            status = "unknown"
            if start and end and start <= end:
                status = "upcoming" if now < start else "ended" if now > end else "active"
            else:
                state.warn("Статус акции неизвестен: даты не подтверждены", incomplete=False)
            voucher=raw.get('is_voucher_action')
            freeze=raw.get('freeze_date')
            freeze_parsed=date_value(freeze) if freeze not in (None,'') else None
            if voucher is not None and type(voucher) is not bool:
                state.warn('Тип акции не подтверждён')
                voucher=None
            if freeze not in (None,'') and freeze_parsed is None:state.warn('Дата заморозки акции не подтверждена')
            rows.append(dict(id=aid, name=raw.get("title") if isinstance(raw.get("title"), str) else "Название отсутствует",
                             is_voucher_action=voucher,freeze_date=freeze_parsed.isoformat() if freeze_parsed else None,
                             auto_add=bool(dates), auto_add_dates=list(dict.fromkeys(dates)), status=status,
                             start_at=start.isoformat() if start else None, end_at=end.isoformat() if end else None,
                             member_count=count(raw.get("participating_products_count")), candidate_count=count(raw.get("potential_products_count"))))
        if len(body["result"]) > MAX_ITEMS:
            state.warn("Достигнут предел количества акций")
        return rows

    async def cards(self, ids, state):
        cards = {}
        for start in range(0, len(ids), 1000):
            batch = ids[start:start + 1000]
            try:
                response = await self.client.read(Op.PRODUCT_INFO, {"product_id": batch})
                if not isinstance(response, dict) or not isinstance(response.get("items"), list):
                    raise ReadError("SCHEMA", "Нет списка карточек")
                for card in response["items"]:
                    if not isinstance(card, dict):
                        raise ReadError("SCHEMA", "Некорректная карточка")
                    pid = identifier(card.get("id"))
                    if pid not in batch or pid in cards:
                        state.warn("Неожиданная или повторная карточка пропущена")
                        continue
                    cards[pid] = card
            except ReadError:
                state.warn("Карточки получены не полностью")
        if any(pid not in cards for pid in ids):
            state.warn("Для части товаров отсутствуют карточки")
        return cards

    async def prices(self, ids, state):
        prices = {}
        for start in range(0, len(ids), 1000):
            batch = ids[start:start + 1000]
            rows = await self.pages(Op.PRICES, {"filter": {"product_id": batch, "visibility": "ALL"}, "limit": 1000, "cursor": ""},
                                    "items", state, lambda row: identifier(row.get("product_id")), cursor="cursor", required=False)
            for row in rows:
                pid = identifier(row["product_id"])
                if pid in batch:
                    prices[pid] = row
                else:
                    state.warn("Неожиданная строка цены пропущена")
        return prices

    async def products(self, action, role, auto_add, auto_add_date, state, output):
        if auto_add and (not auto_add_date or auto_add_date not in action["auto_add_dates"]):
            raise ReadError("VALIDATION", "Выберите точную дату автодобавления из акции", status=422)
        if not auto_add and auto_add_date is not None:
            raise ReadError("VALIDATION", "Дата относится только к автодобавлению", status=422)
        op = (Op.AUTO_PRODUCTS if role == "member" else Op.AUTO_CANDIDATES) if auto_add else (Op.ACTION_PRODUCTS if role == "member" else Op.ACTION_CANDIDATES)
        payload = {"action_id": int(action["id"]), "limit": 100}
        if auto_add:
            payload.update(auto_add_date=auto_add_date, offset=0)
        else:
            payload["last_id"] = ""
        id_field = "product_id" if auto_add and role == "member" else "id"
        def receive(raw):
            pid = identifier(raw[id_field])
            assumptions_before = state.currency_assumptions
            assumed_fields = []
            def field_money(field, value, **kwargs):
                before = state.currency_assumptions
                result = money_value(value, state, allow_unknown_currency=True, **kwargs)
                if state.currency_assumptions > before:
                    assumed_fields.append(field)
                return result
            base, currency = field_money("price", raw.get("price"))
            values = {field: field_money(field, raw.get(field)) for field in
                      ("price_max_elastic", "price_min_elastic", "min_seller_price", "max_discount_price", "max_action_price")}
            low_raw, high_raw = values["price_max_elastic"][0], values["price_min_elastic"][0]
            seller, discount, max_action = values["min_seller_price"][0], values["max_discount_price"][0], values["max_action_price"][0]
            action_price, action_currency = field_money("action_price", raw.get("action_price_to_auto_add") if auto_add else raw.get("action_price"))
            currencies = {field: pair[1] for field, pair in values.items()}
            currencies.update(price=currency, action_price=action_currency,
                              min_price=None if auto_add else values["price_max_elastic"][1],
                              max_price=None if auto_add else values["price_min_elastic"][1])
            # Auto-add thresholds describe separate policies, not an ordered range.
            low, high = (None, None) if auto_add else (low_raw, high_raw)
            if not auto_add:
                if "price_max_elastic" in assumed_fields:
                    assumed_fields.append("min_price")
                if "price_min_elastic" in assumed_fields:
                    assumed_fields.append("max_price")
            if (low_raw is None) != (high_raw is None):
                state.warn("Денежный диапазон Elastic получен не полностью")
            if low is not None and high is not None and Decimal(low) > Decimal(high):
                state.warn("Денежные границы инвертированы; диапазон не подтверждён")
            output.append(dict(id=f"{action['id']}:{role}:{auto_add_date or 'ordinary'}:{pid}", action_id=action["id"],
                               product_id=pid, sku=sku_value(raw, state),
                               offer_id=raw.get("offer_id") if isinstance(raw.get("offer_id"), str) else "",
                               name=raw.get("name") if isinstance(raw.get("name"), str) and raw["name"] else "Название отсутствует", role=role, price=base,
                               currency=currency, currency_assumed=state.currency_assumptions > assumptions_before,
                               money_currencies=currencies, money_assumed_fields=assumed_fields,
                               action_price=action_price, min_price=low, max_price=high,
                               current_boost=boost_value(raw.get("current_boost")) if role == "member" and not auto_add else None, elastic=(True if low_raw is not None or high_raw is not None else None), auto_add_at=auto_add_date if auto_add else None,
                               is_voucher_action=action.get('is_voucher_action'),freeze_date=action.get('freeze_date'),
                               price_min_elastic=high_raw, price_max_elastic=low_raw, max_discount_price=discount, min_seller_price=seller,
                               max_action_price=max_action))
        rows = await self.pages(op, payload, "products", state, lambda row: identifier(row.get(id_field)), offset=auto_add,
                                nested=False, receive=receive)
        ids = [identifier(row[id_field]) for row in rows]
        cards = await self.cards(ids, state)
        price_rows = await self.prices(ids, state)
        for row in output:
            card = cards.get(row["product_id"], {})
            price_row = price_rows.get(row["product_id"], {})
            card_details=price_row.get('price')
            card_details=card_details if isinstance(card_details,dict) else {}
            card_unit=card_details.get('currency_code')
            # This is the documented card limit without promotions, not an
            # action-price substitute. Never turn a missing card unit into RUB.
            card_amount,_=money_value(card_details.get('price'),state,currency=card_unit) if card_unit not in (None,'') else (None,None)
            row['card_price']=card_amount
            row['card_currency']=card_unit if isinstance(card_unit,str) and card_unit else None
            if not row["offer_id"]:
                row["offer_id"] = card.get("offer_id") if isinstance(card.get("offer_id"), str) and card["offer_id"] else price_row.get("offer_id") if isinstance(price_row.get("offer_id"), str) else ""
            if row["name"] == "Название отсутствует" and isinstance(card.get("name"), str) and card["name"]:
                row["name"] = card["name"]
            if row["sku"] is None:
                row["sku"] = sku_value(card, state)
            if "price" in row["money_assumed_fields"]:
                # The catalog price carries its own explicit currency. It can
                # confirm the base only; action prices/bounds remain independent.
                details = price_row.get("price")
                if isinstance(details, dict) and details.get("currency_code") not in (None, ""):
                    candidate, currency = money_value(details.get("price"), state, currency=details["currency_code"])
                    if candidate is not None or details["currency_code"] != "RUB":
                        row["price"], row["currency"] = candidate, currency
                        row["money_currencies"]["price"] = currency
                        row["money_assumed_fields"].remove("price")
                        row["currency_assumed"] = bool(row["money_assumed_fields"])
            if row["currency"] in (None, "") or (row["price"] is None and row["currency"] == "RUB"):
                assumptions_before = state.currency_assumptions
                details = price_row.get("price")
                details = details if isinstance(details, dict) else {}
                candidate, currency = money_value(details.get("price"), state, currency=details.get("currency_code"), allow_unknown_currency=True)
                if candidate is None and currency in (None, "RUB"):
                    candidate, currency = money_value(card.get("price"), state, currency=card.get("currency_code"), allow_unknown_currency=True)
                # Prefer an independently confirmed base. If enrichment has no
                # value, retain the valid unlabelled amount returned by actions.
                if candidate is not None or currency not in (None, "", "RUB"):
                    row["price"], row["currency"] = candidate, currency
                    row["money_currencies"]["price"] = currency
                row["currency_assumed"] = row["currency_assumed"] or state.currency_assumptions > assumptions_before
                if state.currency_assumptions > assumptions_before:
                    row["money_assumed_fields"].append("price")
            if not row["offer_id"] or row["price"] is None:
                state.warn("Для части товаров не подтверждён артикул или базовая цена")
            if any(row.get(field) is not None and currency is None
                   for field, currency in row["money_currencies"].items()):
                state.warn("Для части цен Ozon не указал валюту: суммы показаны без неё, скидки рассчитаны исходя из одной валюты", incomplete=False)
            # User-authorized informational ratio only. Currency metadata stays
            # unknown; mutation validation and removal thresholds remain strict.
            row.update(discount_fields(row, allow_unknown_currency=True))

    async def stocks(self, state, output):
        rows = await self.pages(Op.PRODUCT_LIST, {"filter": {"visibility": "ALL"}, "last_id": "", "limit": 1000}, "items", state,
                                lambda row: identifier(row.get("product_id")), nested=True)
        ids = [identifier(row["product_id"]) for row in rows]
        cards = await self.cards(ids, state)
        warehouses = await self.pages(Op.WAREHOUSES, {"limit": 200, "cursor": ""}, "warehouses", state,
                                      lambda row: identifier(row.get("warehouse_id")), cursor="cursor", required=False)
        names = {identifier(row["warehouse_id"]): row["name"] if isinstance(row.get("name"), str) else "Название склада отсутствует" for row in warehouses}
        catalog_by_id = {identifier(row["product_id"]): row for row in rows}
        offers, skus = [], []
        for row, pid in zip(rows, ids):
            offer = row.get("offer_id")
            if isinstance(offer, str) and offer:
                offers.append(offer)
            else:
                card = cards.get(pid, {})
                offer = card.get("offer_id")
                if isinstance(offer, str) and offer:
                    offers.append(offer)
                else:
                    sku = sku_value(card, state)
                    if sku:
                        skus.append(sku)
                    else:
                        state.warn("Для части каталога отсутствует допустимый selector остатков")
        seen = set()
        for selector, values in (("offer_id", list(dict.fromkeys(offers))), ("sku", list(dict.fromkeys(skus)))):
            for start in range(0, len(values), 1000):
                def receive(raw):
                    pid, wid = identifier(raw["product_id"]), identifier(raw["warehouse_id"])
                    if pid not in catalog_by_id:
                        state.warn("Строка остатков вне прочитанного каталога пропущена")
                        return
                    key = f"{pid}:{wid}"
                    if key in seen:
                        state.warn("Повторная строка склада между batches")
                        return
                    if len(output) >= MAX_ITEMS:
                        state.warn("Достигнут предел количества строк остатков")
                        return False
                    seen.add(key)
                    card = cards.get(pid, {})
                    offer = raw.get("offer_id") or card.get("offer_id") or catalog_by_id[pid].get("offer_id")
                    sku = sku_value(raw, state) or sku_value(card, state)
                    output.append(dict(id=key, product_id=pid, sku=sku, offer_id=offer if isinstance(offer, str) else "",
                                       name=card.get("name") if isinstance(card.get("name"), str) else "Название отсутствует",
                                       warehouse_id=wid, warehouse_name=raw.get("warehouse_name") if isinstance(raw.get("warehouse_name"), str) else names.get(wid, "Название склада отсутствует"),
                                       available=count(raw.get("free_stock")), reserved=count(raw.get("reserved")), present=count(raw.get("present"))))
                    if any(output[-1][field] is None for field in ("available", "reserved", "present")):
                        state.warn("Часть количественных полей остатков отсутствует или некорректна")
                await self.pages(Op.STOCKS, {selector: values[start:start + 1000], "limit": 1000, "cursor": ""}, "products", state,
                                 lambda row: (identifier(row.get("product_id")), identifier(row.get("warehouse_id"))), cursor="cursor", required=False, receive=receive)
                if len(output) >= MAX_ITEMS:
                    state.warn("Достигнут предел количества строк остатков")
                    return

    async def catalog(self, scope="actions", action_id=None, role="member", auto_add=False, auto_add_date=None):
        if scope not in ("actions", "products", "stocks") or role not in ("member", "candidate") or type(auto_add) is not bool:
            raise ReadError("VALIDATION", "Некорректная область чтения", status=422)
        state = State()
        result = dict(mode="LIVE_READ", fetched_at=datetime.now(timezone.utc).isoformat(), complete=True, warnings=[], actions=[], products=[], stocks=[])
        try:
            async with asyncio.timeout(DEADLINE_SECONDS):
                if scope == "stocks":
                    await self.stocks(state, result["stocks"])
                else:
                    result["actions"] = await self.actions(state)
                    if scope == "products":
                        try:
                            aid = identifier(action_id)
                        except ReadError as exc:
                            raise ReadError("VALIDATION", "Некорректный идентификатор выбранной акции", status=422) from exc
                        action = next((action for action in result["actions"] if action["id"] == aid), None)
                        if action is None:
                            raise ReadError("VALIDATION", "Выбранная акция отсутствует в полученном списке", status=422)
                        await self.products(action, role, auto_add, auto_add_date, state, result["products"])
        except TimeoutError:
            state.warn("Превышено время чтения; результат неполный, сохранены только уже полученные строки")
        result["complete"], result["warnings"] = state.complete, state.warnings
        return result
