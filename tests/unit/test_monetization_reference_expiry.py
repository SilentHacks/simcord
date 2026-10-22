"""Expiry-only workflow: reused evidence, real HTTP, no checkout or live writes."""

import asyncio
import copy
import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aiohttp import web
from test_monetization_reference_validation import Bundle
from test_remaining_monetization_capture import context as capture_context
from test_remaining_monetization_capture import server

context = capture_context

END = datetime(2026, 10, 21, 12, tzinfo=UTC)
ACTIVE = {
    "id": "500",
    "application_id": "100",
    "user_id": "200",
    "sku_id": "400",
    "type": 1,
    "guild_id": None,
    "deleted": False,
    "starts_at": "2026-10-20T00:00:00Z",
    "ends_at": None,
}
SUB = {
    "id": "900",
    "user_id": "200",
    "sku_ids": ["400"],
    "entitlement_ids": ["500"],
    "status": 2,
    "canceled_at": "2026-10-20T23:00:00Z",
    "current_period_end": END.isoformat(),
}


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    module = importlib.import_module("monetization_reference_expiry")
    directory = tmp_path / "source"
    directory.mkdir()
    bundle = Bundle(directory)
    bundle.manifest.update(
        skus=[
            {"id": "400", "name": "A", "type": 5, "flags": 256},
            {"id": "401", "name": "B", "type": 5, "flags": 256},
        ],
        config={"user_sku_b": "B", "channel": "parity"},
        checkout={
            "live_upgrade": {
                "a": "500",
                "cancellation_verified": True,
                "identity": {"application_id": "100", "user_id": "200", "sku_ids": ["400", "401"]},
            }
        },
    )
    bundle.request("GET", "/applications/100/entitlements/500", copy.deepcopy(ACTIVE))
    bundle.request(
        "GET", "/skus/400/subscriptions", [copy.deepcopy(SUB)], query={"user_id": "200", "limit": "100"}
    )
    bundle.record(
        "interactions",
        sequence=1,
        payload={
            "id": "700",
            "application_id": "100",
            "guild_id": "300",
            "member": {"user": {"id": "200"}},
            "entitlements": [copy.deepcopy(ACTIVE)],
        },
    )
    assert bundle.save()["valid"]
    return module, bundle


def test_source_binds_actual_subscription_and_before_interaction(source):
    module, bundle = source
    proof = module.load_expiry_source(bundle.directory)
    assert proof["entitlement_id"] == "500" and proof["subscription_id"] == "900"
    assert proof["baseline"]["source_interaction_ref"] == "interactions.jsonl:1"
    assert proof["baseline"]["before_probe_complete"] is True
    bundle.rows["rest"][2]["response"][0]["status"] = 0
    bundle.save()
    with pytest.raises(RuntimeError):
        module.load_expiry_source(bundle.directory)


def test_source_requires_actual_before_presence_not_just_manifest_flag(source):
    module, bundle = source
    bundle.rows["interactions"][0]["payload"]["entitlements"] = []
    bundle.save()
    with pytest.raises(RuntimeError):
        module.load_expiry_source(bundle.directory)


@pytest.mark.parametrize("late", [False, True])
@pytest.mark.parametrize("interrupt", [None, "after_probe", "after_create", "unhealthy_window"])
async def test_expiry_requires_only_one_post_end_probe_and_never_replays_grant(
    context, source, monkeypatch, late, interrupt
):
    _, ctx = context
    module, bundle = source
    lifecycle = importlib.import_module("monetization_reference_lifecycle")
    proof = module.load_expiry_source(bundle.directory)
    ctx.state["expiry_source"] = proof
    ctx.state["watch_baselines"] = {"500": copy.deepcopy(proof["baseline"])}
    ctx.state["baseline_ids"] = ["500"]
    ctx.skus = bundle.manifest["skus"]
    ctx.args.mode, ctx.args.watch_seconds, ctx.args.poll_seconds = "watch", 1, 0.01
    ctx.args.watch_entitlement, ctx.args.consume_history = ["500"], []
    ctx.args.live_lifecycle_confirmed = True
    ctx.owner_id, ctx.owner_type = "200", 2
    ended = {**ACTIVE, "ends_at": END.isoformat()}
    grant = {**ACTIVE, "id": "501", "type": 8, "starts_at": None}
    polls, writes, probes = [], [], []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return END + timedelta(seconds=1)

    monkeypatch.setattr(lifecycle, "datetime", Clock)

    interrupted = False

    async def endpoint(request):
        if request.method == "POST":
            writes.append((request.method, request.path))
            assert request.path == "/applications/100/entitlements"
            assert await request.json() == {"sku_id": "400", "owner_id": "200", "owner_type": 2}
            return web.json_response(grant, status=201)
        if request.method == "DELETE":
            writes.append((request.method, request.path))
            assert request.path == "/applications/100/entitlements/501"
            grant["deleted"] = True
            return web.Response(status=204)
        if request.path.startswith("/skus/"):
            return web.json_response([SUB])
        if request.path.endswith("/500"):
            polls.append(1)
            return web.json_response(ended if late or len(polls) > 1 else ACTIVE)
        if request.path.endswith("/501"):
            return web.json_response(grant)
        return web.json_response([] if request.query.get("exclude_ended") == "true" else [ended])

    async def probe(case):
        probes.append(case)
        assert case.endswith(".after.interaction")
        ctx.state["cases"][case] = {"status": "observed"}

    async def observe(case):
        nonlocal interrupted
        if interrupt == "unhealthy_window" and case.endswith(".transition.window"):
            ctx.state["cases"][case] = {"status": "observed" if interrupted else "capture_failed"}
            interrupted = True
            return
        if not interrupted and (
            (interrupt == "after_probe" and case.endswith(".transition.window"))
            or (interrupt == "after_create" and case.endswith(".duplicate_after_natural_end.window"))
        ):
            interrupted = True
            raise asyncio.CancelledError

    ctx.probe, ctx.observe = probe, observe
    async with server(endpoint) as base:
        ctx.rest.base = base
        if interrupt and interrupt != "unhealthy_window":
            with pytest.raises(asyncio.CancelledError):
                await lifecycle.capture_lifecycle(ctx)
        await lifecycle.capture_lifecycle(ctx)
        await lifecycle.capture_lifecycle(ctx)
    assert probes == ["lifecycle.watch.500.after.interaction"]
    assert writes == [
        ("POST", "/applications/100/entitlements"),
        ("DELETE", "/applications/100/entitlements/501"),
    ]
    assert ctx.state["owned_entitlements"]["501"]["deleted"]
    assert "500" not in ctx.state["owned_entitlements"]
    assert ctx.state["cases"]["lifecycle.natural_expiry"]["transition"] == "natural_end"
    assert ctx.state["cases"]["lifecycle.duplicate_after_natural_end"]["status"] == "observed"
    assert "lifecycle.watch.500.before_projection" not in ctx.state["cases"]


@pytest.mark.parametrize(
    "change", [{"status": 0}, {"current_period_end": (END + timedelta(days=30)).isoformat()}]
)
async def test_resumed_or_changed_subscription_stops_without_mutation(context, source, change):
    _, ctx = context
    module, bundle = source
    ctx.state["expiry_source"] = module.load_expiry_source(bundle.directory)

    async def endpoint(request):
        assert request.method == "GET"
        return web.json_response([{**SUB, **change}])

    async with server(endpoint) as base:
        ctx.rest.base = base
        assert not await module.check_expiry_subscription(ctx, "expiry.preflight")
    assert not ctx.state["owned_entitlements"]


async def test_too_early_preflight_does_not_create_gateway_command(context, source, monkeypatch):
    _, ctx = context
    module, bundle = source
    ctx.state["expiry_source"] = module.load_expiry_source(bundle.directory)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return END - timedelta(hours=1)

    monkeypatch.setattr(module, "datetime", Clock)

    async def endpoint(request):
        assert request.method == "GET" and request.path == "/skus/400/subscriptions"
        return web.json_response([SUB])

    async with server(endpoint) as base:
        ctx.rest.base = base
        assert not await module.prepare_expiry(ctx)
    assert not ctx.state.get("watch_baselines") and not ctx.state.get("temporary_commands")


@pytest.mark.parametrize(
    "rows,verified", [([], False), ([{**SUB, "status": 1, "entitlement_ids": []}], True)]
)
async def test_missing_reporting_preserves_capture_and_inactive_can_clear_grant_ids(
    context, source, rows, verified
):
    _, ctx = context
    module, bundle = source
    ctx.state["expiry_source"] = module.load_expiry_source(bundle.directory)

    async def endpoint(request):
        assert request.method == "GET"
        return web.json_response(rows)

    async with server(endpoint) as base:
        ctx.rest.base = base
        assert await module.check_expiry_subscription(ctx, "expiry.preflight")
    assert ctx.state["expiry_billing_verified"] is verified
    assert not ctx.state["owned_entitlements"]
