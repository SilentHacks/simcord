from __future__ import annotations

import asyncio
import json
from importlib import resources
from types import SimpleNamespace

import discord
import jsonschema
import pytest
from aiohttp import ClientSession, FormData
from discord import app_commands
from preview_helpers import action_body, preview_headers

from simcord.interactions import command_leaves
from simcord.preview import _commands

_SCHEMA = json.loads(resources.files("simcord.preview").joinpath("protocol.schema.json").read_text())
_VALIDATOR = jsonschema.Draft202012Validator(_SCHEMA)
_CATALOG_VALIDATOR = jsonschema.Draft202012Validator(
    {"$ref": "#/$defs/commandCatalog", "$defs": _SCHEMA["$defs"]}
)
_ACTION_VALIDATOR = jsonschema.Draft202012Validator(
    {"$ref": "#/$defs/actionRequest", "$defs": _SCHEMA["$defs"]}
)
_ACTION_RESPONSE_VALIDATOR = jsonschema.Draft202012Validator(
    {"$ref": "#/$defs/actionResponse", "$defs": _SCHEMA["$defs"]}
)


def _entry(page, invocation: str) -> dict:
    return next(item for item in page.command_catalog["entries"] if item["invocation"] == invocation)


def _registered(env, name: str):
    for scope in (env.guild.id, None):
        registered = env.backend.commands.get(scope, {})
        command = registered.get((name, 1))
        if command is not None:
            return scope, command
    raise AssertionError(f"command {name!r} is not registered")


def _command_body(page, kind: str, sequence: int, entry: dict, **fields):
    return action_body(
        page,
        kind,
        sequence,
        request_id=f"{kind}-{sequence}",
        command_id=entry["commandId"],
        path=entry["path"],
        schema_fingerprint=entry["schemaFingerprint"],
        **fields,
    )


@pytest.mark.asyncio
async def test_catalog_command_id_selects_colliding_guild_and_global_callbacks(env, channel, alice):
    async def global_callback(interaction: discord.Interaction, global_value: str) -> None:
        await interaction.response.send_message(f"global:{global_value}")

    async def guild_callback(interaction: discord.Interaction, guild_value: str) -> None:
        await interaction.response.send_message(f"guild:{guild_value}")

    global_command = app_commands.Command(
        name="collision", description="Global collision", callback=global_callback
    )
    guild_command = app_commands.Command(
        name="collision", description="Guild collision", callback=guild_callback
    )

    async def global_autocomplete(interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=f"global:{current}", value=f"global:{current}")]

    async def guild_autocomplete(interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=f"guild:{current}", value=f"guild:{current}")]

    global_command.autocomplete("global_value")(global_autocomplete)
    guild_command.autocomplete("guild_value")(guild_autocomplete)
    env.bot.tree.add_command(global_command)
    env.bot.tree.add_command(guild_command, guild=discord.Object(id=env.guild.id))
    await env.bot.tree.sync()
    await env.bot.tree.sync(guild=discord.Object(id=env.guild.id))

    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        page = preview._python
        entries = [entry for entry in page.command_catalog["entries"] if entry["invocation"] == "collision"]
        assert {entry["scope"] for entry in entries} == {"global", "guild"}
        assert alice.available_commands(channel).count("collision") == 1

        for sequence, scope, option_name in (
            (1, "global", "global_value"),
            (2, "guild", "guild_value"),
        ):
            entry = next(item for item in entries if item["scope"] == scope)
            receipt = await preview._action(
                page.id,
                _command_body(
                    page,
                    "run_command",
                    sequence,
                    entry,
                    options={option_name: scope},
                ),
            )
            assert receipt["settlement"] == "settled"
            message_id = receipt["outcomes"][0]["messageId"]
            assert page.snapshot["messages"][message_id]["content"] == f"{scope}:{scope}"

        for sequence, scope, option_name in (
            (3, "global", "global_value"),
            (4, "guild", "guild_value"),
        ):
            entry = next(item for item in entries if item["scope"] == scope)
            receipt = await preview._action(
                page.id,
                _command_body(
                    page,
                    "autocomplete_command",
                    sequence,
                    entry,
                    focused=option_name,
                    value="probe",
                    options={},
                ),
            )
            assert receipt["settlement"] == "settled"
            assert receipt["result"]["choices"] == [{"name": f"{scope}:probe", "value": f"{scope}:probe"}]


@pytest.mark.asyncio
async def test_command_catalog_route_authorization_visibility_and_revocation(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    async with (
        env.preview(channel, viewers=[alice, bob], layout="channel") as preview,
        ClientSession() as client,
    ):
        page = preview._python
        url = preview._origin + "/api/commands"
        response = await client.get(url, headers={"X-Simcord-Capability": preview.capability})
        assert response.status == 410
        response = await client.get(
            url,
            headers={"X-Simcord-Capability": "wrong", "X-Simcord-Context": page.id},
        )
        assert response.status == 401
        response = await client.get(url, headers=preview_headers(preview, "p_unknown"))
        assert response.status == 410
        response = await client.get(url, headers=preview_headers(preview, page.id))
        assert response.status == 200
        catalog = await response.json()
        assert catalog == page.command_catalog
        assert catalog["state"] == "available"
        assert catalog["application"]["id"] == str(env.backend.bot_user.id)
        assert {item["invocation"] for item in catalog["entries"]} == set(alice.available_commands(channel))
        assert "etag" not in {key.lower() for key in response.headers}
        assert response.headers["Cache-Control"] == "no-store"
        assert page.snapshot["commands"] == {
            "state": catalog["state"],
            "fingerprint": catalog["fingerprint"],
            "count": len(catalog["entries"]),
        }
        snapshot_errors = list(_VALIDATOR.iter_errors(page.snapshot))
        catalog_errors = list(_CATALOG_VALIDATOR.iter_errors(catalog))
        assert not snapshot_errors, [(list(error.path), error.message) for error in snapshot_errors]
        assert not catalog_errors, [(list(error.path), error.message) for error in catalog_errors]

        await env.bot.get_channel(channel.id).set_permissions(
            env.bot.get_guild(env.guild.id).get_member(bob.id), view_channel=False
        )
        denied_page = preview._open_page(bob.id)
        denied = await client.get(
            url,
            headers=preview_headers(preview, denied_page.id),
        )
        denied_catalog = await denied.json()
        assert denied_catalog["state"] == "unavailable"
        assert denied_catalog["entries"] == []
        assert denied_catalog["application"] is None
        assert denied_page.snapshot["commands"]["state"] == "unavailable"
        assert denied_page.snapshot["channel"]["canUseApplicationCommands"] is False
        assert not list(_VALIDATOR.iter_errors(denied_page.snapshot))
        _CATALOG_VALIDATOR.validate(denied_catalog)

        scope, command = _registered(env, "tag")
        command_id = command["id"]
        env.backend.commands[scope].pop(("tag", 1))
        preview._publish(page, reason="refresh")
        assert all(item["commandId"] != command_id for item in page.command_catalog["entries"])
        assert page.snapshot["commands"]["count"] < len(catalog["entries"])
        env.backend.commands[scope][("tag", 1)] = command
        preview._publish(page, reason="refresh")


@pytest.mark.asyncio
async def test_catalog_truncation_fingerprints_and_unsynced_diagnostic(env, channel, alice, monkeypatch):
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        page = preview._python
        monkeypatch.setattr(_commands, "MAX_CATALOG_LEAVES", 2)
        preview._publish(page, reason="refresh")
        assert len(page.command_catalog["entries"]) == 2
        assert page.command_catalog["truncated"] is True

        scope, command = _registered(env, "tag")
        path, leaf = command_leaves(command)[0]
        reversed_root = dict(reversed(list(command.items())))
        reversed_leaf = dict(reversed(list(leaf.items())))
        one = _commands.entry_for_leaf(command, path, leaf)
        reordered = _commands.entry_for_leaf(reversed_root, path, reversed_leaf)
        assert one["schemaFingerprint"] == reordered["schemaFingerprint"]
        changed = {**leaf, "description": "Changed description"}
        assert (
            one["schemaFingerprint"] != _commands.entry_for_leaf(command, path, changed)["schemaFingerprint"]
        )

        monkeypatch.setattr(_commands, "MAX_CATALOG_LEAVES", 1_000)
        env.backend.commands[scope].pop(("tag", 1))
        for strict_sync in (True, False):
            env.strict_sync = strict_sync
            preview._publish(page, reason="refresh")
            assert any(item["code"] == "commands-unsynced" for item in page.snapshot["diagnostics"])
            assert all(item["invocation"] != "tag" for item in page.command_catalog["entries"])
        env.backend.commands[scope][("tag", 1)] = command


@pytest.mark.asyncio
async def test_run_command_receipt_modal_duplicate_and_ephemeral_visibility(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    async with env.preview(channel, viewers=[alice, bob], layout="channel") as preview:
        page = preview._python
        bob_page = preview._open_page(bob.id)
        entry = _entry(page, "config set")
        body = _command_body(page, "run_command", 1, entry, options={"key": "theme", "value": "dark"})
        _ACTION_VALIDATOR.validate(body)
        receipt = await preview._action(page.id, body)
        _ACTION_RESPONSE_VALIDATOR.validate(receipt)
        assert receipt["settlement"] == "settled"
        assert receipt["command"] == {"commandId": entry["commandId"], "invocation": "config set"}
        assert receipt["outcomes"][0]["kind"] == "response"
        assert "theme=dark" in page.snapshot["messages"][receipt["outcomes"][0]["messageId"]]["content"]
        assert await preview._action(page.id, body) == receipt

        greeting = _entry(page, "dm_greeting")
        ephemeral = await preview._action(
            page.id,
            _command_body(page, "run_command", 2, greeting, options={}),
        )
        assert ephemeral["settlement"] == "settled"
        ephemeral_id = ephemeral["outcomes"][0]["messageId"]
        assert ephemeral_id in preview._page_payload(page)["messages"]
        assert ephemeral_id not in preview._page_payload(bob_page)["messages"]

        feedback = _entry(page, "feedback")
        opened = await preview._action(
            page.id,
            _command_body(page, "run_command", 3, feedback, options={}),
        )
        assert opened["settlement"] == "settled"
        assert opened["command"]["invocation"] == "feedback"
        assert opened["outcomes"] == [{"kind": "modal"}]
        assert page.snapshot["modal"] is not None


@pytest.mark.asyncio
async def test_command_rejections_do_not_consume_sequence_and_generation_is_guarded(env, channel, alice):
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        page = preview._python
        entry = _entry(page, "picker_options")
        changed = _command_body(
            page,
            "run_command",
            1,
            {**entry, "schemaFingerprint": "sf_" + "0" * 32},
            options={},
        )
        changed_result = await preview._action(page.id, changed)
        assert changed_result["diagnostics"][0]["code"] == "command-changed"
        assert page.last_sequence == 0

        invalid = await preview._action(
            page.id,
            _command_body(page, "run_command", 1, entry, options={"text": "x"}),
        )
        assert invalid["diagnostics"][0]["code"] == "command-option-invalid"
        assert invalid["diagnostics"][0]["subject"] == {"commandOption": "text"}
        assert page.last_sequence == 0

        missing = await preview._action(
            page.id,
            action_body(
                page,
                "run_command",
                1,
                request_id="command-missing",
                command_id="999999999999",
                path=["missing"],
                schema_fingerprint="sf_" + "0" * 32,
                options={},
            ),
        )
        assert missing["diagnostics"][0]["code"] == "command-unavailable"
        assert page.last_sequence == 0

        stale = _command_body(page, "run_command", 1, _entry(page, "dm_greeting"), options={})
        stale["bot_generation"] = env._generation + 1
        stale_result = await preview._action(page.id, stale)
        assert stale_result["diagnostics"][0]["code"] == "stale-generation"
        assert page.last_sequence == 0

        accepted = await preview._action(
            page.id,
            _command_body(page, "run_command", 1, _entry(page, "dm_greeting"), options={}),
        )
        assert accepted["settlement"] == "settled"
        assert page.last_sequence == 1


@pytest.mark.asyncio
async def test_autocomplete_answered_unanswered_partial_values_and_ledger(env, channel, alice, monkeypatch):
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        page = preview._python
        tag = _entry(page, "tag")
        before = len(page.activity)
        answered_body = _command_body(
            page,
            "autocomplete_command",
            1,
            tag,
            focused="name",
            value="py",
            options={},
        )
        _ACTION_VALIDATOR.validate(answered_body)
        answered = await preview._action(page.id, answered_body)
        _ACTION_RESPONSE_VALIDATOR.validate(answered)
        assert answered["settlement"] == "settled"
        assert answered["result"]["answered"] is True
        assert len(answered["result"]["choices"]) <= 25
        assert len(page.activity) == before

        captured = {}
        fake_interaction = env.backend.new_interaction(4, channel.id, alice.id, env.guild.id)
        suggest = _entry(page, "suggest")

        async def many_choices(*args, **kwargs):
            captured["filled"] = args[-1]
            return SimpleNamespace(
                autocomplete_choices=[{"name": str(index), "value": index} for index in range(30)],
                _interaction=fake_interaction,
            )

        monkeypatch.setattr("simcord.preview._action_plans._autocomplete_result", many_choices)
        limited = await preview._action(
            page.id,
            _command_body(
                page,
                "autocomplete_command",
                2,
                suggest,
                focused="query",
                value="py",
                options={"unknown": "drop", "limit": "not-an-integer"},
            ),
        )
        assert captured["filled"] == {}
        assert limited["result"]["answered"] is True
        assert len(limited["result"]["choices"]) == 25
        assert isinstance(limited["result"]["choices"][0]["value"], int)
        assert len(page.activity) == before

        async def unanswered(*args, **kwargs):
            return SimpleNamespace(autocomplete_choices=None, _interaction=fake_interaction)

        monkeypatch.setattr("simcord.preview._action_plans._autocomplete_result", unanswered)
        unanswered_receipt = await preview._action(
            page.id,
            _command_body(
                page,
                "autocomplete_command",
                3,
                suggest,
                focused="query",
                value="py",
                options={},
            ),
        )
        _ACTION_RESPONSE_VALIDATOR.validate(unanswered_receipt)
        assert unanswered_receipt["result"]["answered"] is False
        assert any(item["code"] == "autocomplete-unanswered" for item in unanswered_receipt["diagnostics"])
        assert len(page.activity) == before + 1

        async def callback_error(*args, **kwargs):
            raise RuntimeError("private bot callback details")

        monkeypatch.setattr("simcord.preview._action_plans._autocomplete_result", callback_error)
        failed = await preview._action(
            page.id,
            _command_body(
                page,
                "autocomplete_command",
                4,
                suggest,
                focused="query",
                value="py",
                options={},
            ),
        )
        assert failed["settlement"] == "failed"
        assert {item["code"] for item in failed["diagnostics"]} >= {
            "action-callback-error",
            "autocomplete-unanswered",
        }
        assert "private bot callback details" not in failed["diagnostics"][0]["message"]
        assert len(page.activity) == before + 2
        assert any(isinstance(error, RuntimeError) for error in env.errors)


@pytest.mark.asyncio
async def test_command_candidate_scope_pages_and_prunes(env, channel, alice):
    for index in range(52):
        env.guild.add_member(env.create_user(f"candidate-{index:02d}"))
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        page = preview._python
        entry = _entry(page, "picker_options")
        key = f"command:{entry['commandId']}:picker_options:option:user"
        first = await preview._action(
            page.id,
            action_body(
                page,
                "browse_candidates",
                1,
                control_key=key,
                query="",
                cursor=None,
                modal_handle=None,
            ),
        )
        assert first["target"] is None
        descriptor = first["result"]["candidate"]
        assert descriptor["type"] == "users"
        assert descriptor["hasNext"] is True
        second = await preview._action(
            page.id,
            action_body(
                page,
                "browse_candidates",
                2,
                control_key=key,
                query="",
                cursor=descriptor["nextCursor"],
                modal_handle=None,
            ),
        )
        assert second["settlement"] == "settled"
        assert second["result"]["candidate"]["entries"]
        malformed = await preview._action(
            page.id,
            action_body(
                page,
                "browse_candidates",
                3,
                control_key="command:bad",
                query="",
                cursor=None,
                modal_handle=None,
            ),
        )
        assert malformed["diagnostics"][0]["code"] == "control-unavailable"
        assert page.last_sequence == 2
        cross_scope = await preview._action(
            page.id,
            action_body(
                page,
                "browse_candidates",
                3,
                control_key="command:999999999999:picker_options:option:user",
                query="",
                cursor=None,
                modal_handle=None,
            ),
        )
        assert cross_scope["diagnostics"][0]["code"] == "control-unavailable"
        assert page.last_sequence == 2

        await env.bot.get_channel(channel.id).set_permissions(
            env.bot.get_guild(env.guild.id).get_member(alice.id), use_application_commands=False
        )
        preview._publish(page, reason="refresh")
        assert page.snapshot["channel"]["canUseApplicationCommands"] is False
        assert key not in page.candidate_queries
        assert any(
            item["code"] == "command-unavailable" and item["state"] == "recovered"
            for item in page.snapshot["diagnostics"]
        )


@pytest.mark.asyncio
async def test_unicode_command_path_candidate_and_diagnostic_subject(env, channel, alice):
    command = env.backend.register_commands(
        env.guild.id,
        [
            {
                "name": "问候",
                "description": "Unicode root",
                "options": [
                    {
                        "name": "挨拶",
                        "description": "Unicode subcommand",
                        "type": 1,
                        "options": [
                            {
                                "name": "名前",
                                "description": "Unicode text option",
                                "type": 3,
                                "min_length": 3,
                            },
                            {
                                "name": "利用者",
                                "description": "Unicode user option",
                                "type": 6,
                            },
                        ],
                    }
                ],
            }
        ],
    )[0]

    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        page = preview._python
        entry = next(item for item in page.command_catalog["entries"] if item["commandId"] == command["id"])
        assert entry["path"] == ["问候", "挨拶"]
        control_key = f"command:{command['id']}:问候.挨拶:option:利用者"
        candidates = await preview._action(
            page.id,
            action_body(
                page,
                "browse_candidates",
                1,
                control_key=control_key,
                query="",
                cursor=None,
                modal_handle=None,
            ),
        )
        assert candidates["settlement"] == "settled"
        assert candidates["result"]["candidate"]["type"] == "users"

        invalid = await preview._action(
            page.id,
            _command_body(
                page,
                "run_command",
                2,
                entry,
                options={"名前": "x"},
            ),
        )
        assert invalid["diagnostics"][0]["code"] == "command-option-invalid"
        assert invalid["diagnostics"][0]["subject"] == {"commandOption": "名前"}


@pytest.mark.asyncio
async def test_catalog_read_does_not_wait_for_action_pipeline(env, channel, alice):
    async with env.preview(channel, viewers=[alice], layout="channel") as preview, ClientSession() as client:
        page = preview._open_page(alice.id)
        entry = _entry(page, "paced")
        running = asyncio.create_task(
            preview._action(
                page.id,
                _command_body(page, "run_command", 1, entry, options={}),
            )
        )
        for _ in range(100):
            if preview._active_action is not None:
                break
            await asyncio.sleep(0.001)
        assert preview._active_action is not None
        busy = await preview._action(
            page.id,
            _command_body(
                page,
                "autocomplete_command",
                2,
                _entry(page, "tag"),
                focused="name",
                value="py",
                options={},
            ),
        )
        assert busy["diagnostics"][0]["code"] == "busy"
        assert page.last_sequence == 1
        response = await client.get(
            preview._origin + "/api/commands",
            headers=preview_headers(preview, page.id),
        )
        assert response.status == 200
        assert (await response.json())["fingerprint"] == page.command_catalog["fingerprint"]
        assert (await running)["settlement"] == "settled"


@pytest.mark.asyncio
async def test_multipart_command_attachment_and_upload_limit(env, channel, alice):
    async with env.preview(channel, viewers=[alice], layout="channel") as preview, ClientSession() as client:
        page = preview._open_page(alice.id)
        entry = _entry(page, "upload")
        body = _command_body(
            page,
            "run_command",
            1,
            entry,
            options={"attachment": {"upload": 0}},
        )
        form = FormData()
        form.add_field("payload", json.dumps(body))
        form.add_field("file:attachment", b"hello", filename="data.txt")
        response = await client.post(
            preview._origin + "/api/action",
            headers=preview_headers(preview, page.id),
            data=form,
        )
        receipt = await response.json()
        assert response.status == 200
        assert receipt["settlement"] == "settled"
        response_message = page.snapshot["messages"][receipt["outcomes"][0]["messageId"]]
        assert "data.txt:hello" in response_message["content"]

        too_large = _command_body(
            page,
            "run_command",
            2,
            entry,
            options={"attachment": {"upload": 0}},
        )
        oversized = FormData()
        oversized.add_field("payload", json.dumps(too_large))
        oversized.add_field("file:attachment", b"x" * (10 * 1024 * 1024 + 1), filename="large.txt")
        response = await client.post(
            preview._origin + "/api/action",
            headers=preview_headers(preview, page.id),
            data=oversized,
        )
        assert response.status == 413


@pytest.mark.asyncio
async def test_dm_catalog_and_command_run(env, alice):
    await alice.send_dm("hello")
    dm = alice.user.dm_channel
    async with env.preview(dm, viewers=[alice.user], layout="channel") as preview:
        page = preview._python
        assert page.command_catalog["state"] == "available"
        assert not list(_VALIDATOR.iter_errors(page.snapshot))
        assert not list(_CATALOG_VALIDATOR.iter_errors(page.command_catalog))
        assert "dm_greeting" in {item["invocation"] for item in page.command_catalog["entries"]}
        assert page.snapshot["channel"]["canUseApplicationCommands"] is True
        entry = _entry(page, "dm_greeting")
        receipt = await preview._action(
            page.id,
            _command_body(page, "run_command", 1, entry, options={}),
        )
        assert receipt["settlement"] == "settled"
        assert receipt["command"]["invocation"] == "dm_greeting"
