"""Pure safety and resumability checks; never connect to Discord or checkout."""

import asyncio
import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def checkout(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("monetization_reference_checkout")


NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)
ROW = {
    "id": "123",
    "application_id": "456",
    "user_id": "789",
    "sku_id": "111",
    "type": 4,
    "deleted": False,
    "consumed": False,
    "starts_at": None,
    "ends_at": None,
}


@pytest.mark.parametrize(
    "patch",
    [
        {"application_id": "999"},
        {"user_id": "999"},
        {"sku_id": "999"},
        {"guild_id": "999"},
        {"type": 1},
        {"type": 8},
        {"type": True},
        {"deleted": True},
        {"deleted": None},
        {"consumed": True},
    ],
)
def test_only_exact_fresh_test_purchase_is_selected(checkout, patch):
    with pytest.raises(RuntimeError):
        checkout.select_new_purchase([{**ROW, **patch}], [], "456", "789", "111", 4)


def test_missing_consumed_is_not_false(checkout):
    row = {key: value for key, value in ROW.items() if key != "consumed"}
    with pytest.raises(RuntimeError):
        checkout.select_new_purchase([row], [], "456", "789", "111", 4)


@pytest.mark.parametrize(
    "rows,baseline",
    [
        ([ROW], ["123"]),
        ([ROW, ROW], []),
        ([ROW], ["999", "999"]),
        ([{**ROW, "id": str(number)} for number in range(100)], []),
        ([], []),
    ],
)
def test_selection_never_guesses_or_reuses(checkout, rows, baseline):
    with pytest.raises(RuntimeError):
        checkout.select_new_purchase(rows, baseline, "456", "789", "111", 4)


def test_active_purchase_cannot_authorize_deleted_consume(checkout):
    with pytest.raises(RuntimeError):
        checkout.validate_purchase(ROW, "456", "789", "111", 4, deleted=True)
    assert checkout.validate_purchase({**ROW, "deleted": True}, "456", "789", "111", 4, deleted=True)


@pytest.mark.parametrize("kind", [1, 8])
def test_upgrade_end_is_not_natural_expiry(checkout, kind):
    a = {**ROW, "type": kind}
    ended = {**a, "ends_at": (NOW - timedelta(seconds=1)).isoformat()}
    b = {**a, "id": "124", "sku_id": "112"}
    assert checkout.classify_upgrade(a, ended, b, NOW) == "ended_by_upgrade"
    for bad in ({**ended, "deleted": True}, {**ended, "application_id": "999"}, a):
        assert checkout.classify_upgrade(a, bad, b, NOW) is None
    assert checkout.classify_upgrade(a, ended, {**b, "ends_at": NOW.isoformat()}, NOW) is None
    assert checkout.classify_upgrade(a, ended, {**b, "user_id": "999"}, NOW) is None


@pytest.mark.parametrize("kind", [1, 8])
def test_live_subscription_selection_preserves_identity_and_temporal_guards(checkout, kind):
    row = {**ROW, "type": kind}
    assert checkout.select_new_purchase([row], [], "456", "789", "111", checkout.LIVE_TYPES)["id"] == "123"
    for patch in (
        {"type": 4},
        {"type": True},
        {"user_id": "999"},
        {"sku_id": "999"},
        {"application_id": "999"},
        {"deleted": True},
        {"starts_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
    ):
        with pytest.raises(RuntimeError):
            checkout.select_new_purchase([{**row, **patch}], [], "456", "789", "111", checkout.LIVE_TYPES)


def test_cancellation_needs_correlated_reporting_not_ended_entitlement(checkout):
    report = {
        "id": "700",
        "user_id": "789",
        "sku_ids": ["112"],
        "entitlement_ids": ["124"],
        "status": 2,
        "canceled_at": NOW.isoformat(),
    }
    assert checkout.cancellation_verified(report, "789", "112", "124")
    for patch in (
        {"status": 0},
        {"canceled_at": None},
        {"user_id": "999"},
        {"sku_ids": ["111"]},
        {"entitlement_ids": ["123"]},
    ):
        assert not checkout.cancellation_verified({**report, **patch}, "789", "112", "124")
    assert not checkout.cancellation_verified({**ROW, "ends_at": NOW.isoformat()}, "789", "111", "123")


def test_no_flags_do_no_io(checkout):
    asyncio.run(checkout.capture_checkouts(SimpleNamespace(args=SimpleNamespace(), state={})))


def test_flags_without_attestation_only_block(checkout):
    blocked = []
    ctx = SimpleNamespace(
        args=SimpleNamespace(deleted_purchase=True, live_upgrade=True),
        state={},
        block=lambda case, reason: blocked.append(case),
    )
    asyncio.run(checkout.capture_checkouts(ctx))
    assert blocked == ["checkout.deleted_purchase", "checkout.live_upgrade"]


def test_zero_total_confirmation_is_not_implicit(checkout, monkeypatch):
    stage = {"cancellation_required": True}

    async def response(function, prompt):
        return "1.00"

    monkeypatch.setattr(checkout.asyncio, "to_thread", response)
    ctx = SimpleNamespace(save=lambda: None)
    with pytest.raises(RuntimeError):
        asyncio.run(checkout._manual(ctx, "case", stage, "subscribe_prompted", "Operator-only", zero=True))
    assert stage["subscribe_prompted"] is True
    assert stage["cancellation_required"] is True
    assert not stage.get("subscribe_prompted_zero_confirmed")


class PurchaseContext:
    def __init__(self, reject_delete=False):
        self.args = SimpleNamespace(user=789, consumable="Consumable")
        self.app = {"id": "456"}
        self.skus = [{"id": "111", "name": "Consumable", "type": 3}]
        self.state = {"cases": {}, "owned_entitlements": {}}
        self.base = "/applications/456/entitlements"
        self.row = dict(ROW)
        self.writes = []
        self.reject_delete = reject_delete
        self.purchased = False
        self.serial = 0

    def save(self):
        pass

    async def walk(self, case, query=None):
        assert query["exclude_deleted"] == "false"
        self.serial += 1
        self.state["cases"][case] = {"rest": [f"rest.jsonl:{self.serial}"]}
        return [dict(self.row)] if self.purchased else []

    async def probe(self, case):
        if "purchase_prompted" in case:
            self.purchased = True
        return {}

    async def observe(self, case):
        pass

    async def inspect(self, case, eid, sku, **kwargs):
        return dict(self.row)

    async def request(self, case, method, path):
        self.serial += 1
        fields = self.state["cases"].setdefault(case, {})
        fields["rest"] = [f"rest.jsonl:{self.serial}"]
        if method != "GET":
            self.writes.append((method, path))
            fields["mutation_complete"] = True
            if method == "DELETE":
                if self.reject_delete:
                    return 403, {"code": 50013}
                self.row["deleted"] = True
            else:
                assert self.row["deleted"] is True
                self.row["consumed"] = True
            return 204, None
        return 200, dict(self.row)


def test_deleted_fixture_mutations_are_resumable_not_cleanup_owned(checkout):
    ctx = PurchaseContext()
    asyncio.run(checkout._deleted_purchase(ctx))
    assert [method for method, path in ctx.writes] == ["DELETE", "POST", "POST"]
    assert ctx.state["manual_test_purchase_ids"] == ["123"]
    assert not ctx.state["owned_entitlements"]
    initial = ctx.state["checkout"]["deleted_purchase"]["purchase_initial"]
    assert initial["consumed"] is False and initial["deleted"] is False
    asyncio.run(checkout._deleted_purchase(ctx))
    assert len(ctx.writes) == 3


def test_rejected_delete_never_consumes_active_row_on_resume(checkout):
    ctx = PurchaseContext(reject_delete=True)
    for _attempt in range(2):
        with pytest.raises(RuntimeError):
            asyncio.run(checkout._deleted_purchase(ctx))
    assert [method for method, path in ctx.writes] == ["DELETE"]


def test_interrupted_live_checkout_keeps_cancellation_obligation(checkout, monkeypatch):
    a = {"id": "111", "name": "A", "type": 5, "flags": 256}
    b = {"id": "112", "name": "B", "type": 5, "flags": 256}
    ctx = PurchaseContext()
    ctx.skus = [a, b]
    ctx.args.user_sku_a, ctx.args.user_sku_b = "A", "B"

    async def interrupted(*args, **kwargs):
        stage = ctx.state["checkout"]["live_upgrade"]
        assert stage["cancellation_required"] is True
        raise KeyboardInterrupt

    monkeypatch.setattr(checkout, "_manual", interrupted)
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(checkout._live_upgrade(ctx))
    stage = ctx.state["checkout"]["live_upgrade"]
    assert stage["cancellation_required"] is True
    assert stage["cancellation_verified"] is False
    assert not ctx.writes


def test_cancellation_resume_never_returns_to_checkout(checkout, monkeypatch):
    ctx = PurchaseContext()
    ctx.args.user_sku_a, ctx.args.user_sku_b = "A", "B"
    ctx.skus = [
        {"id": "111", "name": "A", "type": 5, "flags": 256},
        {"id": "112", "name": "B", "type": 5, "flags": 256},
    ]
    stage = checkout._stage(ctx, "live_upgrade", ctx.skus)
    stage.update(cancellation_required=True, cancellation_started=True, a="123")
    calls = []

    async def cancel(context, prefix, saved, a, b):
        calls.append(saved["a"])

    async def forbidden(*args, **kwargs):
        raise AssertionError("Cancellation resume must not prompt checkout")

    monkeypatch.setattr(checkout, "_cancel", cancel)
    monkeypatch.setattr(checkout, "_manual", forbidden)
    asyncio.run(checkout._live_upgrade(ctx))
    assert calls == ["123"]


def test_canceled_replacement_does_not_hide_uncanceled_original(checkout):
    ctx = PurchaseContext()
    a, b = {"id": "111"}, {"id": "112"}
    stage = {"cancellation_required": True, "a": "123", "b": "124", "upgrade_prompted": True}
    rows = {
        eid: {**ROW, "id": eid, "sku_id": sku, "type": 8} for eid, sku in (("123", "111"), ("124", "112"))
    }
    reports = {
        sku: {
            "id": sid,
            "user_id": "789",
            "sku_ids": [sku],
            "entitlement_ids": [eid],
            "status": status,
            "canceled_at": None if status == 0 else "2026-01-01T00:00:00Z",
        }
        for sku, sid, eid, status in (("111", "201", "123", 0), ("112", "202", "124", 2))
    }

    async def request(case, method, path, **kwargs):
        assert method == "GET", "Live subscriptions must never be mutated through this recorder"
        if path.startswith("/skus/"):
            report = reports[path.split("/")[2]]
            return 200, [report] if path.endswith("/subscriptions") else report
        return 200, rows[path.rsplit("/", 1)[-1]]

    ctx.request = request
    ctx.block = lambda case, reason: ctx.state["cases"].update(
        {case: {"status": "blocked", "reason": reason}}
    )
    asyncio.run(checkout._cancel(ctx, "checkout.live_upgrade", stage, a, b))
    assert not stage["cancellation_verified"]
    rows["123"]["ends_at"] = "2020-01-01T00:00:00Z"
    asyncio.run(checkout._cancel(ctx, "checkout.live_upgrade", stage, a, b))
    assert stage["cancellation_verified"]
