"""Upgrade financial safety and recovery over the recorder's real local HTTP transport."""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime, timedelta

import aiohttp
import pytest
import test_remaining_monetization_capture as capture_fixtures
from aiohttp import web

context = capture_fixtures.context

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
PREFIX = "checkout.live_upgrade"
BASE = "/applications/100/entitlements"


def entitlement(eid, sku="400", **fields):
    return {
        "id": eid,
        "application_id": "100",
        "user_id": "200",
        "sku_id": sku,
        "type": 1,
        "guild_id": None,
        "deleted": False,
        "starts_at": (NOW - timedelta(days=1)).isoformat(),
        "ends_at": None,
        **fields,
    }


def subscription(sid="900", eid="500", sku="400", **fields):
    return {
        "id": sid,
        "user_id": "200",
        "sku_ids": [sku],
        "entitlement_ids": [eid],
        "status": 0,
        "canceled_at": None,
        "renewal_sku_ids": [sku],
        "current_period_end": (NOW + timedelta(days=30)).isoformat(),
        **fields,
    }


class Session:
    """Only the human UI and Gateway observation are replaced; REST stays real."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.start = "canceled"
        self.grant_status = 201
        self.grant_patch = {}
        self.answers = []
        self.confirmed = []
        self.probes = []
        self.actions = []
        self.writes = []
        self.rows = {}
        self.reports = []
        self.active_a = "500"
        self.interrupt = None
        self.schedule = "valid"
        self.cancel_patch = {}
        self.leave_renewing = set()
        self.lose_grant_response = False

    def seed(self, start="canceled", grant_status=201, history=0):
        self.start, self.grant_status = start, grant_status
        self.rows = {
            str(eid): entitlement(
                str(eid), type=8, deleted=True, ends_at=(NOW - timedelta(days=2)).isoformat()
            )
            for eid in range(1, history + 1)
        }
        self.reports = []
        if start != "missing":
            fields = {"ends_at": (NOW - timedelta(days=1)).isoformat()} if start == "ended" else {}
            self.rows["500"] = entitlement("500", **fields)
            self.reports = [
                subscription(
                    status=0 if start == "active" else 2,
                    canceled_at=None if start == "active" else (NOW - timedelta(hours=1)).isoformat(),
                )
            ]
        self.answers = (["RESUMED"] if start == "canceled" else [] if start == "active" else ["0.00"]) + [
            "0.00"
        ]
        self.ctx.state["baseline_ids"] = list(self.rows)
        self.ctx.state["upgrade_source"] = {
            "entitlement_id": "500",
            "subscription_id": "900",
            "period_end": (NOW + timedelta(days=30)).isoformat(),
        }
        self.ctx.save()

    def confirm(self, _prompt):
        saved = json.loads((self.ctx.evidence.directory / "manifest.json").read_text())
        stage = saved["checkout"]["live_upgrade"]
        assert stage["cancellation_required"] and not stage["cancellation_verified"]
        assert self.answers, "Recovery must not request another checkout attestation"
        answer = self.answers.pop(0)
        self.confirmed.append(answer)
        return answer

    async def probe(self, case, **_kwargs):
        self.probes.append(case)
        step = {
            PREFIX + ".prepare_a_prompted.interaction": "prepare",
            PREFIX + ".upgrade_prompted.interaction": "upgrade",
            PREFIX + ".downgrade_prompted.interaction": "downgrade",
            PREFIX + ".cancellation.interaction": "cancel",
        }.get(case)
        if step and (step == "cancel" or step not in self.actions):
            self.actions.append(step)
            stage = self.ctx.state["checkout"]["live_upgrade"]
            assert stage["cancellation_required"] and not stage["cancellation_verified"]
            if step == "prepare":
                if self.start == "canceled":
                    self.reports[0].update(status=0, canceled_at=None)
                else:
                    self.active_a = "502" if "500" in self.rows else "500"
                    self.rows[self.active_a] = entitlement(self.active_a)
                    self.reports.append(subscription("901", self.active_a))
            elif step == "upgrade":
                self.rows[self.active_a]["ends_at"] = (NOW - timedelta(seconds=1)).isoformat()
                self.rows["501"] = entitlement("501", "401")
                report = next(row for row in self.reports if self.active_a in row["entitlement_ids"])
                report.update(sku_ids=["401"], entitlement_ids=["501"], renewal_sku_ids=["401"])
            elif step == "downgrade":
                report = next(row for row in self.reports if "501" in row["entitlement_ids"])
                report["renewal_sku_ids"] = ["400"] if self.schedule != "missing" else ["401"]
                if self.schedule == "ended_b":
                    self.rows["501"]["ends_at"] = (NOW - timedelta(seconds=1)).isoformat()
            else:
                for report in self.reports:
                    if not self.leave_renewing.intersection(report["entitlement_ids"]):
                        report.update(status=2, canceled_at=NOW.isoformat())
                    if "501" in report["entitlement_ids"]:
                        report.update(self.cancel_patch)
            if self.interrupt == step:
                self.interrupt = None
                self.ctx.state["cases"][case] = {"status": "capture_failed"}
                self.ctx.save()
                raise aiohttp.ClientConnectionError("operator capture interrupted")
        self.ctx.state["cases"][case] = {"status": "observed"}
        self.ctx.save()
        return {}

    async def observe(self, case):
        self.ctx.state["cases"][case] = {"status": "observed"}
        self.ctx.save()

    async def endpoint(self, request):
        if request.method != "GET":
            body = await request.json() if request.can_read_body else None
            self.writes.append((request.method, request.path, body))
            if request.method == "POST" and request.path == BASE:
                assert self.rows["501"]["ends_at"] is None, "Cross-tier grant must run while B grants access"
                assert "downgrade" not in self.actions
                if self.lose_grant_response:
                    request.transport.close()
                    return web.Response(status=204)
                if self.grant_status != 201:
                    return web.json_response(
                        {"code": 200000, "message": "Duplicate entitlement"}, status=self.grant_status
                    )
                grant = {**entitlement("800", type=8, starts_at=None), **self.grant_patch}
                if grant["id"] not in self.rows:
                    self.rows[grant["id"]] = grant
                return web.json_response(grant, status=201)
            if request.method == "DELETE" and request.path == BASE + "/800":
                self.rows["800"]["deleted"] = True
                return web.Response(status=204)
            return web.json_response({"code": 50013}, status=403)
        if request.path.startswith("/skus/"):
            if not request.path.endswith("/subscriptions"):
                return web.json_response({"code": 10013, "message": "Unknown User"}, status=400)
            sku = request.path.split("/")[2]
            return web.json_response([row for row in self.reports if sku in row["sku_ids"]])
        if request.path != BASE:
            row = self.rows.get(request.path.rsplit("/", 1)[-1])
            return web.json_response(row if row else {"code": 10029}, status=200 if row else 404)
        rows = [
            row
            for row in self.rows.values()
            if row["sku_id"] in request.query.get("sku_ids", "400,401").split(",")
        ]
        if request.query.get("exclude_deleted") != "false":
            rows = [row for row in rows if not row["deleted"]]
        if request.query.get("exclude_ended") == "true":
            rows = [row for row in rows if row["ends_at"] is None]
        for cursor, lower in (("before", True), ("after", False)):
            if cursor in request.query:
                rows = [
                    row
                    for row in rows
                    if (
                        int(row["id"]) < int(request.query[cursor])
                        if lower
                        else int(row["id"]) > int(request.query[cursor])
                    )
                ]
        return web.json_response(
            sorted(rows, key=lambda row: int(row["id"]), reverse=True)[
                : int(request.query.get("limit", "100"))
            ]
        )


@pytest.fixture
def upgrade_context(context, monkeypatch):
    capture, ctx = context
    module = importlib.import_module("monetization_reference_upgrade")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)

    for name in (
        "monetization_reference_upgrade",
        "monetization_reference_checkout",
        "monetization_reference_lifecycle",
    ):
        target = importlib.import_module(name)
        if hasattr(target, "datetime"):
            monkeypatch.setattr(target, "datetime", Clock)
    ctx.mode = "upgrade"
    ctx.checkout.user_sku_a, ctx.checkout.user_sku_b = "A", "B"
    ctx.checkout.live_upgrade = ctx.checkout.team_discount_confirmed = True
    ctx.policy.observe = ctx.lifecycle.poll_seconds = ctx.checkout.settle_seconds = 0
    ctx.skus = [
        {"id": "400", "name": "A", "type": 5, "flags": 256},
        {"id": "401", "name": "B", "type": 5, "flags": 256},
    ]
    session = Session(ctx)
    ctx.probe, ctx.observe = session.probe, session.observe
    monkeypatch.setattr("builtins.input", session.confirm)
    return capture, module, session


@pytest.mark.parametrize(
    "start,grant_status",
    [
        ("canceled", 201),
        ("active", 400),
        ("missing", 201),
        ("ended", 400),
        ("restarted", 201),
    ],
)
async def test_upgrade_preserves_live_identity_and_finishes_with_list_proven_cancellation(
    upgrade_context, start, grant_status
):
    _, module, session = upgrade_context
    session.seed(start, grant_status, history=130)
    ctx = session.ctx
    async with capture_fixtures.server(session.endpoint) as base:
        ctx.rest.base = base
        await module.capture_upgrade(ctx)
        await module.capture_upgrade(ctx)
    stage = ctx.state["checkout"]["live_upgrade"]
    assert stage["upgrade_observed"] and stage["downgrade_observed"]
    assert stage["cancellation_required"] and stage["cancellation_verified"] and stage["done"]
    assert stage["a"] == session.active_a and stage["b"] == "501"
    assert ctx.state["cases"][PREFIX]["classification"] == "ended_by_upgrade"
    assert ctx.state["cases"][PREFIX + ".scheduled_downgrade"]["classification"] == "scheduled_downgrade_only"
    assert ctx.state["cases"][PREFIX + ".cross_tier_duplicate"]["status"] == "observed"
    assert ctx.state["cases"][PREFIX + ".cancellation"]["status"] == "observed"
    assert set(stage["a_baseline"]) == {str(eid) for eid in range(1, 131)} | (
        {"500"} if start != "missing" else set()
    )
    assert (
        stage["a_initial"]["type"]
        == session.rows[session.active_a]["type"]
        == session.rows["501"]["type"]
        == 1
    )
    assert not session.rows[session.active_a]["deleted"]
    assert session.rows[session.active_a]["ends_at"] == (NOW - timedelta(seconds=1)).isoformat()
    assert session.rows["501"]["ends_at"] is None and not session.rows["501"]["deleted"]
    assert next(row for row in session.reports if "501" in row["entitlement_ids"])["renewal_sku_ids"] == [
        "400"
    ]
    expected_ids = {session.active_a, "501"} | ({"500"} if start == "restarted" else set())
    assert set(ctx.state["manual_live_entitlement_ids"]) == expected_ids
    assert set(stage["managed_entitlements"]) == expected_ids
    assert all(eid not in ctx.state["owned_entitlements"] for eid in expected_ids)
    expected_answers = (
        ["0.00"] if start == "active" else ["RESUMED", "0.00"] if start == "canceled" else ["0.00", "0.00"]
    )
    assert session.confirmed == expected_answers and not session.answers
    expected_writes = [("POST", BASE, {"sku_id": "400", "owner_id": "200", "owner_type": 2})]
    if grant_status == 201:
        expected_writes.append(("DELETE", BASE + "/800", None))
        assert ctx.state["owned_entitlements"]["800"]["deleted"]
    else:
        assert not ctx.state["owned_entitlements"]
    assert session.writes == expected_writes
    receipts = [json.loads(line) for line in (ctx.evidence.directory / "rest.jsonl").read_text().splitlines()]
    assert [row["status"] for row in receipts if row["method"] == "POST" and row["path_query"] == BASE] == [
        grant_status
    ]


@pytest.mark.parametrize("schedule", ["missing", "ended_b"])
async def test_scheduled_downgrade_requires_renewal_a_and_retained_b_access(upgrade_context, schedule):
    _, module, session = upgrade_context
    session.seed("active", 400)
    session.schedule = schedule
    async with capture_fixtures.server(session.endpoint) as base:
        session.ctx.rest.base = base
        await module.capture_upgrade(session.ctx)
    stage = session.ctx.state["checkout"]["live_upgrade"]
    assert stage["upgrade_observed"] and not stage.get("downgrade_observed", False)
    assert session.ctx.state["cases"][PREFIX + ".scheduled_downgrade"]["status"] == "blocked"
    assert stage["cancellation_verified"]
    assert session.rows["500"]["starts_at"] < NOW.isoformat()


@pytest.mark.parametrize(
    "patch",
    [
        {"user_id": "999"},
        {"sku_ids": ["400"]},
        {"entitlement_ids": ["999"]},
        {"status": 0},
        {"canceled_at": None},
    ],
)
async def test_cancellation_requires_positive_owner_sku_entitlement_and_status_proof(upgrade_context, patch):
    _, module, session = upgrade_context
    session.seed("active", 400)
    session.cancel_patch = patch
    async with capture_fixtures.server(session.endpoint) as base:
        session.ctx.rest.base = base
        await module.capture_upgrade(session.ctx)
    stage = session.ctx.state["checkout"]["live_upgrade"]
    assert stage["cancellation_required"] and not stage["cancellation_verified"]
    assert not stage.get("done", False)
    assert session.rows["501"]["ends_at"] is None
    assert session.ctx.state["cases"][PREFIX + ".cancellation"]["status"] == "blocked"


async def test_cancellation_only_recovery_keeps_every_managed_id_not_just_latest_a_b(upgrade_context):
    _, module, session = upgrade_context
    session.seed("active", 400)
    ctx = session.ctx
    session.rows.update(
        {
            "502": entitlement("502", ends_at=(NOW - timedelta(seconds=1)).isoformat()),
            "501": entitlement("501", "401"),
        }
    )
    session.reports.append(subscription("901", "501", "401"))
    checkout = importlib.import_module("monetization_reference_checkout")
    stage = checkout._stage(ctx, "live_upgrade", ctx.skus)
    stage.update(
        a="502",
        b="501",
        cancellation_required=True,
        cancellation_started=True,
        cancellation_verified=False,
        upgrade_prompted=True,
        managed_entitlements={"500": {"sku_id": "400"}, "502": {"sku_id": "400"}, "501": {"sku_id": "401"}},
    )
    ctx.state["manual_live_entitlement_ids"] = ["500", "502", "501"]
    session.leave_renewing = {"500"}
    ctx.save()
    async with capture_fixtures.server(session.endpoint) as base:
        ctx.rest.base = base
        await module.capture_upgrade(ctx)
        assert not stage["cancellation_verified"]
        assert ctx.state["cases"][PREFIX + ".cancellation"]["status"] == "blocked"
        session.leave_renewing.clear()
        await module.capture_upgrade(ctx)
    assert stage["cancellation_verified"]
    assert session.probes == [PREFIX + ".cancellation.interaction"] * 2
    assert not session.confirmed and not session.writes and not ctx.state["owned_entitlements"]
    assert set(stage["managed_entitlements"]) == {"500", "502", "501"}


@pytest.mark.parametrize(
    "interrupt,grant_status",
    [
        ("prepare", 201),
        ("upgrade", 201),
        ("downgrade", 201),
        ("grant", 201),
        ("grant", 400),
    ],
)
async def test_interrupted_action_recovers_from_saved_receipts_without_second_checkout_or_grant(
    upgrade_context, interrupt, grant_status
):
    capture, module, session = upgrade_context
    session.seed("canceled", grant_status)
    ctx = session.ctx
    session.interrupt = interrupt
    interaction_cases = {
        phase: PREFIX + f".{key}_prompted.interaction"
        for phase, key in (("prepare", "prepare_a"), ("upgrade", "upgrade"), ("downgrade", "downgrade"))
    }
    request = ctx.request

    async def interrupt_after_receipt(case, method, path, **kwargs):
        result = await request(case, method, path, **kwargs)
        if session.interrupt == "grant" and method == "POST" and path == BASE:
            session.interrupt = None
            raise aiohttp.ClientConnectionError("interrupted after persisted HTTP receipt")
        return result

    ctx.request = interrupt_after_receipt
    async with capture_fixtures.server(session.endpoint) as base:
        ctx.rest.base = base
        with pytest.raises(aiohttp.ClientConnectionError):
            await module.capture_upgrade(ctx)
        ctx.state = json.loads((ctx.evidence.directory / "manifest.json").read_text())
        assert ctx.state["checkout"]["live_upgrade"]["cancellation_required"]
        if interrupt in interaction_cases:
            failed_case = interaction_cases[interrupt]
            assert ctx.state["cases"][failed_case]["status"] == "capture_failed"
            assert session.probes.count(failed_case) == 1
        capture.reconcile(ctx)
        await module.capture_upgrade(ctx)
        await module.capture_upgrade(ctx)
    stage = ctx.state["checkout"]["live_upgrade"]
    assert stage["cancellation_verified"] and stage["done"]
    assert session.confirmed == ["RESUMED", "0.00"]
    assert session.actions == ["prepare", "upgrade", "downgrade", "cancel"]
    for phase, case in interaction_cases.items():
        assert ctx.state["cases"][case]["status"] == "observed"
        assert session.probes.count(case) == (2 if interrupt == phase else 1)
    assert [method for method, path, _body in session.writes if path == BASE] == ["POST"]
    assert not any(path.endswith("/consume") for _method, path, _body in session.writes)
    assert all(path == BASE or path == BASE + "/800" for _method, path, _body in session.writes)
    if grant_status == 201:
        assert ctx.state["owned_entitlements"]["800"]["deleted"]
    else:
        assert not ctx.state["owned_entitlements"]


async def test_unknown_grant_outcome_stops_manual_work_and_is_not_replayed(upgrade_context):
    capture, module, session = upgrade_context
    session.seed("active")
    session.lose_grant_response = True
    ctx = session.ctx
    async with capture_fixtures.server(session.endpoint) as base:
        ctx.rest.base = base
        with pytest.raises(aiohttp.ClientError):
            await module.capture_upgrade(ctx)
        assert ctx.state["pending_write"]["method"] == "POST"
        capture.reconcile(ctx)
        with pytest.raises(RuntimeError):
            await module.capture_upgrade(ctx)
    assert session.actions == ["upgrade"]
    assert session.ctx.state["checkout"]["live_upgrade"]["cancellation_required"]
    assert not session.ctx.state["checkout"]["live_upgrade"]["cancellation_verified"]
    assert [method for method, _path, _body in session.writes] == ["POST"]
    assert not ctx.state["owned_entitlements"]


@pytest.mark.parametrize(
    "receipt",
    [
        {"id": "500", "type": 8},
        {"id": "501", "type": 1},
        {"id": "700", "type": 8},
        {"id": "800", "type": 1},
        {"id": "800", "sku_id": "401"},
    ],
)
async def test_create_receipt_cannot_turn_real_or_manual_rows_into_cleanup_owned_ids(
    upgrade_context, receipt
):
    _, module, session = upgrade_context
    session.seed("active")
    session.grant_patch = receipt
    ctx = session.ctx
    ctx.state["manual_test_purchase_ids"] = ["700"]
    async with capture_fixtures.server(session.endpoint) as base:
        ctx.rest.base = base
        with pytest.raises(RuntimeError):
            await module.capture_upgrade(ctx)
    assert not ctx.state["owned_entitlements"]
    assert [method for method, _path, _body in session.writes] == ["POST"]
    assert session.actions == ["upgrade"]
    assert not session.rows["500"]["deleted"] and not session.rows["501"]["deleted"]
    assert ctx.state["checkout"]["live_upgrade"]["cancellation_required"]
    assert not ctx.state["checkout"]["live_upgrade"]["cancellation_verified"]


@pytest.mark.parametrize("start", ["canceled", "active"])
async def test_nonzero_attestation_never_advances_checkout_and_still_cancels_managed_a(
    upgrade_context, start
):
    _, module, session = upgrade_context
    session.seed(start)
    session.answers = ["1.00"]
    ctx = session.ctx
    async with capture_fixtures.server(session.endpoint) as base:
        ctx.rest.base = base
        await module.capture_upgrade(ctx)
    stage = ctx.state["checkout"]["live_upgrade"]
    assert not stage.get("upgrade_observed", False)
    assert not stage.get("downgrade_observed", False)
    assert stage["cancellation_required"] and stage["cancellation_verified"]
    assert session.actions == ["cancel"] and not session.writes
    assert "501" not in session.rows
    assert not ctx.state["owned_entitlements"]


async def test_expired_source_cannot_start_or_reopen_checkout(upgrade_context):
    _, module, session = upgrade_context
    session.seed("missing")
    session.ctx.state["upgrade_source"]["period_end"] = (NOW - timedelta(seconds=1)).isoformat()
    await module.capture_upgrade(session.ctx)
    assert not session.confirmed and not session.probes and not session.writes
    assert session.ctx.state["cases"][PREFIX]["status"] == "blocked"


async def test_expired_source_preserves_cancellation_only_recovery(upgrade_context):
    _, module, session = upgrade_context
    session.seed("active")
    ctx = session.ctx
    ctx.state["upgrade_source"]["period_end"] = (NOW - timedelta(seconds=1)).isoformat()
    stage = module._stage(ctx, "live_upgrade", ctx.skus)
    stage.update(
        a="500",
        cancellation_required=True,
        managed_entitlements={"500": {"sku_id": "400"}},
    )
    async with capture_fixtures.server(session.endpoint) as base:
        ctx.rest.base = base
        await module.capture_upgrade(ctx)
    assert stage["cancellation_started"] and stage["cancellation_verified"]
    assert not session.confirmed and not session.writes
    assert session.actions == ["cancel"]
