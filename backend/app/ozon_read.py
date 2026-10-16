"""Closed semantic read allowlist: POST does not imply an Ozon mutation."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from enum import Enum
import json
import re
import time
import httpx
from .connection import Credentials
from .vendor_errors import normalize_vendor_error

BASE_URL = "https://api-seller.ozon.ru"
MAX_BYTES = 8 * 1024 * 1024


class ReadOperation(Enum):
    ROLES = ("POST", "/v1/roles")
    ACTIONS = ("GET", "/v1/actions")
    ACTION_PRODUCTS = ("POST", "/v2/actions/products")
    ACTION_CANDIDATES = ("POST", "/v2/actions/candidates")
    AUTO_PRODUCTS = ("POST", "/v2/actions/auto-add/products/list")
    AUTO_CANDIDATES = ("POST", "/v2/actions/auto-add/products/candidates")
    PRICES = ("POST", "/v5/product/info/prices")
    PRODUCT_LIST = ("POST", "/v3/product/list")
    PRODUCT_INFO = ("POST", "/v3/product/info/list")
    WAREHOUSES = ("POST", "/v2/warehouse/list")
    STOCKS = ("POST", "/v2/product/info/stocks-by-warehouse/fbs")
    FINANCE_BALANCE = ("POST", "/v1/finance/balance")
    FINANCE_CASHFLOW = ("POST", "/v1/finance/cash-flow-statement/list")
    FINANCE_DAY = ("POST", "/v1/finance/accrual/by-day")
    FINANCE_POSTINGS = ("POST", "/v1/finance/accrual/postings")
    FINANCE_TYPES = ("POST", "/v1/finance/accrual/types")
    FINANCE_REALIZATION = ("POST", "/v2/finance/realization")
    FINANCE_REALIZATION_DAY = ("POST", "/v1/finance/realization/by-day")
    FINANCE_REPORT_INFO = ("POST", "/v1/report/info")
    STRATEGY_COMPETITORS = ("POST", "/v1/pricing-strategy/competitors/list")
    STRATEGY_LIST = ("POST", "/v1/pricing-strategy/list")
    STRATEGY_INFO = ("POST", "/v1/pricing-strategy/info")
    STRATEGY_PRODUCTS = ("POST", "/v1/pricing-strategy/products/list")
    STRATEGY_PRODUCT_INFO = ("POST", "/v1/pricing-strategy/product/info")
    STRATEGY_IDS = ("POST", "/v1/pricing-strategy/strategy-ids-by-product-ids")
    ANALYTICS_WAREHOUSES = ("POST", "/v2/analytics/stock_on_warehouses")
    ANALYTICS_TURNOVER = ("POST", "/v1/analytics/turnover/stocks")
    ANALYTICS_STOCKS = ("POST", "/v1/analytics/stocks")
    RATING_SUMMARY = ("POST", "/v1/rating/summary")


class FinanceReport(Enum):
    MUTUAL = ("POST", "/v1/finance/mutual-settlement")
    COMPENSATION = ("POST", "/v1/finance/compensation")
    DECOMPENSATION = ("POST", "/v1/finance/decompensation")


class ReadError(Exception):
    def __init__(self, code: str, message: str, status: int = 502, *, diagnostic=None, vendor_error=None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.diagnostic = diagnostic
        self.vendor_error = vendor_error


async def rejection_diagnostic(response, *, secrets=()):
    """Bounded classification and allowlisted, credential-redacted reason."""
    chunks=[]; size=0
    async for chunk in response.aiter_bytes():
        size+=len(chunk)
        if size>16384: return {'category':'ERROR_BODY_LIMIT','upstream_status':response.status_code}
        chunks.append(chunk)
    try:
        value=json.loads(b''.join(chunks))
        if type(value) is not dict: raise ValueError()
    except (ValueError,UnicodeError): return {'category':'UNKNOWN_ERROR_FORMAT','upstream_status':response.status_code}
    code=value.get('code')
    safe_code=code if type(code) is int and -100 <= code <= 100 else code if type(code) is str and code in ('INVALID_ARGUMENT','PERMISSION_DENIED','UNAUTHENTICATED','NOT_FOUND','UNIMPLEMENTED','RESOURCE_EXHAUSTED') else None
    message=str(value.get('message','')).lower()
    fields=[field for field in ('company_id','client_id','page','limit','strategy_id','product_id','coefficient') if re.search(r'(?<![a-z_])'+field+r'(?![a-z_])',message)]
    category='SUBSCRIPTION' if any(word in message for word in ('premium','subscription','подписк')) else 'PERMISSION' if any(word in message for word in ('permission','forbidden','access denied','права','доступ')) else 'REQUIRED_FIELD' if any(word in message for word in ('required','missing','обязател')) else 'INVALID_PARAMETER' if any(word in message for word in ('invalid','range','must','validation','некоррект')) else 'UNSUPPORTED_OPERATION' if any(word in message for word in ('unimplemented','not supported','not available','disabled','deprecated','отключ','недоступ')) else 'UPSTREAM_REJECTION'
    envelope=value.get('error') if isinstance(value.get('error'),dict) else value
    return {'category':category,'upstream_status':response.status_code,'ozon_code':safe_code,'fields':fields,'vendor_error':normalize_vendor_error(envelope,secrets=secrets)}


def invalid() -> None:
    raise ReadError("INVALID_READ_REQUEST", "Некорректный контракт запроса чтения", 422)


def identifier(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return False
    return bool(re.fullmatch(r"[1-9][0-9]{0,19}", str(value))) and int(value) <= 2**64 - 1


def integer(value, maximum: int) -> bool:
    return type(value) is int and 1 <= value <= maximum


def selectors(value: dict, *, required: bool) -> None:
    keys = set(value) & {"product_id", "offer_id", "sku"}
    if len(keys) > 1 or (required and len(keys) != 1):
        invalid()
    for key in keys:
        values = value[key]
        if not isinstance(values, list) or not 1 <= len(values) <= 1000 or len(set(map(str, values))) != len(values):
            invalid()
        if key == "offer_id":
            if any(not isinstance(item, str) or not item or len(item) > 1024 for item in values):
                invalid()
        elif any(not identifier(item) for item in values):
            invalid()


def validate_request(op: ReadOperation, body: dict | None) -> None:
    if type(op) is not ReadOperation:
        raise ReadError("READ_ONLY", "Запрошенная операция не входит в список чтения", 403)
    if op.name.startswith(('STRATEGY_', 'ANALYTICS_', 'RATING_')):
        from .seller_extensions import validate_extension_request
        validate_extension_request(op.name, body)
        return
    if op.name.startswith('FINANCE_'):
        from .finance_contracts import validate_finance
        try: validate_finance(op.name,body)
        except (ValueError,TypeError,KeyError,OverflowError): invalid()
        return
    if op in (ReadOperation.ACTIONS, ReadOperation.ROLES):
        if body is not None:
            invalid()
        return
    if type(body) is not dict:
        invalid()
    allowed = {
        ReadOperation.ACTION_PRODUCTS: {"action_id", "limit", "last_id"},
        ReadOperation.ACTION_CANDIDATES: {"action_id", "limit", "last_id"},
        ReadOperation.AUTO_PRODUCTS: {"action_id", "auto_add_date", "limit", "offset"},
        ReadOperation.AUTO_CANDIDATES: {"action_id", "auto_add_date", "limit", "offset"},
        ReadOperation.PRICES: {"filter", "limit", "cursor"},
        ReadOperation.PRODUCT_LIST: {"filter", "limit", "last_id"},
        ReadOperation.PRODUCT_INFO: {"product_id", "offer_id", "sku"},
        ReadOperation.WAREHOUSES: {"limit", "cursor"},
        ReadOperation.STOCKS: {"offer_id", "sku", "limit", "cursor"},
    }[op]
    if set(body) - allowed:
        invalid()
    if op is ReadOperation.PRODUCT_INFO:
        selectors(body, required=True)
        return
    maximum = 200 if op is ReadOperation.WAREHOUSES else 100 if op in (ReadOperation.ACTION_PRODUCTS, ReadOperation.ACTION_CANDIDATES, ReadOperation.AUTO_PRODUCTS, ReadOperation.AUTO_CANDIDATES) else 1000
    if not integer(body.get("limit"), maximum):
        invalid()
    for field in ("cursor", "last_id"):
        if field in body and (not isinstance(body[field], str) or len(body[field]) > 8192):
            invalid()
    if op in (ReadOperation.ACTION_PRODUCTS, ReadOperation.ACTION_CANDIDATES, ReadOperation.AUTO_PRODUCTS, ReadOperation.AUTO_CANDIDATES):
        if type(body.get("action_id")) is not int or not identifier(body["action_id"]):
            invalid()
    if op in (ReadOperation.AUTO_PRODUCTS, ReadOperation.AUTO_CANDIDATES):
        date = body.get("auto_add_date")
        try:
            parsed = datetime.fromisoformat(date.replace("Z", "+00:00")) if isinstance(date, str) and len(date) <= 64 else None
            if parsed is None or parsed.tzinfo is None:
                invalid()
        except ValueError:
            invalid()
        offset = body.get("offset", 0)
        if type(offset) is not int or not 0 <= offset <= 50000:
            invalid()
    if op in (ReadOperation.PRICES, ReadOperation.PRODUCT_LIST):
        selector = body.get("filter")
        if type(selector) is not dict or set(selector) - {"product_id", "offer_id", "visibility"}:
            invalid()
        selectors(selector, required=op is ReadOperation.PRICES)
        if selector.get("visibility", "ALL") not in ("ALL", "VISIBLE", "INVISIBLE"):
            invalid()
    if op is ReadOperation.STOCKS:
        selectors(body, required=True)


def retry_delay(value: str | None, attempt: int) -> float:
    if value is None:
        return float(2**attempt)
    try:
        if re.fullmatch(r"[0-9]+", value):
            return float(value)
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return float(2**attempt)


class ReadClient:
    def __init__(self, credentials: Credentials | None, *, transport=None):
        self._credentials = credentials
        self._http = httpx.AsyncClient(base_url=BASE_URL, follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(12, connect=5), transport=transport)
        self._lock = asyncio.Lock()
        self._next_request = 0.0

    async def close(self):
        await self._http.aclose()

    async def warehouse_read(self,op,payload):
        from .warehouse_reports import WarehouseRead,validate_warehouse_request
        if type(op) is not WarehouseRead:invalid()
        validate_warehouse_request(op.name,payload)
        if self._credentials is None:raise ReadError('NOT_CONNECTED','Выберите или подключите магазин',409)
        try:
            async with asyncio.timeout(45):
                return await self._read(op,payload,attempts=3)
        except TimeoutError:
            raise ReadError('READ_TIMEOUT','Истекло время чтения Ozon. Повторите загрузку позже.',504) from None

    async def read(self, op: ReadOperation, payload: dict | None = None) -> dict:
        validate_request(op, payload)
        if self._credentials is None:
            raise ReadError("NOT_CONFIGURED", "Подключение не настроено. Заполните backend/.env локально и перезапустите start-read; ключ в чат не отправляйте.", 503)
        try:
            async with asyncio.timeout(45):
                return await self._read(op, payload, attempts=1 if op is ReadOperation.ANALYTICS_TURNOVER else 3)
        except TimeoutError:
            raise ReadError("READ_TIMEOUT", "Истекло время чтения Ozon. Повторите загрузку позже.", 504) from None

    async def create_finance_report(self, kind: FinanceReport, month: str):
        from .finance_contracts import report_request
        if type(kind) is not FinanceReport: invalid()
        try: payload=report_request(month)
        except ValueError: invalid()
        if self._credentials is None: raise ReadError('NOT_CONFIGURED','Seller подключение не настроено',503)
        try:
            async with asyncio.timeout(45):
                return await self._read(kind,payload,attempts=1)
        except TimeoutError:
            raise ReadError('UNKNOWN_REPORT_RESULT','Результат создания отчёта неизвестен. Не повторяйте автоматически; проверьте документы Seller.',504) from None
        except ReadError as exc:
            if exc.code in ('RESPONSE_CONTRACT','RESPONSE_LIMIT'):
                raise ReadError('UNKNOWN_REPORT_RESULT','Отчёт мог быть создан, но ответ не подтверждён. Проверьте документы Seller.',502) from None
            raise

    async def _read(self, op, payload, attempts=3):
        method, path = op.value
        for attempt in range(attempts):
            delay = None
            try:
                async with self._lock:
                    await asyncio.sleep(max(0, self._next_request - time.monotonic()))
                    self._next_request = time.monotonic() + 0.2
                    async with self._http.stream(method, path, json=payload if payload is not None else None,
                        headers={"Client-Id": self._credentials.client_id, "Api-Key": self._credentials.api_key}) as response:
                        status = response.status_code
                        if 300 <= status < 400:
                            raise ReadError("REDIRECT_BLOCKED", "Ozon вернул перенаправление. Оно заблокировано; ключ другому адресу не отправлен.")
                        if status in (401, 403):
                            diagnostic=await rejection_diagnostic(response,secrets=(self._credentials.client_id,self._credentials.api_key))
                            vendor_error=diagnostic.pop('vendor_error',None)
                            raise ReadError("AUTHORIZATION", "Ozon отклонил доступ. Проверьте Client-Id, ключ, срок действия и права на чтение в Seller.", 403,diagnostic=diagnostic,vendor_error=vendor_error)
                        if status == 429 or 500 <= status < 600:
                            if attempts == 1:
                                diagnostic=await rejection_diagnostic(response,secrets=(self._credentials.client_id,self._credentials.api_key))
                                vendor_error=diagnostic.pop('vendor_error',None)
                                if type(op) is ReadOperation:
                                    raise ReadError('RATE_LIMIT' if status==429 else 'UPSTREAM_ERROR','Ozon ограничил чтение; повторите после указанного интервала',503,diagnostic=diagnostic,vendor_error=vendor_error)
                                raise ReadError('UNKNOWN_REPORT_RESULT','Создание отчёта не подтверждено. Проверьте документы Seller перед повторным запуском.',503,diagnostic=diagnostic,vendor_error=vendor_error)
                            delay = retry_delay(response.headers.get("retry-after"), attempt)
                            if attempt == attempts-1 or delay > 15:
                                diagnostic=await rejection_diagnostic(response,secrets=(self._credentials.client_id,self._credentials.api_key))
                                vendor_error=diagnostic.pop('vendor_error',None)
                                raise ReadError("RATE_LIMIT" if status == 429 else "UPSTREAM_ERROR", "Ozon временно ограничил чтение. Повторите загрузку позже.", 503,diagnostic=diagnostic,vendor_error=vendor_error)
                        elif status >= 400 or status < 200:
                            diagnostic=await rejection_diagnostic(response,secrets=(self._credentials.client_id,self._credentials.api_key))
                            vendor_error=diagnostic.pop('vendor_error',None)
                            raise ReadError("READ_REJECTED", f"Ozon отклонил запрос чтения (HTTP {status}). Требуется проверка контракта и прав.",diagnostic=diagnostic,vendor_error=vendor_error)
                        else:
                            chunks, size = [], 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > MAX_BYTES:
                                    raise ReadError("RESPONSE_LIMIT", "Ответ Ozon превышает безопасный размер; загрузка неполная.")
                                chunks.append(chunk)
                            try:
                                def reject_constant(value):
                                    raise ValueError("Non-finite JSON")
                                result = json.loads(b"".join(chunks), parse_float=Decimal, parse_constant=reject_constant)
                                if not isinstance(result, dict):
                                    raise ValueError("Expected object")
                            except (ValueError, UnicodeError, RecursionError):
                                raise ReadError("RESPONSE_CONTRACT", "Ozon вернул ответ неизвестного формата. Данные не подменялись.") from None
                            if 'code' in result and 'message' in result:
                                error=normalize_vendor_error(result,secrets=(self._credentials.client_id,self._credentials.api_key))
                                raise ReadError('READ_REJECTED',error['message'],vendor_error=error)
                            return result
            except httpx.RequestError:
                if attempts == 1:
                    if type(op) is ReadOperation:
                        raise ReadError('NETWORK','Ответ чтения не получен; автоматический повтор запрещён лимитом операции',503) from None
                    raise ReadError('UNKNOWN_REPORT_RESULT','Ответ создания отчёта потерян. Не повторяйте автоматически; проверьте документы Seller.',503) from None
                if attempt == attempts-1:
                    raise ReadError("NETWORK", "Не удалось безопасно прочитать Ozon. Проверьте интернет и повторите загрузку.", 503) from None
                delay = float(2**attempt)
            if delay is not None:
                await asyncio.sleep(delay)
        raise ReadError("READ_FAILED", "Чтение Ozon не завершено", 503)
