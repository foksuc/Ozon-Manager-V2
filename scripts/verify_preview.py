"""Authenticated volatile calculation, bounded reads. Evidence contains counts only."""
import asyncio
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
from verify_read import ROOT, ObservedReads
from app.connection import load_connection
from app.live_catalog import LiveCatalog
from app.models import Catalog, ReadPreviewRequest
from app.ozon_read import ReadClient, ReadError
from app.read_preview import calculate_read_preview
from app.storage import confined


async def verify():
    settings = load_connection("LIVE_READ")
    report = {"date_utc":datetime.now(timezone.utc).isoformat(),"scope":"All returned ordinary actions/roles and auto-add dates/roles plus FBS stocks; bounded by 150 read calls; up to 1000 calculation rows per scope; no writes",
              "checks":{},"mutation_requests":0,"persisted_catalog_rows":0}
    if settings.credentials is None:
        return {**report,"status":"NOT_CONFIGURED"}
    client = ReadClient(settings.credentials)
    observed = ObservedReads(client)
    reader = LiveCatalog(observed)
    async def check(label, kind="prices", **scope):
        data = Catalog.model_validate(await reader.catalog(scope="products" if kind == "prices" else "stocks", **scope))
        items = data.products if kind == "prices" else data.stocks
        if not items:
            report["checks"][label] = {"status":"EMPTY", "complete":data.complete}
            return
        request = ReadPreviewRequest(kind=kind,ids=[row.id for row in items[:1000]],mode="percent" if kind == "prices" else "manual",
            value="5" if kind == "prices" else "0",rounding="0.01" if kind == "prices" else "1",**scope)
        result = calculate_read_preview(data, request)
        report["checks"][label] = {"status":"CALCULATED" if result.valid else "BLOCKED_ROWS_OR_PARTIAL",
            "complete":result.complete,"loaded_rows":len(items),"rows":len(result.rows),"calculated":sum(r.after is not None for r in result.rows),
            "blocked":sum(r.error is not None for r in result.rows),"unknown_currency":sum(r.currency is None for r in result.rows) if kind == "prices" else 0,
            "stored":False,"executable_plan":False}
    try:
        actions = Catalog.model_validate(await reader.catalog()).actions
        expected = 1 + 2 * len(actions) + 2 * sum(len(a.auto_add_dates) for a in actions)
        report["expected_scopes"] = expected
        for action_index, selected in enumerate(actions,1):
            for role in ("member","candidate"):
                await check(f"ordinary_{action_index}_{role}",action_id=selected.id,role=role)
            for date_index, date in enumerate(selected.auto_add_dates,1):
                for role in ("member","candidate"):
                    await check(f"auto_{action_index}_date_{date_index}_{role}",action_id=selected.id,role=role,auto_add=True,auto_add_date=date)
        await check("stocks",kind="stocks")
        report["status"] = "VERIFIED_WITH_LIMITS" if actions and len(report["checks"]) == expected and all(c.get("complete") for c in report["checks"].values()) else "INCOMPLETE"
    except ReadError as error:
        report["status"] = "READ_ERROR"
        report["error_code"] = error.code
    except Exception:
        report["status"] = "INTERNAL_ERROR"
    finally:
        await client.close()
    report["adapter_calls"] = observed.calls
    return report


if __name__ == "__main__":
    if len(sys.argv) != 1 or ROOT.resolve() != Path.cwd().resolve():
        raise SystemExit("Use the closed project command")
    result = asyncio.run(verify())
    confined(ROOT / "docs/evidence/r1-live-preview-checks.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "VERIFIED_WITH_LIMITS" else 1)
