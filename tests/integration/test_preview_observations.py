from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from importlib import resources

import discord
import pytest
from aiohttp import ClientSession
from discord import app_commands
from preview_helpers import action_body, control_key, preview_headers

from simcord.backend.errors import SetupError


@pytest.mark.asyncio
async def test_cached_dependencies_redact_without_refresh_and_capture_aborts(env, channel, alice):
    secret = env.guild.create_text_channel("cached-secret-name")
    retained = env.guild.create_text_channel("cached-public-name")
    content = f"Published <#{secret.id}> and <#{retained.id}>"
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.ChannelSelect(custom_id="channel-picker"))
    message = await env.bot.get_channel(channel.id).send(content, view=view)
    async with env.preview(channel, viewers=[alice], layout="channel") as preview, ClientSession() as client:
        await preview.show(message)
        page = preview._python
        published = deepcopy(page.snapshot)
        revision = page.revision
        pin = preview._pin_capture(alice, message, 0.0, (960, 720), "channel")
        preview._assert_capture_live(pin.page)
        await message.edit(content="Unpublished replacement")
        await env.bot.get_channel(retained.id).edit(name="unpublished-public-name")
        member = env.bot.get_guild(env.guild.id).get_member(alice.id)
        await env.bot.get_channel(secret.id).set_permissions(member, view_channel=False)
        response = await client.get(preview._origin + "/api/state", headers=preview_headers(preview, page.id))
        assert response.status == 200
        observed = await response.json()
        assert observed["publishedRevision"] == revision
        assert observed["messages"][str(message.id)]["content"] == content
        assert "Unpublished replacement" not in json.dumps(observed)
        assert "cached-secret-name" not in json.dumps(observed)
        assert "unpublished-public-name" not in json.dumps(observed)
        row = next(item for item in observed["messageIndex"] if item["id"] == str(message.id))
        assert "#cached-public-name" in row["excerpt"]
        assert all(
            item["id"] != str(secret.id)
            for candidate in observed["candidates"].values()
            for item in candidate["entries"] + candidate["selected"]
        )
        assert page.snapshot == published
        with pytest.raises(SetupError, match="dependency access was revoked"):
            preview._assert_capture_live(pin.page)


@pytest.mark.asyncio
async def test_catalog_manifest_tracks_revoked_application_avatar(env, channel, alice):
    async with env.preview(channel, viewers=[alice], layout="channel") as preview, ClientSession() as client:
        page = preview._python
        headers = preview_headers(preview, page.id)
        original = await (await client.get(preview._origin + "/api/commands", headers=headers)).json()
        published = deepcopy(page.command_catalog)
        assert original["application"]["avatarAssetId"] is not None
        env.backend.bot_user.avatar = "replacement-avatar"
        observed = preview._page_payload(page)
        current = await (await client.get(preview._origin + "/api/commands", headers=headers)).json()
        assert current["application"]["avatarAssetId"] is None
        assert current["fingerprint"] != original["fingerprint"]
        assert observed["commands"]["fingerprint"] == current["fingerprint"]
        assert observed["publishedRevision"] == page.revision
        assert page.command_catalog == published


@pytest.mark.asyncio
async def test_cached_reply_and_deleted_root_are_filtered_without_publication(env, channel, alice):
    source = await env.bot.get_channel(channel.id).send("Private referenced excerpt")
    root = await env.bot.get_channel(channel.id).send("Published reply", reference=source)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(root)
        page = preview._python
        assert page.snapshot["messages"][str(root.id)]["reply"]["state"] == "resolved"
        original = deepcopy(page.snapshot)
        await source.delete()
        observed = preview._page_payload(page)
        assert observed["messages"][str(root.id)]["reply"] == {"state": "unavailable"}
        assert "Private referenced excerpt" not in json.dumps(observed)
        await root.delete()
        observed = preview._page_payload(page)
        assert str(root.id) not in observed["messages"]
        assert all(item["id"] != str(root.id) for item in observed["messageIndex"])
        assert page.snapshot == original


@pytest.mark.asyncio
async def test_action_result_recovers_original_choices_and_rechecks_scope(env, channel, alice):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def callback(interaction: discord.Interaction, query: str) -> None:
        await interaction.response.send_message(query)

    command = app_commands.Command(name="observation-search", description="Search", callback=callback)

    async def autocomplete(interaction: discord.Interaction, current: str):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return [app_commands.Choice(name="Retained original", value=current)]

    command.autocomplete("query")(autocomplete)
    env.bot.tree.add_command(command)
    await env.bot.tree.sync()
    async with env.preview(channel, viewers=[alice], layout="channel") as preview, ClientSession() as client:
        page = preview._open_page(alice.id)
        entry = next(item for item in page.command_catalog["entries"] if item["invocation"] == command.name)
        headers = preview_headers(preview, page.id)
        url = preview._origin + "/api/action-result?request_id=original&sequence=1"
        body = action_body(
            page,
            "autocomplete_command",
            1,
            request_id="original",
            command_id=entry["commandId"],
            path=entry["path"],
            schema_fingerprint=entry["schemaFingerprint"],
            focused="query",
            value="needle",
            options={},
        )
        task = asyncio.create_task(preview._action(page.id, body))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            pending = await client.get(url, headers=headers)
            assert pending.status == 409
        finally:
            release.set()
        original = await task
        recovered = await client.get(url, headers=headers)
        assert recovered.status == 200
        assert (await recovered.json())["result"] == original["result"]
        assert original["result"]["choices"] == [{"name": "Retained original", "value": "needle"}]
        assert calls == 1
        assert (await client.get(url)).status == 401
        for suffix in (
            "?request_id=other&sequence=1",
            "?request_id=original&sequence=0",
            "?request_id=original&sequence=bad",
        ):
            assert (
                await client.get(preview._origin + "/api/action-result" + suffix, headers=headers)
            ).status == 404
        registered = env.backend.commands[None][(command.name, 1)]
        registered["description"] = "Changed schema"
        assert (await client.get(url, headers=headers)).status == 404
        catalog_response = await client.get(preview._origin + "/api/commands", headers=headers)
        assert all(
            item["commandId"] != entry["commandId"] for item in (await catalog_response.json())["entries"]
        )
        assert entry in page.command_catalog["entries"]
        assert calls == 1
        preview._close_page(page.id)
        assert (await client.get(url, headers=headers)).status == 410


@pytest.mark.asyncio
async def test_command_permission_and_diagnostic_metadata_are_current(env, channel, alice):
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        page = preview._python
        headers = preview_headers(preview, page.id)
        member = env.bot.get_guild(env.guild.id).get_member(alice.id)
        await env.bot.get_channel(channel.id).set_permissions(member, use_application_commands=False)
        response = await client.get(preview._origin + "/api/commands", headers=headers)
        assert (await response.json())["state"] == "unavailable"
        state = await client.get(preview._origin + "/api/state", headers=headers)
        observed = await state.json()
        assert observed["commands"]["state"] == "unavailable"
        assert observed["channel"]["canUseApplicationCommands"] is False
        metadata = await client.get(preview._origin + "/backend-diagnostics.json")
        assert metadata.status == 200
        expected = json.loads(
            resources.files("simcord.preview").joinpath("static/backend-diagnostics.json").read_text()
        )
        assert await metadata.json() == expected
        assert metadata.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_cached_user_candidates_and_mentions_revoke_without_refresh(env, channel, alice):
    victim = env.guild.add_member(env.create_user("cached-user-name"))
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.UserSelect(custom_id="users"))
    message = await env.bot.get_channel(channel.id).send(f"Published <@{victim.id}>", view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        original = deepcopy(page.snapshot)
        assert "cached-user-name" in json.dumps(original)
        env.guild.remove_member(victim)
        observed = preview._page_payload(page)
        assert "cached-user-name" not in json.dumps(observed)
        assert str(victim.id) not in observed["entities"]["users"]
        assert all(
            item["id"] != str(victim.id)
            for descriptor in observed["candidates"].values()
            for item in descriptor["entries"] + descriptor["selected"]
        )
        assert observed["publishedRevision"] == original["publishedRevision"]
        assert page.snapshot == original


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["permission", "context", "bot_generation", "superseded"])
async def test_retained_result_scope_is_submission_owned(env, channel, alice, change):
    async def callback(interaction: discord.Interaction, query: str) -> None:
        await interaction.response.send_message(query)

    command = app_commands.Command(name="scope-search", description="Scope", callback=callback)

    async def autocomplete(interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name="Original", value=current)]

    command.autocomplete("query")(autocomplete)
    env.bot.tree.add_command(command)
    await env.bot.tree.sync()
    message = await env.bot.get_channel(channel.id).send("Focus")
    async with env.preview(channel, viewers=[alice], layout="channel") as preview, ClientSession() as client:
        page = preview._python
        entry = next(item for item in page.command_catalog["entries"] if item["invocation"] == command.name)
        receipt = await preview._action(
            page.id,
            action_body(
                page,
                "autocomplete_command",
                1,
                request_id="scoped",
                command_id=entry["commandId"],
                path=entry["path"],
                schema_fingerprint=entry["schemaFingerprint"],
                focused="query",
                value="needle",
                options={},
            ),
        )
        assert receipt["settlement"] == "settled"
        if change == "permission":
            member = env.bot.get_guild(env.guild.id).get_member(alice.id)
            await env.bot.get_channel(channel.id).set_permissions(member, use_application_commands=False)
        elif change == "context":
            await preview.show(message)
        elif change == "bot_generation":
            env._generation += 1
        else:
            await preview._action(page.id, action_body(page, "refresh", 2, request_id="newer"))
        result = await client.get(
            preview._origin + "/api/action-result?request_id=scoped&sequence=1",
            headers=preview_headers(preview, page.id),
        )
        assert result.status == 404


@pytest.mark.asyncio
async def test_candidate_replay_omits_result_after_source_deletion(env, channel, alice):
    from jsonschema import Draft202012Validator

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.UserSelect(custom_id="retained-users"))
    message = await env.bot.get_channel(channel.id).send("Published candidate source", view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        key = control_key(page.snapshot, "retained-users")
        body = action_body(page, "browse_candidates", 1, control_key=key, query="")
        original = await preview._action(page.id, body)
        assert original["result"]["candidate"]["type"] == "users"
        await message.delete()
        replay = await preview._action(page.id, body)
        schema = json.loads(resources.files("simcord.preview").joinpath("protocol.schema.json").read_text())
        Draft202012Validator({"$ref": "#/$defs/actionResponse", "$defs": schema["$defs"]}).validate(replay)
        assert "result" not in replay
        assert replay["requestId"] == original["requestId"]
        assert replay["settlement"] == "settled"
        assert replay["rejected"] is False
