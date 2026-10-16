"""Isolated browser fixture at :8001; no credentials file or network client."""
import asyncio
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).absolute().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.connection import Connection, Credentials
from app.ozon_read import ReadOperation as Op, validate_request
from app.live_catalog import LiveCatalog
from app.main import create_app
from app import main as application_module
from app.storage import Store
import uvicorn

DATE1 = "2026-10-14T00:00:00Z"
DATE2 = "2026-10-15T00:00:00Z"
MONEY = lambda value: {"amount": value, "currency": "RUB"}
FIXTURE_NOTICE = "СИНТЕТИЧЕСКАЯ ПРОВЕРКА READ-ONLY. Данные придуманы; запросов к Ozon нет."


class FixtureClient:
    async def close(self):
        pass

    async def read(self, op, payload=None):
        validate_request(op, payload)
        await asyncio.sleep(0.15)
        pid = "9007199254740993"
        responses = {
            Op.ROLES: {"expires_at": "2026-12-31T00:00:00Z", "roles": [{"name": "SYNTHETIC", "methods": ["/v1/actions", "/v2/products/stocks"]}]},
            Op.ACTIONS: {"result": [{"id": 10001, "title": "Синтетическая акция с автодобавлением", "date_start": "2026-10-01T00:00:00Z", "date_end": "2026-10-31T23:59:59Z", "auto_add_dates": [DATE1, DATE2], "participating_products_count": 1, "potential_products_count": 1}]},
            Op.ACTION_PRODUCTS: {"products": [{"id": int(pid), "price": MONEY("1000.00"), "action_price": MONEY("920.00"), "price_min_elastic": MONEY("950.00"), "price_max_elastic": MONEY("800.00")}], "last_id": "", "total": 1},
            Op.ACTION_CANDIDATES: {"products": [{"id": 20002, "price": MONEY("1999.00"), "max_action_price": MONEY("1900.00")}], "last_id": "", "total": 1},
            Op.AUTO_PRODUCTS: {"products": [{"product_id": int(pid), "offer_id": "SYN-LARGE-ID", "sku": "30001", "name": "Синтетическая термокружка", "price": MONEY("1000.00"), "action_price_to_auto_add": MONEY("900.00" if payload["auto_add_date"] == DATE1 else "850.00"), "max_discount_price": MONEY("950.00"), "min_seller_price": MONEY("800.00")}], "total": 1} if op is Op.AUTO_PRODUCTS else {},
            Op.AUTO_CANDIDATES: {"products": [{"id": 20002, "name": "Синтетическая лампа", "offer_id": "SYN-LAMP", "sku": "30002", "price": MONEY("1999.00"), "action_price_to_auto_add": MONEY("1899.05")}], "total": 1},
            Op.PRODUCT_LIST: {"result": {"items": [{"product_id": int(pid), "offer_id": "SYN-LARGE-ID"}, {"product_id": 20002, "offer_id": "SYN-LAMP"}], "last_id": "", "total_items": 2}},
            Op.PRODUCT_INFO: {"items": [{"id": int(pid), "offer_id": "SYN-LARGE-ID", "name": "Синтетическая термокружка", "sku": "30001"}, {"id": 20002, "offer_id": "SYN-LAMP", "name": "Синтетическая лампа", "sku": "30002"}]},
            Op.PRICES: {"items": [{"product_id": int(pid), "offer_id": "SYN-LARGE-ID", "price": {"price": 1000, "currency_code": "RUB"}}, {"product_id": 20002, "offer_id": "SYN-LAMP", "price": {"price": 1999, "currency_code": "RUB"}}], "cursor": "", "total_items": 2},
            Op.WAREHOUSES: {"warehouses": [{"warehouse_id": 40001, "name": "Синтетический FBS"}], "cursor": "", "has_next": False},
            Op.STOCKS: {"products": [{"product_id": int(pid), "offer_id": "SYN-LARGE-ID", "warehouse_id": 40001, "present": 12, "reserved": 2, "free_stock": 9}, {"product_id": 20002, "offer_id": "SYN-LAMP", "warehouse_id": 40001, "present": 0, "reserved": 0}], "cursor": "", "has_next": False},
        }
        response = deepcopy(responses[op])
        if op is Op.PRODUCT_INFO:
            response["items"] = [row for row in response["items"] if str(row["id"]) in payload["product_id"]]
        if op is Op.PRICES:
            response["items"] = [row for row in response["items"] if str(row["product_id"]) in payload["filter"]["product_id"]]
            response["total_items"] = len(response["items"])
        return response


class FixtureCatalog(LiveCatalog):
    async def catalog(self, **kwargs):
        result = await super().catalog(**kwargs)
        result["warnings"].insert(0, FIXTURE_NOTICE)
        return result


if __name__ == "__main__":
    if len(sys.argv) != 1 or Path.cwd().resolve() != ROOT.resolve():
        raise SystemExit("Запускайте только через закрытый runner browser-fixture из V2")
    client = FixtureClient()
    # This test process alone serves a different fixed loopback port.
    application_module.ALLOWED_ORIGINS = application_module.ALLOWED_ORIGINS | {"http://127.0.0.1:8001"}
    app = create_app(Store(ROOT / ".cache/i1-browser/fixture.sqlite3"),
                     connection=Connection("LIVE_READ", Credentials("10001", "SYNTHETIC-NOT-A-KEY"), FIXTURE_NOTICE), read_client=client)
    app.state.live_catalog = FixtureCatalog(client)
    uvicorn.run(app, host="127.0.0.1", port=8001, workers=1)
