"""Bounded authenticated read acceptance. Never persist response values or secrets."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

ROOT = Path(__file__).absolute().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.connection import load_connection
from app.live_catalog import LiveCatalog
from app.models import Catalog
from app.ozon_read import ReadClient, ReadError, ReadOperation
from app.storage import confined

FIELD_NAMES = set("result items products roles name methods expires_at id product_id offer_id sku sources price action_price amount currency currency_code price_min_elastic price_max_elastic max_action_price max_discount_price min_seller_price action_price_to_auto_add date_start date_end title auto_add_dates participating_products_count potential_products_count total total_items cursor last_id has_next warehouses warehouse_id warehouse_name is_rfbs free_stock reserved present limit offset price_min price_max add_mode is_manually_added is_elastic_discount min_price max_price marketing_price old_price commissions stocks errors error code message details".split())


def shape(value, depth=0):
    """Only field names/types/counts; no leaf values, including errors or strings."""
    if isinstance(value, dict) and depth < 5:
        return {key if key in FIELD_NAMES else "<unrecognized-key>": shape(item, depth + 1)
                for key, item in list(value.items())[:40]}
    if isinstance(value, list):
        return {"type": "array", "count": len(value), "item": shape(value[0], depth + 1) if value and depth < 5 else None}
    if isinstance(value, Decimal):
        return "decimal"
    return type(value).__name__


class ObservedReads:
    def __init__(self, client):
        self.client = client
        self.operations = {}
        self.calls = 0

    async def read(self, operation, payload=None):
        self.calls += 1
        if self.calls > 150:
            raise ReadError("ACCEPTANCE_LIMIT", "Достигнут предел проверочных чтений")
        item = self.operations.setdefault(operation.name, {"method": operation.value[0], "path": operation.value[1],
                                                          "calls": 0, "successes": 0, "errors": []})
        item["calls"] += 1
        try:
            response = await self.client.read(operation, payload)
        except ReadError as error:
            # Codes come from application constants, never API error text.
            item["errors"].append({"code": error.code, "local_status": error.status})
            raise
        item["successes"] += 1
        item.setdefault("response_shape", shape(response))
        # Count wire placeholders without recording any monetary/entity values.
        for row in response.get("products", []):
            if not isinstance(row, dict):
                continue
            for field in ("price", "action_price", "max_action_price", "price_min_elastic", "price_max_elastic", "min_seller_price", "max_discount_price", "action_price_to_auto_add"):
                value = row.get(field)
                category = "absent" if value is None else "scalar"
                if isinstance(value, dict):
                    amount, currency = value.get("amount"), value.get("currency")
                    category = "rub" if currency == "RUB" else "currency_unknown"
                    if currency in (None, ""):
                        category = "empty_money" if amount in (None, "") else "zero_without_currency" if amount in (0, "0", "0.00") else "amount_without_currency"
                buckets = item.setdefault("money_field_counts", {}).setdefault(field, {})
                buckets[category] = buckets.get(category, 0) + 1
        return response


async def verify():
    settings = load_connection("LIVE_READ")
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "mode": settings.mode,
              "configured": settings.credentials is not None, "scope": "Authenticated reads only; no mutations",
              "limits": "150 adapter calls, 120sec per catalog, one ordinary action and one auto-add date; not exhaustive store acceptance",
              "checks": {}, "operations": {}}
    if settings.credentials is None:
        report["status"] = "NOT_CONFIGURED"
        return report
    client = ReadClient(settings.credentials)
    observed = ObservedReads(client)
    catalog = LiveCatalog(observed)

    async def read_scope(label, **parameters):
        try:
            result = Catalog.model_validate(await catalog.catalog(**parameters)).model_dump(mode="json")
        except ReadError as error:
            report["checks"][label] = {"status": "ERROR", "code": error.code, "local_status": error.status}
            return None
        except Exception:
            report["checks"][label] = {"status": "INTERNAL_OR_DTO_ERROR"}
            return None
        report["checks"][label] = {"status": "PASS" if result["complete"] else "PARTIAL", "complete": result["complete"],
                                  "actions": len(result["actions"]), "products": len(result["products"]),
                                  "stocks": len(result["stocks"]), "warning_count": len(result["warnings"]),
                                  "warning_categories": result["warnings"]}
        # Adapter warnings are fixed text; never insert API body or entity values.
        if parameters.get("scope") == "stocks":
            report["checks"][label]["available_missing"] = sum(row["available"] is None for row in result["stocks"])
            report["checks"][label]["reserved_missing"] = sum(row["reserved"] is None for row in result["stocks"])
        if parameters.get("scope") == "products":
            report["checks"][label]["base_price_missing"] = sum(row["price"] is None for row in result["products"])
            report["checks"][label]["currency_unknown"] = sum(row["currency"] is None for row in result["products"])
        return result

    try:
        try:
            roles = await observed.read(ReadOperation.ROLES)
            report["checks"]["roles"] = {"status": "PASS", "role_count": len(roles.get("roles", []))}
        except ReadError as error:
            report["checks"]["roles"] = {"status": "ERROR", "code": error.code}
        actions = await read_scope("actions", scope="actions")
        if actions and actions["actions"]:
            rows = actions["actions"]
            report["checks"]["actions"]["auto_add_count"] = sum(row["auto_add"] for row in rows)
            action = max(rows, key=lambda row: ((row["status"] == "active"), (row["member_count"] or 0) + (row["candidate_count"] or 0)))
            for role in ("member", "candidate"):
                await read_scope("ordinary_" + role, scope="products", action_id=action["id"], role=role)
            auto = [row for row in rows if row["auto_add_dates"]]
            if auto:
                selected = max(auto, key=lambda row: (row["status"] != "ended", len(row["auto_add_dates"])))
                date = selected["auto_add_dates"][0]
                for role in ("member", "candidate"):
                    await read_scope("auto_add_" + role, scope="products", action_id=selected["id"], role=role,
                                     auto_add=True, auto_add_date=date)
            else:
                report["checks"]["auto_add"] = {"status": "NOT_AVAILABLE", "reason": "No dates returned by actions"}
        await read_scope("stocks", scope="stocks")
    finally:
        await client.close()
        report["operations"] = observed.operations
    statuses = [item["status"] for item in report["checks"].values()]
    report["status"] = "PASS_WITH_SCOPE_LIMITS" if statuses and all(value == "PASS" for value in statuses) else "PARTIAL_OR_FAILED"
    report["mutation_requests"] = 0
    return report


def main():
    # The project runner checks ancestors/subtrees before starting this fixed command.
    if Path.cwd().resolve() != ROOT.resolve() or len(sys.argv) != 1:
        raise SystemExit("Run the closed V2 project command without overrides")
    target = confined(ROOT / "docs/evidence/i1-live-read-checks.json")
    try:
        result = asyncio.run(verify())
    except Exception:
        result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "status": "INTERNAL_CHECK_ERROR", "mutation_requests": 0}
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Print only summaries; response_shape contains types but remains in the local report.
    print(json.dumps({key: value for key, value in result.items() if key != "operations"}, ensure_ascii=False))
    print(json.dumps({"operation_results": {key: {field: item[field] for field in ("calls", "successes", "errors")}
                                            for key, item in result.get("operations", {}).items()}}, ensure_ascii=False))
    return 0 if result["status"] == "PASS_WITH_SCOPE_LIMITS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
