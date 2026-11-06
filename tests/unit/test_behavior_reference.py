"""Offline safety and replay consumers; never contact Discord or require secrets."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web
from test_remaining_monetization_capture import server


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    return SimpleNamespace(
        **{
            name: importlib.import_module(f"scripts.behavior_reference.{name}")
            for name in ("scenarios", "transport", "runner", "ledger", "offline", "oracle")
        }
    )


def test_handwritten_inventory_and_controls(modules):
    cases = modules.scenarios.CASES
    assert len({case.id for case in cases}) == len(cases)
    for case in cases:
        assert case.source in modules.scenarios.DOCS
        if case.control:
            assert case.control != case.id and case.control in modules.scenarios.BY_ID
    selected = modules.scenarios.select(["send.no-view"])
    assert {case.id for case in selected} == {"send.no-view", "send.text"}
    with pytest.raises(ValueError, match="No scenario"):
        modules.scenarios.select(["does-not-exist"])


def test_authorization_rejects_other_guild_before_any_write(tmp_path, modules):
    evidence = modules.transport.open_evidence(tmp_path / "capture", ("admin-secret", "subject-secret"))
    with pytest.raises(ValueError, match="unauthorized guild"):
        modules.transport.Journal(evidence, modules.transport.EMOJI_GUILD)
    assert not (evidence.directory / "manifest.json").exists()


def test_both_secrets_and_authentication_fields_are_redacted_everywhere(tmp_path, modules):
    evidence = modules.transport.open_evidence(tmp_path / "capture", ("admin-secret", "subject-secret"))
    data = {
        "nested": {
            "content": "admin-secret subject-secret",
            "Authorization": "Bot ignored",
            "session_id": "session",
        }
    }
    evidence.record("gateway", **data)
    evidence.manifest(**data)
    for path in evidence.directory.iterdir():
        text = path.read_text()
        assert "admin-secret" not in text and "subject-secret" not in text
        assert "Bot ignored" not in text and '"session"' not in text
        assert "[REDACTED]" in text


def test_unproven_prefix_and_human_mutations_are_refused(tmp_path, modules):
    evidence = modules.transport.open_evidence(tmp_path / "capture", ())
    journal = modules.transport.Journal(evidence, modules.transport.LAB_GUILD)
    journal.state.update(subject="200", author="300", application="200", command_name="lab-unique")
    for method, path, body in (
        ("DELETE", "/channels/999", None),
        ("PATCH", f"/guilds/{journal.state['guild']}/roles/{journal.state['guild']}", {"permissions": "8"}),
        (
            "PATCH",
            f"/guilds/{journal.state['guild']}/members/400",
            {"communication_disabled_until": "future"},
        ),
        ("PUT", "/applications/200/commands", []),
        ("PUT", f"/applications/200/guilds/{journal.state['guild']}/commands", []),
        ("POST", f"/applications/200/guilds/{journal.state['guild']}/commands", {"name": "existing"}),
    ):
        with pytest.raises(RuntimeError, match="Refusing mutation"):
            journal.authorize(method, path, body)
    with pytest.raises(RuntimeError, match="positive create receipt"):
        journal.prove({"id": "999", "kind": "channel", "path": "/channels/999", "create_ref": "rest.jsonl:1"})


def test_interrupted_write_cannot_be_replayed_or_adopted_by_name(tmp_path, modules):
    evidence = modules.transport.open_evidence(tmp_path / "capture", ())
    journal = modules.transport.Journal(evidence, modules.transport.LAB_GUILD)
    journal.start("POST", f"/guilds/{journal.state['guild']}/channels", {"name": "lab-test"}, None)
    with pytest.raises(RuntimeError, match="unresolved mutation"):
        journal.start("POST", f"/guilds/{journal.state['guild']}/channels", {"name": "lab-test"}, None)
    recovered = modules.transport.Journal(evidence, modules.transport.LAB_GUILD, resume=True)
    assert recovered.state["pending_write"] is None and recovered.state["uncertain_writes"]
    with pytest.raises(RuntimeError, match="unresolved mutation"):
        recovered.start("POST", f"/guilds/{journal.state['guild']}/channels", {"name": "lab-test"}, None)
    assert not journal.state["owned"]


def test_normalization_preserves_validator_tree_and_permission_masks(modules):
    value = {
        "id": "1567657025029021897",
        "allow": "2251799813685248",
        "timestamp": "2026-11-06T12:34:56.000+00:00",
        "errors": {
            "options": {
                "25": {
                    "_errors": [{"code": "BASE_TYPE_MAX_LENGTH", "message": "Must be 25 or fewer in length."}]
                }
            }
        },
        "token": "do-not-keep",
    }
    result = modules.ledger.normalize(value, {"guild": value["id"]})
    assert result["id"] == "$guild" and result["timestamp"] == "$timestamp"
    assert result["allow"] == value["allow"]
    assert result["errors"] == value["errors"] and "token" not in result
    assert modules.ledger.normalize({"version": 1793959542556, "position": 23}, {}) == {
        "version": "$version",
        "position": "$position",
    }


def test_ratchet_rejects_new_changed_and_stale_gaps(modules):
    expected = [{"id": "send.text", "observation": {"status": 200}}]
    actual = [{"id": "send.text", "observation": {"status": 403}}]
    with pytest.raises(AssertionError, match="new mismatch"):
        modules.ledger.ratchet(expected, actual, {"schema": 1, "gaps": {}})
    gap = {
        "reason": "Synthetic unit-test mismatch, not live evidence",
        "expected": expected[0]["observation"],
        "actual": actual[0]["observation"],
    }
    modules.ledger.ratchet(expected, actual, {"schema": 1, "gaps": {"send.text": gap}})
    with pytest.raises(AssertionError, match="stale known gap"):
        modules.ledger.ratchet(expected, expected, {"schema": 1, "gaps": {"send.text": gap}})
    gap["actual"] = {"status": 404}
    with pytest.raises(AssertionError, match="signature changed"):
        modules.ledger.ratchet(expected, actual, {"schema": 1, "gaps": {"send.text": gap}})


async def test_offline_consumer_uses_distinct_author_and_subject_and_raw_send_semantics(tmp_path, modules):
    adapter = await modules.offline.Offline.create(tmp_path)
    runner = modules.runner.Runner(adapter, gateway=adapter.gateway)
    await runner.setup()
    success = await runner.run(modules.scenarios.BY_ID["send.text"])
    refused = await runner.run(modules.scenarios.BY_ID["send.no-view"])
    assert runner.symbols["subject"] != runner.symbols["author"]
    assert success["observation"]["status"] == 200
    assert refused["observation"]["status"] == 403
    assert refused["observation"]["code"] == 50001
    assert runner.overwrites["text"] == ()
    requests = [
        json.loads(line) for line in (adapter.evidence.directory / "requests.jsonl").read_text().splitlines()
    ]
    send = next(
        row for row in reversed(requests) if row["method"] == "POST" and row["path"].endswith("/messages")
    )
    assert send["body"] == {"content": "lab-message", "allowed_mentions": {"parse": []}}
    await modules.transport.cleanup(adapter)
    assert all(item["deleted"] for item in adapter.journal.state["owned"])
    assert success["source"]["kind"] == "offline"
    assert success["scenario"] == modules.ledger.scenario_snapshot(modules.scenarios.BY_ID["send.text"])
    assert success["observation"]["effect"]["history_probe"]["new_message"] is True
    assert refused["observation"]["effect"]["history_probe"]["new_message"] is False


def test_ledger_rejects_documentation_disguised_as_capture(modules):
    with pytest.raises(ValueError, match="real live captures"):
        modules.ledger.validate_ledger(
            {
                "schema": 1,
                "capture_date": "2026-11-06",
                "provenance": {"kind": "docs", "recorder": "unit-test"},
                "rows": [],
            }
        )


async def test_rate_limits_retry_only_explicit_refusals_with_durable_ownership(tmp_path, modules):
    evidence = modules.transport.open_evidence(tmp_path / "capture", ("admin-secret", "subject-secret"))
    journal = modules.transport.Journal(evidence, modules.transport.LAB_GUILD)
    operations = []

    async def endpoint(request):
        operations.append(request.method)
        saved = json.loads((evidence.directory / "manifest.json").read_text())
        assert saved["pending_write"]["method"] == "POST"
        if len(operations) == 1:
            return web.json_response({"retry_after": 0}, status=429)
        return web.json_response({"id": "500", "name": "lab-test"})

    async with server(endpoint) as base:
        async with aiohttp.ClientSession() as session:
            adapter = modules.transport.Live(
                {"admin": modules.transport.MultipartSession(session)}, evidence, journal, pace=0
            )
            adapter.rest["admin"].base = base
            status, _ = await adapter.request(
                "admin", "POST", f"/guilds/{journal.state['guild']}/roles", body={"name": "lab-test"}
            )
    assert status == 200 and operations == ["POST", "POST"]
    assert journal.state["pending_write"] is None
    assert journal.state["owned"][0]["create_ref"] == "rest.jsonl:2"
    journal.prove(journal.state["owned"][0])


async def test_lost_write_response_is_not_retried_and_has_no_cleanup_ownership(tmp_path, modules):
    evidence = modules.transport.open_evidence(tmp_path / "capture", ())
    journal = modules.transport.Journal(evidence, modules.transport.LAB_GUILD)
    operations = []

    async def endpoint(request):
        operations.append(request.method)
        request.transport.close()
        return web.Response(status=204)

    async with server(endpoint) as base:
        async with aiohttp.ClientSession() as session:
            adapter = modules.transport.Live(
                {"admin": modules.transport.MultipartSession(session)}, evidence, journal, pace=0
            )
            adapter.rest["admin"].base = base
            with pytest.raises(aiohttp.ClientError):
                await adapter.request(
                    "admin", "POST", f"/guilds/{journal.state['guild']}/roles", body={"name": "lab-test"}
                )
    assert operations == ["POST"]
    assert journal.state["pending_write"] and not journal.state["owned"]


def test_recovery_adopts_only_correlated_positive_receipt(tmp_path, modules):
    evidence = modules.transport.open_evidence(tmp_path / "capture", ())
    journal = modules.transport.Journal(evidence, modules.transport.LAB_GUILD)
    path, body = f"/guilds/{journal.state['guild']}/roles", {"name": "lab-test"}
    journal.start("POST", path, body, None)
    evidence.record(
        "requests", request_id="requests.jsonl:1", method="POST", path=path, body=body, query=None
    )
    evidence.record(
        "rest",
        request_id="requests.jsonl:1",
        method="POST",
        path_query=path,
        body=body,
        status=200,
        response={"id": "500", "name": "lab-test"},
    )
    recovered = modules.transport.Journal(evidence, modules.transport.LAB_GUILD, resume=True)
    assert recovered.state["pending_write"] is None
    assert [item["id"] for item in recovered.state["owned"]] == ["500"]
    recovered.prove(recovered.state["owned"][0])


def test_exact_request_semantics_cannot_be_blessed_as_known_gap(modules):
    expected = [{"id": "send.text", "request": {"body": {"content": "one"}}, "observation": {"status": 200}}]
    actual = [{"id": "send.text", "request": {"body": {"content": "two"}}, "observation": {"status": 200}}]
    with pytest.raises(AssertionError, match="raw request semantics differ"):
        modules.ledger.ratchet(expected, actual, {"schema": 1, "gaps": {}})


def test_capture_freezes_symbolic_scenario_body_and_actor(modules):
    case = modules.scenarios.BY_ID["send.text"]
    row = modules.ledger.observation(
        case,
        "POST",
        "send",
        200,
        {"id": "1567657025029021898", "content": "lab-message", "author": {"id": "1567657025029021899"}},
        {},
        {"text": "1567657025029021897"},
        request={
            "path": "/channels/1567657025029021897/messages",
            "actor": "subject",
            "body": {"content": "lab-message"},
            "query": None,
            "file": False,
        },
    )
    ledger = {
        "schema": 1,
        "capture_date": "2026-11-06",
        "provenance": {"kind": "live", "recorder": "synthetic-unit-test-only"},
        "rows": [row],
    }
    modules.ledger.validate_ledger(ledger)
    assert row["request"]["path"] == "/channels/$text/messages"
    row["scenario"]["body"]["content"] = "changed-after-capture"
    with pytest.raises(ValueError, match="Scenario changed since live capture"):
        modules.ledger.validate_ledger(ledger)


def test_live_only_permission_oracle_uses_observed_contract_and_explicit_bits(modules):
    oracle = modules.oracle
    assert oracle.permission_value({"permissions": "4473885000465985"}) == 4473885000465985
    for response in ({"permissions": 8}, {"permissions": "-1"}, {}, "8"):
        with pytest.raises(RuntimeError, match="response contract changed"):
            oracle.permission_value(response)
    runner = SimpleNamespace(
        adapter=SimpleNamespace(journal=SimpleNamespace(state={"baseline_subject_permissions": 0})),
        role_permissions=modules.runner.BASE_PERMISSIONS,
    )
    denied = oracle.expected_bits(runner, modules.scenarios.BY_ID["send.no-send"])
    import discord

    assert not denied & discord.Permissions(send_messages=True).value
    assert denied & discord.Permissions(attach_files=True).value
    hidden = oracle.expected_bits(runner, modules.scenarios.BY_ID["send.no-view"])
    assert not hidden & discord.Permissions(view_channel=True).value
    assert hidden & discord.Permissions(send_messages=True).value


async def test_gateway_never_persists_identify_ready_or_session_secrets(tmp_path, modules):
    import asyncio

    gateway_module = importlib.import_module("scripts.behavior_reference.gateway")
    packets = asyncio.Queue()
    for packet in (
        {"op": 10, "d": {"heartbeat_interval": 60000}},
        {
            "op": 0,
            "t": "READY",
            "s": 1,
            "d": {"session_id": "session-secret", "resume_gateway_url": "wss://ignored"},
        },
        {"op": 0, "t": "GUILD_CREATE", "s": 2, "d": {"id": modules.transport.LAB_GUILD, "channels": []}},
        {
            "op": 0,
            "t": "VOICE_STATE_UPDATE",
            "s": 3,
            "d": {
                "guild_id": modules.transport.LAB_GUILD,
                "session_id": "voice-secret",
                "content": "admin-secret subject-secret",
            },
        },
    ):
        await packets.put(packet)

    class Socket:
        async def receive_json(self, **kwargs):
            return await packets.get()

        async def send_json(self, packet):
            pass

        async def close(self):
            pass

    class Session:
        async def ws_connect(self, url, **kwargs):
            return Socket()

    evidence = modules.transport.open_evidence(tmp_path / "capture", ("admin-secret", "subject-secret"))
    async with gateway_module.Gateway(Session(), "subject-secret", evidence):
        await asyncio.sleep(0)
    text = (evidence.directory / "gateway.jsonl").read_text()
    assert "READY" not in text and "IDENTIFY" not in text
    assert all(
        secret not in text for secret in ("session-secret", "voice-secret", "admin-secret", "subject-secret")
    )


async def test_cleanup_proves_legacy_parent_deletes_and_cascades_seed_messages(tmp_path, modules):
    adapter = await modules.offline.Offline.create(tmp_path)
    runner = modules.runner.Runner(adapter, gateway=adapter.gateway)
    await runner.setup()
    await runner.run(modules.scenarios.BY_ID["thread.active-edit"])
    await runner.run(modules.scenarios.BY_ID["thread.manager-delete"])
    # Model a journal from before deletion references were persisted. Receipts,
    # not the deleted boolean, must establish the old thread's cascade.
    for item in adapter.journal.state["owned"]:
        item.pop("delete_ref", None)
    start = adapter.evidence.counts["requests"]
    await modules.transport.cleanup(adapter)
    rows = [
        json.loads(line) for line in (adapter.evidence.directory / "requests.jsonl").read_text().splitlines()
    ][start:]
    assert not any(row["method"] == "DELETE" and "/messages/" in row["path"] for row in rows)
    assert all(item["deleted"] for item in adapter.journal.state["owned"])
    assert any(
        item.get("cascade_delete_ref") for item in adapter.journal.state["owned"] if item["kind"] == "message"
    )


async def test_deleted_boolean_without_parent_delete_receipt_never_proves_cascade(tmp_path, modules):
    adapter = await modules.offline.Offline.create(tmp_path)
    runner = modules.runner.Runner(adapter, gateway=adapter.gateway)
    await runner.setup()
    await runner.run(modules.scenarios.BY_ID["thread.active-edit"])
    parent = next(item for item in adapter.journal.state["owned"] if item["id"] == runner.symbols["thread"])
    child = next(
        item
        for item in adapter.journal.state["owned"]
        if item["kind"] == "message" and item["path"].startswith(parent["path"] + "/")
    )
    parent["deleted"] = True
    with pytest.raises(RuntimeError):
        adapter.journal.cascade(child)
    assert not child["deleted"] and "cascade_delete_ref" not in child


async def test_unknown_role_patch_cleanup_deletes_owned_role_without_replay_or_double_detach(
    tmp_path, modules
):
    adapter = await modules.offline.Offline.create(tmp_path)
    journal = adapter.journal
    role = journal.state["owned"][0]
    detach = {
        "method": "DELETE",
        "path": f"/guilds/{journal.state['guild']}/members/{journal.state['subject']}/roles/{role['id']}",
        "done": False,
    }
    journal.state["restores"].append(detach)
    journal.start("PATCH", role["path"], {"permissions": "8"}, None)
    adapter.backend.edit_role(int(journal.state["guild"]), int(role["id"]), {"permissions": 8})
    start = adapter.evidence.counts["requests"]
    with pytest.raises(RuntimeError, match="ambiguous write remains"):
        await modules.transport.cleanup(adapter)
    requests = [
        json.loads(line) for line in (adapter.evidence.directory / "requests.jsonl").read_text().splitlines()
    ][start:]
    assert [(row["method"], row["path"]) for row in requests] == [("DELETE", role["path"])]
    assert detach["done"] and role["deleted"]
    assert int(role["id"]) not in adapter.backend.get_guild(int(journal.state["guild"])).roles


async def test_raw_gateway_preserves_identity_before_ambient_position_normalization(tmp_path, modules):
    adapter = await modules.offline.Offline.create(tmp_path)
    runner = modules.runner.Runner(adapter, gateway=adapter.gateway)
    await runner.setup()
    case = modules.scenarios.BY_ID["hidden.gateway-create"]
    method, path, body, query, file = runner.request(case)
    start = len(adapter.gateway.events)
    status, response = await adapter.request("admin", method, path, body=body, query=query, file=file)
    event = await adapter.gateway.channel_event(start, response["id"], "CHANNEL_CREATE")
    assert status == 201
    for key in ("id", "type", "position", "parent_id"):
        assert event.get(key) == response.get(key)
    assert event["name"] == "___hidden___" and event["flags"] & (1 << 17)
    await modules.transport.cleanup(adapter)


def synthetic_publication(tmp_path, modules):
    """Unit-only schema data: never installs a repository live-evidence fixture."""
    evidence = modules.transport.open_evidence(tmp_path / "capture", ())
    journal = modules.transport.Journal(evidence, modules.transport.LAB_GUILD)
    case = modules.scenarios.BY_ID["send.text"]
    row = modules.ledger.observation(
        case,
        "POST",
        "send",
        200,
        {"content": "lab-message", "author": {"id": "unit-subject"}},
        {},
        {},
        request={
            "actor": "subject",
            "path": "/channels/$text/messages",
            "body": {"content": "lab-message"},
            "query": None,
            "file": False,
        },
    )
    evidence.record(
        "requests",
        utc="2026-11-05T00:00:00+00:00",
        method="POST",
        path="/channels/unit-test-only/messages",
        body={"content": "lab-message"},
    )
    evidence.record("observations", utc="2026-11-05T00:00:01+00:00", row=row)
    return evidence, journal, row


def test_offline_publication_preserves_original_date_and_refuses_conflicting_overlap(tmp_path, modules):
    publication = importlib.import_module("scripts.behavior_reference.publish")
    evidence, _, row = synthetic_publication(tmp_path, modules)
    destination = tmp_path / "published.json"
    publication.publish(evidence.directory, destination)
    ledger = modules.ledger.load_ledger(destination)
    assert ledger["capture_date"] == "2026-11-05T00:00:01+00:00"
    assert ledger["provenance"]["capture_started_at"] == "2026-11-05T00:00:00+00:00"
    assert ledger["rows"] == [row]
    supplement = tmp_path / "supplement.json"
    modules.ledger.write_ledger(supplement, [row], capture_date="2026-11-06")
    publication.publish(evidence.directory, destination, [supplement])
    assert len(modules.ledger.load_ledger(destination)["rows"]) == 1
    row["observation"]["code"] = 50013
    modules.ledger.write_ledger(supplement, [row], capture_date="2026-11-06")
    with pytest.raises(ValueError, match="Overlapping live captures disagree"):
        publication.publish(evidence.directory, destination, [supplement])


def test_publication_refuses_uncertainty_unclean_state_and_missing_snapshot(tmp_path, modules):
    publication = importlib.import_module("scripts.behavior_reference.publish")
    evidence, journal, row = synthetic_publication(tmp_path, modules)
    destination = tmp_path / "published.json"
    journal.state["uncertain_writes"] = [{"method": "POST", "path": "/unknown"}]
    journal.save()
    with pytest.raises(ValueError, match="ambiguous write"):
        publication.publish(evidence.directory, destination)
    journal.state["uncertain_writes"] = []
    journal.state["restores"] = [{"done": False}]
    journal.save()
    with pytest.raises(ValueError, match="restorations are cleaned"):
        publication.publish(evidence.directory, destination)
    journal.state["restores"] = []
    journal.save()
    row.pop("scenario")
    (evidence.directory / "observations.jsonl").write_text(
        json.dumps({"utc": "2026-11-05T00:00:01+00:00", "row": row}) + "\n"
    )
    with pytest.raises(ValueError, match="Scenario changed since live capture"):
        publication.publish(evidence.directory, destination)
    assert not destination.exists()
