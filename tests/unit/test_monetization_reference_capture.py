"""Schema-1 capture keeps its distinct workflow over the shared durable transport."""

from __future__ import annotations

import importlib
import json

import aiohttp
import pytest
from aiohttp import web
from test_remaining_monetization_capture import context as capture_context
from test_remaining_monetization_capture import server

context = capture_context


def legacy_capture(ctx, base):
    module = importlib.import_module("capture_monetization_reference")
    config = importlib.import_module("monetization_reference_config")
    transport = importlib.import_module("monetization_reference_transport")
    identity = config.CaptureIdentity(200, 300)
    inputs = config.SubscriptionCaptureConfig(
        identity, config.ProbeConfig(identity), config.RequestPolicy(observe=0), ("A", "B")
    )
    evidence = transport.Evidence(ctx.evidence.directory.parent / "legacy", "dummy-local-secret")
    capture = module.LegacyCapture(inputs, evidence, transport.Rest(ctx.rest.session, evidence, base))
    capture.app, capture.base = {"id": "100"}, "/applications/100/entitlements"
    return module, capture


async def test_schema1_create_intent_and_cleanup_proof_are_durable(context):
    _, ctx = context
    operations = []
    capture = None

    async def endpoint(request):
        operations.append(request.method)
        saved = json.loads((capture.evidence.directory / "manifest.json").read_text())
        assert saved["pending_write"]["method"] == request.method
        if request.method == "POST":
            return web.json_response(
                {"id": "500", "application_id": "100", "sku_id": "400", "user_id": "200", "type": 8}
            )
        return web.Response(status=204)

    async with server(endpoint) as base:
        _, capture = legacy_capture(ctx, base)
        try:
            await capture.create({"id": "400"}, "S02 create A", required=True)
            assert capture.state["owned_entitlements"]["500"]["create_ref"] == "rest.jsonl:1"
            await capture.close()
        finally:
            await capture.client.close()
    saved = json.loads((capture.evidence.directory / "manifest.json").read_text())
    assert saved["schema_version"] == 1
    assert saved["created_entitlement_ids"] == saved["deleted_entitlement_ids"] == ["500"]
    assert saved["pending_write"] is None and operations == ["POST", "DELETE"]


async def test_schema1_lost_response_does_not_acquire_cleanup_ownership(context):
    _, ctx = context
    operations = []

    async def endpoint(request):
        operations.append(request.method)
        request.transport.close()
        return web.Response(status=204)

    async with server(endpoint) as base:
        _, capture = legacy_capture(ctx, base)
        try:
            with pytest.raises(aiohttp.ClientError):
                await capture.create({"id": "400"}, "S02 create A", required=True)
            await capture.close()
        finally:
            await capture.client.close()
    assert operations == ["POST"]
    assert capture.state["pending_write"] and capture.state["status"] == "uncertain_write"
    assert not capture.state["owned_entitlements"]


async def test_subscription_sequence_preserves_legacy_actions_and_owned_cleanup(context):
    _, ctx = context
    posts, deleted = [], []
    ids = iter(("500", "501", "502"))

    async def endpoint(request):
        if request.method == "POST":
            body = await request.json()
            posts.append(body)
            if len(posts) == 2:
                return web.json_response({"code": 40007}, status=400)
            return web.json_response(
                {
                    "id": next(ids),
                    "application_id": "100",
                    "sku_id": body["sku_id"],
                    "user_id": "200",
                    "type": 8,
                }
            )
        if request.method == "DELETE":
            eid = request.path.rsplit("/", 1)[-1]
            if eid in deleted:
                return web.json_response({"code": 10029}, status=404)
            deleted.append(eid)
            return web.Response(status=204)
        return web.json_response([])

    async with server(endpoint) as base:
        module, capture = legacy_capture(ctx, base)

        async def observed(label):
            capture.evidence.mark(label)

        async def probed(label):
            capture.evidence.mark(label)

        capture.observe, capture.client.probe = observed, probed
        try:
            await module.capture_subscriptions(capture, [{"id": "400"}, {"id": "401"}])
            await capture.close()
        finally:
            await capture.client.close()
    actions = [
        json.loads(line)["action"]
        for line in (capture.evidence.directory / "actions.jsonl").read_text().splitlines()
    ]
    assert actions[0] == "S01 baseline interaction"
    assert "S03 observe A create" in actions and "S15 deleted interaction" in actions
    assert len(posts) == 4 and deleted == ["500", "501", "502"]
    assert capture.state["created_entitlement_ids"] == ["500", "501", "502"]
