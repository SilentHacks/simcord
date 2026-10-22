"""Exercise journal ownership and pagination against actual local HTTP transport."""

from __future__ import annotations

import importlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@asynccontextmanager
async def server(handler):
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    try:
        yield f"http://127.0.0.1:{runner.addresses[0][1]}"
    finally:
        await runner.cleanup()


@pytest.fixture
async def context(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    module = importlib.import_module("capture_remaining_monetization")
    args = SimpleNamespace(
        user=200,
        guild=300,
        other_user=None,
        other_guild=None,
        channel="parity",
        request_interval=0,
        max_pages=10,
    )
    state = {
        "schema_version": 2,
        "status": "running",
        "application_id": "100",
        "user_id": "200",
        "guild_id": "300",
        "cases": {},
        "sections": {},
        "owned_entitlements": {},
        "pending_write": None,
        "cleanup_errors": [],
        "temporary_command_id": None,
        "baseline_ids": [],
        "source_bundles": [],
    }
    ctx = module.Capture(args, module.Evidence(tmp_path / "capture", "dummy-local-secret"), state)
    ctx.app, ctx.base = {"id": "100"}, "/applications/100/entitlements"
    async with aiohttp.ClientSession() as http:
        ctx.rest = module.Rest(http, ctx.evidence)
        yield module, ctx
    await ctx.client.close()


async def test_successful_write_is_journaled_before_network_and_cleanup_is_owned(context):
    module, ctx = context
    operations = []

    async def endpoint(request):
        journal = json.loads((ctx.evidence.directory / "manifest.json").read_text())
        assert journal["pending_write"]["method"] == request.method
        operations.append(request.method)
        if request.method == "POST":
            return web.json_response(
                {"id": "500", "sku_id": "400", "application_id": "100", "user_id": "200", "type": 8}
            )
        return web.Response(status=204)

    async with server(endpoint) as base:
        ctx.rest.base = base
        eid = await ctx.create("fixture.create", {"id": "400"})
        assert eid == "500"
        saved = json.loads((ctx.evidence.directory / "manifest.json").read_text())
        assert saved["pending_write"] is None and saved["owned_entitlements"][eid]["deleted"] is False
        await ctx.remove("cleanup.fixture", eid)
        assert operations == ["POST", "DELETE"]
        assert ctx.state["owned_entitlements"][eid]["deleted"] is True
        assert ctx.state["owned_entitlements"][eid]["create_ref"] == "rest.jsonl:1"
        resumed = module.Evidence(ctx.evidence.directory, "dummy-local-secret", resume=True)
        assert (
            resumed.record("actions", event="resume") == f"actions.jsonl:{ctx.evidence.counts['actions'] + 1}"
        )


async def test_lost_write_response_is_never_replayed_or_adopted(context):
    module, ctx = context
    attempts = []

    async def endpoint(request):
        attempts.append(await request.json())
        request.transport.close()
        return web.Response(status=204)

    async with server(endpoint) as base:
        ctx.rest.base = base
        with pytest.raises(aiohttp.ClientError):
            await ctx.create("fixture.lost", {"id": "400"})
        assert len(attempts) == 1
        assert ctx.state["pending_write"]["method"] == "POST"
        module.reconcile(ctx)
        assert ctx.state["uncertain_writes"] and not ctx.state["owned_entitlements"]
        with pytest.raises(RuntimeError, match="unresolved"):
            await ctx.create("fixture.must_not_replay", {"id": "400"})
        assert len(attempts) == 1
        assert ctx.state["uncertain_writes"][0]["case"] == "fixture.lost"
        rows = [json.loads(line) for line in (ctx.evidence.directory / "rest.jsonl").read_text().splitlines()]
        assert rows[-1]["status"] is None and rows[-1]["outcome_unknown"] is True


async def test_preexisting_or_foreign_resource_never_acquires_cleanup_provenance(context):
    _, ctx = context
    ctx.state["baseline_ids"] = ["500"]

    async def endpoint(request):
        return web.json_response(
            {"id": "500", "sku_id": "400", "application_id": "100", "user_id": "200", "type": 8}
        )

    async with server(endpoint) as base:
        ctx.rest.base = base
        with pytest.raises(RuntimeError, match="preexisting"):
            await ctx.create("fixture.preexisting", {"id": "400"})
        assert not ctx.state["owned_entitlements"]
        with pytest.raises(RuntimeError, match="positive create receipt"):
            await ctx.remove("cleanup.foreign", "500")
        assert ctx.evidence.counts["requests"] == 1


@pytest.mark.parametrize(
    "patch,manual_ids",
    [
        ({"type": 1}, []),
        ({}, ["500"]),
        ({"sku_id": "401"}, []),
        ({"user_id": "201"}, []),
    ],
)
async def test_successful_create_cannot_adopt_manual_or_wrong_fixture(context, patch, manual_ids):
    _, ctx = context
    ctx.state["manual_live_entitlement_ids"] = manual_ids
    ctx.args.other_user = 201
    writes = []

    async def endpoint(request):
        writes.append(request.method)
        return web.json_response(
            {"id": "500", "sku_id": "400", "application_id": "100", "user_id": "200", "type": 8, **patch}
        )

    async with server(endpoint) as base:
        ctx.rest.base = base
        with pytest.raises(RuntimeError):
            await ctx.create("fixture.invalid_receipt", {"id": "400"})
        assert not ctx.state["owned_entitlements"]
        assert ctx.state["pending_write"] and ctx.state["status"] == "uncertain_write"
        with pytest.raises(RuntimeError, match="positive create receipt"):
            await ctx.remove("cleanup.unowned", "500")
        assert writes == ["POST"]


@pytest.mark.parametrize("descending", [True, False])
async def test_walk_captures_both_server_orderings_without_losing_middle_rows(context, descending):
    _, ctx = context
    expected = {str(value) for value in range(1, 206)}

    async def endpoint(request):
        ids = list(range(1, 206))
        if "before" in request.query:
            ids = [value for value in ids if value < int(request.query["before"])]
        if "after" in request.query:
            ids = [value for value in ids if value > int(request.query["after"])]
        ids.sort(reverse=descending)
        return web.json_response([{"id": str(value)} for value in ids[: int(request.query["limit"])]])

    async with server(endpoint) as base:
        ctx.rest.base = base
        rows = await ctx.walk("pagination.measure", {"exclude_deleted": "false"})
        assert {row["id"] for row in rows} == expected
        refs = [
            json.loads(line) for line in (ctx.evidence.directory / "requests.jsonl").read_text().splitlines()
        ]
        assert any("before" in row["query"] for row in refs) and any("after" in row["query"] for row in refs)


async def test_ignored_cursor_does_not_claim_complete_history(context):
    _, ctx = context

    async def endpoint(request):
        return web.json_response([{"id": str(value)} for value in range(1, 101)])

    async with server(endpoint) as base:
        ctx.rest.base = base
        assert await ctx.walk("pagination.stuck", {}) is None
        assert ctx.state["cases"]["pagination.stuck.traversal"]["status"] == "blocked"
        assert all(
            json.loads(line)["method"] == "GET"
            for line in (ctx.evidence.directory / "requests.jsonl").read_text().splitlines()
        )


async def test_recorded_response_recovers_without_resubmitting_create(context):
    module, ctx = context
    intent = {
        "case": "fixture.receipt",
        "method": "POST",
        "path": ctx.base,
        "body": {"sku_id": "400", "owner_id": "200", "owner_type": 2},
        "request_start_line": 1,
    }
    ctx.state["pending_write"] = intent
    ctx.evidence.action = intent["case"]

    async def endpoint(request):
        return web.json_response(
            {"id": "500", "sku_id": "400", "application_id": "100", "user_id": "200", "type": 8}
        )

    async with server(endpoint) as base:
        ctx.rest.base = base
        await ctx.rest.request("POST", ctx.base, body=intent["body"])
        module.reconcile(ctx)
        assert ctx.evidence.counts["requests"] == 1
        assert ctx.state["pending_write"] is None and ctx.state["uncertain_writes"] == []
        assert ctx.state["owned_entitlements"]["500"]["create_ref"] == "rest.jsonl:1"


async def test_checkout_baseline_traverses_large_history_before_requesting_interaction(context):
    _, ctx = context
    checkout = importlib.import_module("monetization_reference_checkout")
    ctx.args.user_sku_a, ctx.args.user_sku_b = "A", "B"
    ctx.skus = [
        {"id": "400", "name": "A", "type": 5, "flags": 256},
        {"id": "401", "name": "B", "type": 5, "flags": 256},
    ]

    async def endpoint(request):
        assert request.method == "GET", "No checkout or entitlement mutation is authorized by a baseline"
        ids = list(range(1, 151)) if request.query["sku_ids"] == "400" else []
        if "before" in request.query:
            ids = [eid for eid in ids if eid < int(request.query["before"])]
        if "after" in request.query:
            ids = [eid for eid in ids if eid > int(request.query["after"])]
        return web.json_response(
            [
                {
                    "id": str(eid),
                    "application_id": "100",
                    "sku_id": "400",
                    "user_id": "200",
                    "type": 8,
                    "deleted": True,
                }
                for eid in sorted(ids, reverse=True)[:100]
            ]
        )

    class OperatorNotPresent(BaseException):
        pass

    async def probe(case):
        raise OperatorNotPresent

    ctx.probe = probe
    async with server(endpoint) as base:
        ctx.rest.base = base
        with pytest.raises(OperatorNotPresent):
            await checkout._live_upgrade(ctx)
    stage = ctx.state["checkout"]["live_upgrade"]
    assert set(stage["a_baseline"]) == {str(eid) for eid in range(1, 151)}
    assert stage["b_baseline"] == []
    assert not stage.get("subscribe_prompted") and not stage.get("cancellation_required")
    assert len(stage["a_baseline_refs"]) > 1
    fresh = {
        "id": "151",
        "application_id": "100",
        "sku_id": "400",
        "user_id": "200",
        "type": 8,
        "deleted": False,
        "starts_at": None,
        "ends_at": None,
    }
    selected = checkout.select_new_purchase(
        stage["a_baseline_rows"] + [fresh], stage["a_baseline"], "100", "200", "400", 8
    )
    assert selected["id"] == "151"


async def test_cancellation_only_recovery_finds_real_purchase_without_new_checkout(context):
    _, ctx = context
    checkout = importlib.import_module("monetization_reference_checkout")
    ctx.args.user_sku_a, ctx.args.user_sku_b = "A", "B"
    ctx.skus = [
        {"id": "400", "name": "A", "type": 5, "flags": 256},
        {"id": "401", "name": "B", "type": 5, "flags": 256},
    ]
    stage = checkout._stage(ctx, "live_upgrade", ctx.skus)
    stage.update(
        cancellation_required=True,
        cancellation_started=True,
        subscribe_prompted=True,
        a_baseline=["499"],
        b_baseline=[],
    )
    row = {
        "id": "500",
        "application_id": "100",
        "sku_id": "400",
        "user_id": "200",
        "type": 1,
        "deleted": False,
        "starts_at": "2026-01-01T00:00:00Z",
        "ends_at": None,
    }
    subscription = {
        "id": "900",
        "user_id": "200",
        "sku_ids": ["400"],
        "entitlement_ids": ["500"],
        "status": 0,
        "canceled_at": None,
    }
    writes = []

    async def endpoint(request):
        if request.method != "GET":
            writes.append(request.method)
            return web.Response(status=405)
        if "/subscriptions" in request.path:
            if request.path.endswith("/subscriptions"):
                return web.json_response([subscription])
            return web.json_response({"message": "Unknown User", "code": 10013}, status=400)
        if request.path.endswith("/500"):
            return web.json_response(row)
        return web.json_response([row] if request.query["sku_ids"] == "400" else [])

    async def probe(case):
        assert case.endswith(".cancellation.interaction"), "Recovery must never return to checkout or upgrade"

    async def observe(case):
        pass

    ctx.probe, ctx.observe = probe, observe
    async with server(endpoint) as base:
        ctx.rest.base = base
        await checkout._live_upgrade(ctx)
        assert not stage["cancellation_verified"]
        subscription.update(status=2, canceled_at="2026-10-21T23:35:00Z")
        await checkout._live_upgrade(ctx)
    assert stage["a"] == "500" and stage["cancellation_verified"]
    assert ctx.state["cases"]["checkout.live_upgrade.cancellation"]["status"] == "observed"
    assert "reason" not in ctx.state["cases"]["checkout.live_upgrade.cancellation"]
    assert ctx.state["manual_live_entitlement_ids"] == ["500"]
    assert not ctx.state["owned_entitlements"] and not writes
    assert not stage.get("upgrade_prompted")
