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
    transport = importlib.import_module("monetization_reference_transport")
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
    config = module.CaptureConfig.from_namespace(args)
    ctx = module.Capture(config, transport.Evidence(tmp_path / "capture", "dummy-local-secret"), state)
    ctx.app, ctx.base = {"id": "100"}, "/applications/100/entitlements"
    async with aiohttp.ClientSession() as http:
        ctx.rest = transport.Rest(http, ctx.evidence)
        yield module, ctx
    await ctx.client.close()


async def test_successful_write_is_journaled_before_network_and_cleanup_is_owned(context):
    _, ctx = context
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
        transport = importlib.import_module("monetization_reference_transport")
        resumed = transport.Evidence(ctx.evidence.directory, "dummy-local-secret", resume=True)
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
        ({"type": 4}, []),
        ({"type": True}, []),
        ({"id": 500}, []),
        ({"id": "0"}, []),
        ({"deleted": True}, []),
        ({"guild_id": "300"}, []),
        ({}, ["500"]),
        ({"sku_id": "401"}, []),
        ({"user_id": "201"}, []),
    ],
)
async def test_successful_create_cannot_adopt_manual_or_wrong_fixture(context, patch, manual_ids):
    _, ctx = context
    ctx.state["manual_live_entitlement_ids"] = manual_ids
    ctx.identity.other_user = 201
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
    ctx.checkout.user_sku_a, ctx.checkout.user_sku_b = "A", "B"
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
    ctx.checkout.user_sku_a, ctx.checkout.user_sku_b = "A", "B"
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


async def test_intent_without_request_is_recoverable_without_inventing_ownership(context, monkeypatch):
    import asyncio

    module, ctx = context

    async def interrupted(*args, **kwargs):
        saved = json.loads((ctx.evidence.directory / "manifest.json").read_text())
        assert saved["pending_write"]["method"] == "POST"
        raise asyncio.CancelledError

    monkeypatch.setattr(ctx.rest, "request", interrupted)
    with pytest.raises(asyncio.CancelledError):
        await ctx.create("interrupted.before_request", {"id": "400"})
    assert not ctx.evidence.counts.get("requests")
    module.reconcile(ctx)
    assert ctx.state["pending_write"] is None
    assert not ctx.state["uncertain_writes"] and not ctx.state["owned_entitlements"]
    assert ctx.state["cases"]["interrupted.before_request"]["status"] == "blocked"


async def test_remote_success_before_ownership_checkpoint_recovers_only_receipt(context, monkeypatch):
    import asyncio

    module, ctx = context
    settle = ctx.settle_write
    operations = []

    async def endpoint(request):
        operations.append(request.method)
        return web.json_response(
            {"id": "500", "sku_id": "400", "application_id": "100", "user_id": "200", "type": 8}
        )

    def interrupted(*args):
        raise asyncio.CancelledError

    async with server(endpoint) as base:
        ctx.rest.base = base
        monkeypatch.setattr(ctx, "settle_write", interrupted)
        with pytest.raises(asyncio.CancelledError):
            await ctx.create("interrupted.after_receipt", {"id": "400"})
        assert not ctx.state["owned_entitlements"]
        monkeypatch.setattr(ctx, "settle_write", settle)
        module.reconcile(ctx)
    assert operations == ["POST"]
    assert ctx.state["owned_entitlements"]["500"]["create_ref"] == "rest.jsonl:1"
    assert not ctx.state["uncertain_writes"] and ctx.state["pending_write"] is None


async def test_lost_cleanup_write_is_retained_and_never_replayed(context):
    module, ctx = context
    operations = []

    async def endpoint(request):
        operations.append(request.method)
        if request.method == "POST":
            return web.json_response(
                {"id": "500", "sku_id": "400", "application_id": "100", "user_id": "200", "type": 8}
            )
        request.transport.close()
        return web.Response(status=204)

    async with server(endpoint) as base:
        ctx.rest.base = base
        await ctx.create("cleanup.fixture", {"id": "400"})
        await ctx.cleanup()
        assert not ctx.cleaning
        module.reconcile(ctx)
        await ctx.cleanup()
    assert operations == ["POST", "DELETE"]
    assert ctx.state["uncertain_writes"]
    assert not ctx.state["owned_entitlements"]["500"]["deleted"]
    assert ctx.state["cleanup_errors"]


async def test_earlier_429_cannot_resolve_later_unknown_attempt(context):
    module, ctx = context
    attempts = []

    async def endpoint(request):
        attempts.append(request.method)
        if len(attempts) == 1:
            return web.json_response({"retry_after": 0}, status=429)
        request.transport.close()
        return web.Response(status=204)

    async with server(endpoint) as base:
        ctx.rest.base = base
        with pytest.raises(aiohttp.ClientError):
            await ctx.create("retry.then_unknown", {"id": "400"})
        module.reconcile(ctx)
    assert attempts == ["POST", "POST"]
    assert ctx.state["uncertain_writes"] and not ctx.state["owned_entitlements"]


async def test_manifest_ownership_flag_without_receipt_never_authorizes_cleanup(context):
    _, ctx = context
    ctx.state["owned_entitlements"]["500"] = {
        "deleted": False,
        "sku_id": "400",
        "owner_type": 2,
        "owner_id": "200",
        "create_ref": "rest.jsonl:1",
    }
    await ctx.cleanup()
    assert not ctx.evidence.counts.get("requests")
    assert ctx.state["cleanup_errors"]


async def test_authenticated_application_mismatch_stops_before_mutation(context):
    module, ctx = context
    operations = []

    async def endpoint(request):
        operations.append(request.method)
        return web.json_response({"id": "999", "name": "Wrong application"})

    async with server(endpoint) as base:
        ctx.rest.base = base
        with pytest.raises(RuntimeError, match="different application"):
            await module.authenticate_application(ctx)
    assert operations == ["GET"]
    assert ctx.app == {"id": "100"}


async def test_cleanup_configuration_ignores_fresh_source_and_checkout_prerequisites(context):
    module, ctx = context
    parser = module.build_parser()
    original = parser.parse_args(["--guild", "300", "--user", "200"])
    original.live_upgrade = True
    original.team_discount_confirmed = False
    original.upgrade_from = "/missing/source"
    original.watch_seconds = -1
    ctx.state["config"] = {
        key: value for key, value in vars(original).items() if key not in {"resume", "output", "cleanup_only"}
    }
    ctx.save()
    args = parser.parse_args(["--resume", str(ctx.evidence.directory), "--cleanup-only"])
    state, expiry, upgrade, config = module.prepare_local(parser, args, {"--resume", "--cleanup-only"})
    assert state["application_id"] == "100" and expiry is None and upgrade is None
    assert isinstance(config, module.CleanupConfig)
    assert config.identity.user == 200 and not hasattr(config, "checkout")
    args = parser.parse_args(["--resume", str(ctx.evidence.directory), "--cleanup-only", "--user", "201"])
    with pytest.raises(SystemExit):
        module.prepare_local(parser, args, {"--resume", "--cleanup-only", "--user"})


async def test_evidence_never_persists_raw_secrets_and_resume_rejects_truncation(context):
    _, ctx = context
    transport = importlib.import_module("monetization_reference_transport")
    ctx.evidence.record(
        "gateway",
        token="interaction-secret",
        nested={"authorization": "another-secret"},
        message="dummy-local-secret",
    )
    ctx.state["token"] = "manifest-secret"
    ctx.save()
    combined = "".join(path.read_text() for path in ctx.evidence.directory.iterdir() if path.is_file())
    for secret in ("dummy-local-secret", "interaction-secret", "another-secret", "manifest-secret"):
        assert secret not in combined
    with (ctx.evidence.directory / "rest.jsonl").open("a") as output:
        output.write('{"status":')
    with pytest.raises(RuntimeError, match="Truncated"):
        transport.Evidence(ctx.evidence.directory, "replacement-secret", resume=True)


@pytest.mark.parametrize("method", ["DELETE", "PUT"])
async def test_disconnect_after_write_dispatch_never_retries_idempotent_verbs(context, method):
    _, ctx = context
    remote_mutations = []

    async def endpoint(request):
        remote_mutations.append(request.method)
        request.transport.close()
        return web.Response(status=204)

    async with server(endpoint) as base:
        ctx.rest.base = base
        with pytest.raises(aiohttp.ClientError):
            await ctx.request("unknown." + method, method, ctx.base + "/500")
    assert remote_mutations == [method]
    assert ctx.evidence.counts["requests"] == 1
    receipts = (ctx.evidence.directory / "rest.jsonl").read_text().splitlines()
    assert len(receipts) == 1
    assert json.loads(receipts[0])["outcome_unknown"] is True
    assert ctx.state["pending_write"]["method"] == method


@pytest.mark.parametrize("name", ["capture_monetization_reference", "capture_remaining_monetization"])
def test_tooling_preflight_rejects_missing_public_middleware_before_credentials(
    tmp_path, monkeypatch, capsys, name
):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    module = importlib.import_module(name)

    class WithoutMiddleware:
        def __init__(self):
            raise AssertionError("No session may be created")

    def forbidden(*args, **kwargs):
        raise AssertionError("No credentials may be requested")

    monkeypatch.setattr(aiohttp, "ClientSession", WithoutMiddleware)
    monkeypatch.setattr(module.getpass, "getpass", forbidden)
    output = tmp_path / "unused"
    monkeypatch.setattr(
        module.sys, "argv", ["capture", "--guild", "300", "--user", "200", "--output", str(output)]
    )
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert "aiohttp>=3.12" in capsys.readouterr().err
    assert not output.exists()
    monkeypatch.setattr(module.sys, "argv", ["capture", "--help"])
    with pytest.raises(SystemExit) as help_exit:
        module.main()
    assert help_exit.value.code == 0


async def test_failure_to_fsync_intent_never_dispatches_request(context, monkeypatch):
    _, ctx = context
    ctx.save()

    def cannot_persist(**state):
        raise OSError("simulated durable intent failure")

    monkeypatch.setattr(ctx.evidence, "manifest", cannot_persist)
    with pytest.raises(OSError):
        await ctx.create("interrupted.before_intent", {"id": "400"})
    saved = json.loads((ctx.evidence.directory / "manifest.json").read_text())
    assert saved["pending_write"] is None and not ctx.evidence.counts.get("requests")


async def test_cancellation_during_cleanup_retains_unknown_delete_for_resume(context):
    import asyncio

    module, ctx = context
    dispatched, release = asyncio.Event(), asyncio.Event()
    operations = []

    async def endpoint(request):
        operations.append(request.method)
        if request.method == "POST":
            return web.json_response(
                {"id": "500", "sku_id": "400", "application_id": "100", "user_id": "200", "type": 8}
            )
        dispatched.set()
        await release.wait()
        return web.Response(status=204)

    async with server(endpoint) as base:
        ctx.rest.base = base
        await ctx.create("cleanup.cancel.fixture", {"id": "400"})
        cleanup = asyncio.create_task(ctx.cleanup())
        try:
            await dispatched.wait()
            cleanup.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cleanup
        finally:
            release.set()
        assert not ctx.cleaning and ctx.state["pending_write"]["method"] == "DELETE"
        module.reconcile(ctx)
        await ctx.cleanup()
    assert operations == ["POST", "DELETE"]
    assert ctx.state["uncertain_writes"] and not ctx.state["owned_entitlements"]["500"]["deleted"]
