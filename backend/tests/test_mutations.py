import asyncio
from copy import deepcopy
import json
import pytest
from app.accounts import AccountStore, AccountError
from app.mutations import MutationService
from app.ozon_write import WriteOperation as Op, WriteError


class Fixture:
    def __init__(self, tmp_path, *, live=False):
        from test_accounts import cipher
        self.accounts = AccountStore("LIVE_READ" if live else "MOCK", root=tmp_path, path=tmp_path / "accounts", cipher=cipher)
        if live:
            self.accounts.add("Synthetic", "1", "synthetic-key")
        self.items = {"a": {"id": "a", "product_id": "111", "current": "10.00", "currency": "RUB", "member": True},
                      "b": {"id": "b", "product_id": "222", "current": "20.00", "currency": "RUB", "member": True}}
        self.calls, self.reads, self.fail_read, self.send_timeout, self.apply, self.reject = 0, 0, False, False, True, set()
        self.now = 0
        self.service = MutationService(self.accounts, self.writer, self.fresh, root=tmp_path, path=tmp_path / "attempts", clock=lambda: self.now)

    async def fresh(self, op, scope, ids):
        self.reads += 1
        if self.fail_read:
            raise RuntimeError("Synthetic read failure")
        return {"complete": True, "items": deepcopy(list(self.items.values()))}

    def writer(self, credentials):
        fixture = self
        class Writer:
            async def send(self, op, body):
                fixture.calls += 1
                # Marker MUST exist before send, including timeout-after-apply.
                if fixture.accounts.mode != "MOCK":
                    markers = list((fixture.service.path).glob("*.json"))
                    assert any(json.loads(marker.read_text())["status"] == "UNKNOWN_RESULT"
                        and marker.stem in fixture.service._plans
                        and json.loads(fixture.service._plans[marker.stem]['document'])['body'] == body
                        for marker in markers)
                result, accepted, rejected = [], [], []
                key = "prices" if op is Op.PRICE else "stocks" if op is Op.STOCK else "product_ids" if op in (Op.ACTION_REMOVE, Op.AUTO_REMOVE) else "to_update" if "to_update" in body else "products"
                for change in body[key]:
                    pid = str(change.get("product_id", change.get("id"))) if isinstance(change, dict) else str(change)
                    original = next(row for row in fixture.items.values() if (row.get('offer_id') == change['offer_id'] if op is Op.STOCK and 'offer_id' in change else row["product_id"] == pid))
                    pid = original['product_id']
                    rejection = original["id"] in fixture.reject
                    if not rejection and fixture.apply:
                        if op in (Op.ACTION_REMOVE, Op.AUTO_REMOVE):
                            original["member"] = False
                        else:
                            action = change.get("action_price")
                            original["current"] = change.get("price", change.get("stock", action.get("amount") if isinstance(action, dict) else action))
                            if op is Op.STOCK:
                                original["present"] = original["current"] + original["reserved"]
                            else:
                                original["member"] = True
                    result.append({"product_id": int(pid), "warehouse_id": original.get("warehouse_id"), "updated": not rejection, "errors": ["SYNTHETIC"] if rejection else []})
                    (rejected if rejection else accepted).append({"product_id": pid} if rejection else pid)
                if fixture.send_timeout:
                    raise TimeoutError("Synthetic timeout after apply")
                return {"result": result} if op in (Op.PRICE, Op.STOCK) else {"product_ids": accepted, "rejected": rejected}
            async def close(self):
                pass
        return Writer()


def test_single_send_double_confirm_tampered_hash_and_immutable_targets(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        changes = [{"id": "a", "after": "12"}]
        plan = await fixture.service.preview("prices", {}, changes)
        changes[0]["after"] = "999"
        plan["rows"][0]["after"] = "888"
        with pytest.raises(WriteError) as error:
            await fixture.service.execute(plan["id"], "bad", confirmed=True)
        assert error.value.code == "PLAN_MISMATCH" and fixture.calls == 0
        result = await fixture.service.execute(plan["id"], plan["hash"], confirmed=True)
        assert result["status"] == "VERIFIED_SUCCESS" and fixture.items["a"]["current"] == "12.00"
        with pytest.raises(WriteError, match="повтор"):
            await fixture.service.execute(plan["id"], plan["hash"], confirmed=True)
        assert fixture.calls == 1
    asyncio.run(run())


@pytest.mark.parametrize("change", ["price", "reserve", "account", "ttl"])
def test_stale_source_binding_or_expiry_never_send(tmp_path, change):
    async def run():
        fixture = Fixture(tmp_path)
        plan = await fixture.service.preview("prices", {}, [{"id": "a", "after": "12"}])
        if change == "price": fixture.items["a"]["current"] = "11.00"
        if change == "reserve": fixture.items["a"]["reserved"] = 1
        if change == "account": fixture.accounts.add("Second"); fixture.accounts.select(next(key for key in fixture.accounts._accounts if key != fixture.accounts.active_id))
        if change == "ttl": fixture.now = 121
        with pytest.raises(WriteError):
            await fixture.service.execute(plan["id"], plan["hash"], confirmed=True)
        assert fixture.calls == 0
    asyncio.run(run())


def test_partial_rejection_each_item_verified(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        fixture.reject.add("b")
        plan = await fixture.service.preview("prices", {}, [{"id": "a", "after": "12"}, {"id": "b", "after": "22"}])
        result = await fixture.service.execute(plan["id"], plan["hash"], confirmed=True)
        assert result["status"] == "PARTIAL"
        assert [row["status"] for row in result["rows"]] == ["VERIFIED_SUCCESS", "REJECTED"]
        assert fixture.calls == 1
    asyncio.run(run())


def test_timeout_after_apply_readback_proves_and_no_retry(tmp_path):
    async def run():
        fixture = Fixture(tmp_path, live=True)
        fixture.send_timeout = True
        plan = await fixture.service.preview("prices", {}, [{"id": "a", "after": "12"}])
        result = await fixture.service.execute(plan["id"], plan["hash"], confirmed=True)
        assert result["status"] == "VERIFIED_SUCCESS" and fixture.calls == 1
        marker = json.loads(next((tmp_path / "attempts").glob("*.json")).read_text())
        assert set(marker) == {"id", "status"} and marker["status"] == "VERIFIED_SUCCESS"
        assert "111" not in json.dumps(marker)
    asyncio.run(run())


def test_unknown_restart_fail_closed_private_data_not_persisted(tmp_path):
    async def run():
        fixture = Fixture(tmp_path, live=True)
        fixture.send_timeout, fixture.apply = True, False
        plan = await fixture.service.preview("prices", {}, [{"id": "a", "after": "12"}])
        result = await fixture.service.execute(plan["id"], plan["hash"], confirmed=True)
        assert result["status"] == "UNKNOWN_RESULT"
        restarted = MutationService(fixture.accounts, fixture.writer, fixture.fresh, root=tmp_path, path=tmp_path / "attempts")
        assert restarted.history()[0]["recovered"]
        with pytest.raises(WriteError) as error:
            await restarted.preview("prices", {}, [{"id": "a", "after": "12"}])
        assert error.value.code == "UNRESOLVED"
        data = next((tmp_path / "attempts").glob("*.json")).read_text()
        assert "current" not in data and "product_id" not in data and "12.00" not in data
    asyncio.run(run())


def test_readback_failure_unknown_and_reconcile_only_reads(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        original = fixture.writer
        def factory(credentials):
            writer = original(credentials)
            send = writer.send
            async def wrapped(op, body):
                result = await send(op, body)
                fixture.fail_read = True
                return result
            writer.send = wrapped
            return writer
        fixture.service.writer_factory = factory
        plan = await fixture.service.preview("prices", {}, [{"id": "a", "after": "12"}])
        result = await fixture.service.execute(plan["id"], plan["hash"], confirmed=True)
        assert result["status"] == "UNKNOWN_RESULT"
        fixture.fail_read = False
        result = await fixture.service.reconcile(plan["id"], plan["hash"])
        assert result["status"] == "VERIFIED_SUCCESS" and fixture.calls == 1
    asyncio.run(run())


@pytest.mark.parametrize("operation", ["action_add", "action_remove", "auto_add", "stocks"])
def test_mock_all_operations_and_live_blockers(tmp_path, operation, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr('app.ozon_write._utc_now',lambda:datetime(2026,10,13,tzinfo=timezone.utc))
    async def run():
        for live in (False, True):
            fixture = Fixture(tmp_path / ("live" if live else "mock"), live=live)
            scope = {"action_id": "3"} if operation.startswith(("action", "auto")) else {}
            if operation.startswith("auto"): scope["auto_add_date"] = "2026-10-09T00:00:00Z"
            after = None if operation.endswith("remove") else "12.00"
            if operation == "stocks":
                fixture.items["a"].update(current=10, reserved=2, present=12, warehouse_id="5")
                after = 0
            if live and operation in ('action_add', 'action_remove'):
                with pytest.raises(WriteError) as error:
                    await fixture.service.preview(operation, scope, [{"id": "a", "after": after}])
                assert error.value.code == 'ACTION_TYPE_UNKNOWN' and fixture.calls == 0
                continue
            if live and operation == 'auto_add':
                fixture.items['a'].update(action_price='10.00',money_currencies={'price':'RUB','action_price':'RUB'})
                with pytest.raises(WriteError) as error:
                    await fixture.service.preview(operation, scope, [{"id": "a", "after": after}])
                assert error.value.code == 'AUTO_RETURN_ONLY' and fixture.calls == 0
                continue
            plan = await fixture.service.preview(operation, scope, [{"id": "a", "after": after}])
            if live and operation != 'stocks':
                assert plan["valid"] is False and plan["blocked_reason"]
                with pytest.raises(WriteError) as error:
                    await fixture.service.execute(plan["id"], plan["hash"], confirmed=True)
                assert error.value.code == "CONTRACT_BLOCKED" and fixture.calls == 0
            else:
                result = await fixture.service.execute(plan["id"], plan["hash"], confirmed=True)
                assert result["status"] == "VERIFIED_SUCCESS" and fixture.calls == 1
    asyncio.run(run())


def test_confirm_required_missing_currency_and_incomplete_fail_closed(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        plan = await fixture.service.preview("prices", {}, [{"id": "a", "after": "12"}])
        with pytest.raises(WriteError):
            await fixture.service.execute(plan["id"], plan["hash"])
        fixture.items["a"]["currency"] = None
        with pytest.raises(WriteError) as error:
            await fixture.service.preview("prices", {}, [{"id": "a", "after": "12"}])
        assert error.value.code == "CURRENCY_UNKNOWN"
        async def incomplete(*args): return {"complete": False, "items": []}
        fixture.service.fresh = incomplete
        with pytest.raises(WriteError):
            await fixture.service.preview("prices", {}, [{"id": "a", "after": "12"}])
        assert fixture.calls == 0
    asyncio.run(run())


def test_concurrent_confirmation_only_one_send(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        plan = await fixture.service.preview("prices", {}, [{"id": "a", "after": "12"}])
        results = await asyncio.gather(*(fixture.service.execute(plan["id"], plan["hash"], confirmed=True) for _ in range(2)), return_exceptions=True)
        assert sum(isinstance(result, WriteError) for result in results) == 1
        assert fixture.calls == 1
    asyncio.run(run())


def test_marker_failure_before_send_and_account_busy(tmp_path):
    async def run():
        fixture = Fixture(tmp_path, live=True)
        plan = await fixture.service.preview("prices", {}, [{"id": "a", "after": "12"}])
        def failed_marker(*args):
            assert fixture.accounts.busy
            with pytest.raises(AccountError):
                fixture.accounts.select(fixture.accounts.active_id)
            raise WriteError("ATTEMPT_STORAGE", "Synthetic")
        fixture.service._marker = failed_marker
        with pytest.raises(WriteError):
            await fixture.service.execute(plan["id"], plan["hash"], confirmed=True)
        assert fixture.calls == 0 and not fixture.accounts.busy
    asyncio.run(run())


def test_boost_price_bounds_are_money_reversed_and_local_price_rate(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        fixture.items["a"].update(elastic=True, price_min_elastic="15.00", price_max_elastic="5.00")
        await fixture.service.preview("action_add", {"action_id": "3"}, [{"id": "a", "after": "12"}])
        for value in ("4", "16"):
            with pytest.raises(WriteError):
                await fixture.service.preview("action_add", {"action_id": "3"}, [{"id": "a", "after": value}])
        for i in range(10):
            plan = await fixture.service.preview("prices", {}, [{"id": "a", "after": str(30 + i)}])
            await fixture.service.execute(plan["id"], plan["hash"], confirmed=True)
        plan = await fixture.service.preview("prices", {}, [{"id": "a", "after": "45"}])
        with pytest.raises(WriteError) as error:
            await fixture.service.execute(plan["id"], plan["hash"], confirmed=True)
        assert error.value.code == "LOCAL_RATE_LIMIT" and fixture.calls == 10
    asyncio.run(run())


@pytest.mark.parametrize("operation", ["prices", "stocks"])
def test_calculation_to_preview_source_race_rejects_old_delta(tmp_path, operation):
    async def run():
        fixture = Fixture(tmp_path)
        op = Op.PRICE if operation == "prices" else Op.STOCK
        if op is Op.STOCK:
            fixture.items["a"].update(current=9, reserved=2, present=11, warehouse_id="5")
        original = await fixture.fresh(op, {}, ["a"])
        computed_after = "11.00" if op is Op.PRICE else 10
        # First read price10/stock9 produced +1; repeated read has price12/stock12.
        fixture.items["a"]["current"] = "12.00" if op is Op.PRICE else 12
        if op is Op.STOCK:
            fixture.items["a"]["present"] = 14
        with pytest.raises(WriteError) as error:
            await fixture.service.preview(operation, {}, [{"id": "a", "after": computed_after}], expected_source=original)
        assert error.value.code == "FRESH_CHANGED"
        assert not fixture.service._plans and fixture.calls == 0
    asyncio.run(run())


def test_expected_source_checks_reserve_policy_and_allows_identical_rows(tmp_path):
    async def run():
        fixture = Fixture(tmp_path)
        original = await fixture.fresh(Op.PRICE, {}, ["a"])
        plan = await fixture.service.preview("prices", {}, [{"id": "a", "after": "11.00"}], expected_source=original)
        assert plan["valid"] is True
        fixture.items["a"]["price_min_elastic"] = "15.00"
        with pytest.raises(WriteError) as error:
            await fixture.service.preview("prices", {}, [{"id": "a", "after": "11.00"}], expected_source=original)
        assert error.value.code == "FRESH_CHANGED"
        assert fixture.calls == 0
    asyncio.run(run())
