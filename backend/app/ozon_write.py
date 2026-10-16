"""Separate closed write transport. One send; no retries or redirect/proxy support."""
from enum import Enum
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import asyncio
import re
import time
import json
import httpx
from .ozon_read import BASE_URL, identifier


class WriteOperation(Enum):
    PRICE = ("prices", "/v1/product/import/prices", 1000)
    STOCK = ("stocks", "/v2/products/stocks", 100)
    ACTION_UPDATE = ("action_add", "/v1/actions/products/update", 1000)
    ACTION_REMOVE = ("action_remove", "/v2/actions/products/deactivate", 1000)
    AUTO_UPDATE = ("auto_add", "/v2/actions/auto-add/products/update", 1000)
    AUTO_REMOVE = ("auto_remove", "/v2/actions/auto-add/products/delete", 1000)

    @classmethod
    def parse(cls, value):
        for member in cls:
            if value == member.value[0] or value == member.name:
                return member
        raise WriteError("INVALID_OPERATION", "Неизвестная операция", 422)


class WriteError(Exception):
    def __init__(self, code, message, status=502, *, vendor_error=None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.vendor_error = vendor_error


# Current rendered official docs checked09.10; conservative early cutoff because
# the published13.10 transition has no stated timezone. Recheck at every send.
AUTO_REMOVE_VALID_UNTIL = datetime(2026, 10, 12, tzinfo=timezone.utc)
LEGACY_VALID_UNTIL = AUTO_REMOVE_VALID_UNTIL
LEGACY_PROFILE = "LEGACY_2026_10_09"
FUTURE_PROFILE = "V2_2026_10_13"
FUTURE_VALID_FROM = datetime(2026, 10, 13, tzinfo=timezone.utc)
ACTION_OPERATIONS = (WriteOperation.ACTION_UPDATE, WriteOperation.ACTION_REMOVE, WriteOperation.AUTO_UPDATE, WriteOperation.AUTO_REMOVE)
LEGACY_PATHS = {
    WriteOperation.ACTION_UPDATE: "/v1/actions/products/activate",
    WriteOperation.ACTION_REMOVE: "/v1/actions/products/deactivate",
    WriteOperation.AUTO_UPDATE: "/v1/actions/auto-add/products/update",
}


def _utc_now():
    return datetime.now(timezone.utc)


def contract_profile(op, *, mock=False):
    if op not in ACTION_OPERATIONS:
        return "STABLE"
    return FUTURE_PROFILE if mock or _utc_now() >= LEGACY_VALID_UNTIL else LEGACY_PROFILE


def contract_path(op, profile):
    return LEGACY_PATHS.get(op, op.value[1]) if profile == LEGACY_PROFILE else op.value[1]


def blocked_reason(op, profile=None):
    if op in ACTION_OPERATIONS:
        if profile == LEGACY_PROFILE and _utc_now() >= LEGACY_VALID_UNTIL:
            return 'Проверенный период сохранённого профиля API завершён; подготовьте новый план'
        if _utc_now() >= LEGACY_VALID_UNTIL:
            if _utc_now() < FUTURE_VALID_FROM:
                return 'Проверенный период старого API завершён; новый профиль доступен с 13.10.2026'
            # AUTO_UPDATE is restricted by the mutation service to returning a
            # candidate at Ozon's exact scheduled price. Readback verifies the
            # schedule; it does not claim that a future card change occurred.
            return None
        return None
    # Stock keeps the working V1 application contract: one selector, warehouse,
    # free stock. The snapshot's orphan required quant_size is not a wire field.
    return None


def money(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,12}(\.[0-9]{1,2})?", value):
        raise WriteError("VALIDATION", "Цена должна быть положительной денежной строкой", 422)
    try:
        parsed = Decimal(value)
        if not parsed.is_finite() or parsed <= 0:
            raise InvalidOperation
        return format(parsed, ".2f")
    except InvalidOperation:
        raise WriteError("VALIDATION", "Некорректная цена", 422) from None


def validate_request(op, body, *, profile=FUTURE_PROFILE):
    def invalid():
        raise WriteError("VALIDATION", "Некорректный контракт запроса изменения", 422)
    if type(op) is not WriteOperation or type(body) is not dict or profile not in {"STABLE", LEGACY_PROFILE, FUTURE_PROFILE}:
        invalid()
    legacy = profile == LEGACY_PROFILE
    key = "prices" if op is WriteOperation.PRICE else "stocks" if op is WriteOperation.STOCK else "product_ids" if op in (WriteOperation.ACTION_REMOVE, WriteOperation.AUTO_REMOVE) else "to_update" if legacy and op is WriteOperation.AUTO_UPDATE else "products"
    allowed = {key} | ({"action_id"} if op not in (WriteOperation.PRICE, WriteOperation.STOCK) else set()) | ({"auto_add_date"} if op in (WriteOperation.AUTO_UPDATE, WriteOperation.AUTO_REMOVE) else set())
    if set(body) != allowed or not isinstance(body[key], list) or not 1 <= len(body[key]) <= op.value[2]:
        invalid()
    if "action_id" in body and (type(body["action_id"]) is not int or not identifier(body["action_id"])):
        invalid()
    if legacy and op in (WriteOperation.ACTION_UPDATE, WriteOperation.ACTION_REMOVE) and body["action_id"] > 2**53-1:
        invalid()
    if "auto_add_date" in body:
        from datetime import datetime
        try:
            date = body["auto_add_date"]
            if not isinstance(date, str) or len(date) > 64 or datetime.fromisoformat(date.replace("Z", "+00:00")).tzinfo is None:
                invalid()
        except ValueError:
            invalid()
    identities = []
    for row in body[key]:
        if key == "product_ids":
            expected_type = int if legacy and op is WriteOperation.ACTION_REMOVE else str
            if not identifier(row) or type(row) is not expected_type or (expected_type is int and row > 2**53-1):
                invalid()
            identities.append(str(row))
            continue
        if type(row) is not dict:
            invalid()
        if op is WriteOperation.PRICE:
            if set(row) != {"product_id", "price", "currency_code"} or row["currency_code"] != "RUB":
                invalid()
            money(row["price"])
        elif op is WriteOperation.STOCK:
            selector = 'offer_id' if 'offer_id' in row else 'product_id'
            if set(row) != {selector, "warehouse_id", "stock"} or not identifier(row["warehouse_id"]) or type(row["warehouse_id"]) is not int or row["warehouse_id"] > 2**63 - 1 or type(row["stock"]) is not int or not 0 <= row["stock"] <= 2**63 - 1:
                invalid()
            if selector == 'offer_id':
                if type(row['offer_id']) is not str or not row['offer_id']:
                    invalid()
                identities.append((selector, row[selector], row['warehouse_id']))
                continue
        elif legacy and op is WriteOperation.ACTION_UPDATE:
            if set(row) != {"product_id", "action_price"}:
                invalid()
            money(row["action_price"])
            if type(row["product_id"]) is not int or row["product_id"] > 2**53-1:
                invalid()
        elif legacy and op is WriteOperation.AUTO_UPDATE:
            if set(row) != {"product_id", "action_price", "currency"} or row["currency"] != "RUB":
                invalid()
            money(row["action_price"])
        else:
            product_key = "id" if op is WriteOperation.AUTO_UPDATE else "product_id"
            if set(row) != {product_key, "action_price"} or type(row["action_price"]) is not dict or set(row["action_price"]) != {"amount", "currency"} or row["action_price"]["currency"] != "RUB":
                invalid()
            money(row["action_price"]["amount"])
        product = row.get("product_id", row.get("id"))
        if not identifier(product) or type(product) is not int:
            invalid()
        if op in (WriteOperation.PRICE, WriteOperation.STOCK) and product > 2**63 - 1:
            invalid()
        if op is WriteOperation.STOCK and row["warehouse_id"] > 2**63 - 1:
            invalid()
        identities.append((str(product), row.get("warehouse_id")))
    if len(set(identities)) != len(identities):
        invalid()


def request_content(op, body, profile):
    """Legacy prices become exact JSON numbers only at the wire boundary."""
    if profile != LEGACY_PROFILE or op not in (WriteOperation.ACTION_UPDATE, WriteOperation.AUTO_UPDATE):
        return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    key = "to_update" if op is WriteOperation.AUTO_UPDATE else "products"
    wire = {**body, key: [{**row, "action_price": Decimal(row["action_price"])} for row in body[key]]}
    def encode(value):
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, dict):
            return "{" + ",".join(json.dumps(key) + ":" + encode(item) for key, item in value.items()) + "}"
        if isinstance(value, list):
            return "[" + ",".join(map(encode, value)) + "]"
        return json.dumps(value, ensure_ascii=False)
    return encode(wire).encode("utf-8")


class WriteClient:
    def __init__(self, credentials, *, transport=None):
        self._credentials = credentials
        self._http = httpx.AsyncClient(base_url=BASE_URL, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(12, connect=5), transport=transport)
        self._lock, self._last = asyncio.Lock(), 0.0

    async def close(self):
        await self._http.aclose()

    async def send(self, operation, body):
        profile = contract_profile(operation)
        validate_request(operation, body, profile=profile)
        if blocked_reason(operation):
            raise WriteError("CONTRACT_BLOCKED", blocked_reason(operation), 409)
        if self._credentials is None:
            raise WriteError("NOT_CONFIGURED", "Выберите настроенный кабинет", 409)
        async with self._lock:
            delay = max(0, .025 - (time.monotonic() - self._last))
            if delay:
                await asyncio.sleep(delay)
            self._last = time.monotonic()
            reason = blocked_reason(operation)
            if reason:
                raise WriteError("CONTRACT_BLOCKED", reason, 409)
            if profile != contract_profile(operation):
                raise WriteError("CONTRACT_BLOCKED", "Профиль API изменился; подготовьте новый план", 409)
            try:
                async with self._http.stream("POST", contract_path(operation, profile), content=request_content(operation, body, profile), headers={
                    "Client-Id": self._credentials.client_id, "Api-Key": self._credentials.api_key,
                    "Content-Type": "application/json"}) as response:
                    # After an attempted send all non-200 results are ambiguous;
                    # the service reconciles by fresh read, never resends.
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 8 * 1024 * 1024:
                            raise WriteError("UNKNOWN_RESULT", "Ответ Ozon превышает допустимый размер")
                        chunks.append(chunk)
                    content=b"".join(chunks)
                    if response.status_code != 200:
                        from .vendor_errors import normalize_vendor_error
                        try:detail=json.loads(content)
                        except (ValueError,UnicodeError):detail={}
                        vendor=normalize_vendor_error(detail,secrets=(self._credentials.client_id,self._credentials.api_key)) if isinstance(detail,dict) else None
                        raise WriteError("UNKNOWN_RESULT", "Ozon не подтвердил результат. Нужна сверка без повторной отправки",vendor_error=vendor)
                    result = json.loads(content, parse_float=Decimal)
                    if not isinstance(result, dict):
                        raise ValueError
                    return result
            except (httpx.HTTPError, ValueError, UnicodeError):
                raise WriteError("UNKNOWN_RESULT", "Исход отправки неизвестен. Повтор запрещён до сверки") from None
