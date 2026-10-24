from __future__ import annotations

import io
import json
from copy import deepcopy

import discord
import pytest
from discord import app_commands
from preview_helpers import action_body, control_key, png_bytes

from simcord.backend.cdn import CDN_BASE
from simcord.backend.errors import SetupError
from simcord.preview import Preview


@pytest.mark.asyncio
@pytest.mark.parametrize("forwarded", [False, True])
async def test_inline_text_attachment_reauthorizes_owner_without_retained_bytes(
    env, channel, alice, forwarded, monkeypatch
):
    # Apply the budget before the initial publication can retain the blob.
    monkeypatch.setattr(Preview, "_MAX_MEDIA_BYTES", 0)
    source = await env.bot.get_channel(channel.id).send(
        "Frozen message", file=discord.File(io.BytesIO(b"private inline body"), filename="private-label.txt")
    )
    message = await alice.forward(source, channel) if forwarded else source
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        published = deepcopy(page.snapshot)
        body = published["messages"][str(message.id)]
        attachment = (body["snapshots"][0] if forwarded else body)["attachments"][0]
        assert attachment["preview"] == "private inline body"
        assert attachment["available"] is False
        assert "private inline body" in json.dumps(preview._page_payload(page))
        if forwarded:
            await source.delete()
            assert "private inline body" in json.dumps(preview._page_payload(page))
            env.backend.get_message(channel.id, message.id).snapshots[0]["message"]["attachments"] = []
        else:
            await message.edit(attachments=[])
        observed = preview._page_payload(page)
        assert "private inline body" not in json.dumps(observed)
        assert "private-label.txt" not in json.dumps(observed)
        assert observed["publishedRevision"] == published["publishedRevision"]
        assert page.snapshot == published


@pytest.mark.asyncio
async def test_send_grant_does_not_change_frozen_pin_but_dependency_loss_aborts(env, channel, alice):
    member = env.bot.get_guild(env.guild.id).get_member(alice.id)
    cached_channel = env.bot.get_channel(channel.id)
    await cached_channel.set_permissions(member, send_messages=False)
    dependency = env.guild.create_text_channel("frozen-dependency")
    message = await cached_channel.send(f"Published <#{dependency.id}>")
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        await preview.show(message)
        page = preview._python
        pin = preview._pin_capture(alice, message, 0.0, (960, 720), "channel")
        frozen = deepcopy(pin.snapshot)
        try:
            assert frozen["channel"]["canSendMessages"] is False
            await cached_channel.set_permissions(member, send_messages=True)
            await message.edit(content="Unpublished replacement")
            preview._assert_capture_live(pin.page)
            assert preview._page_payload(page)["channel"]["canSendMessages"] is False
            assert pin.snapshot == frozen
            await env.bot.get_channel(dependency.id).set_permissions(member, view_channel=False)
            with pytest.raises(SetupError, match="dependency access was revoked"):
                preview._assert_capture_live(pin.page)
            assert pin.snapshot == frozen
        finally:
            preview._close_page(pin.page.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["removed", "disabled", "replaced"])
async def test_candidate_replay_checks_original_control_without_callback(env, channel, alice, change):
    view = discord.ui.View(timeout=None)
    select = discord.ui.UserSelect(custom_id="retained-users")
    view.add_item(select)
    message = await env.bot.get_channel(channel.id).send("Candidate source", view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        key = control_key(page.snapshot, "retained-users")
        body = action_body(page, "browse_candidates", 1, control_key=key, query="")
        original = await preview._action(page.id, body)
        assert original["result"]["candidate"]["type"] == "users"
        if change == "removed":
            await message.edit(view=None)
        elif change == "disabled":
            select.disabled = True
            await message.edit(view=view)
        else:
            replacement = discord.ui.View(timeout=None)
            replacement.add_item(discord.ui.RoleSelect(custom_id="retained-users"))
            await message.edit(view=replacement)
        replay = await preview._action(page.id, body)
        assert "result" not in replay
        assert replay["settlement"] == original["settlement"] == "settled"
        assert replay["outcomes"] == original["outcomes"]
        assert replay["requestId"] == original["requestId"]
        assert replay["rejected"] is False


@pytest.mark.asyncio
async def test_latest_summary_keeps_own_sources_after_another_publication(env, channel, alice):
    dependency = env.guild.create_text_channel("original-private-label")
    retained = env.guild.create_text_channel("original-retained-label")
    message = await env.bot.get_channel(channel.id).send(f"Read <#{dependency.id}> and <#{retained.id}>")
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        page = preview._python
        body = action_body(page, "browse_messages", 1, query="", filter="all")
        original = await preview._action(page.id, body)
        assert "original-private-label" in json.dumps(original["result"])
        await message.edit(content="Unpublished unrelated replacement")
        await env.bot.get_channel(retained.id).edit(name="new-retained-label")
        # Python publication does not supersede this browser's admitted request.
        preview._publish(page, reason="refresh")
        assert (
            page.summary_sources[str(message.id)] is not page.latest_action.summary_sources[str(message.id)]
        )
        member = env.bot.get_guild(env.guild.id).get_member(alice.id)
        await env.bot.get_channel(dependency.id).set_permissions(member, view_channel=False)
        replay = await preview._action(page.id, body)
        encoded = json.dumps(replay["result"])
        assert "original-private-label" not in encoded
        assert "#original-retained-label" in encoded
        assert "new-retained-label" not in encoded
        assert "Unpublished unrelated replacement" not in encoded
        assert replay["settlement"] == original["settlement"]
        retained_action = page.latest_action
        await preview._action(page.id, action_body(page, "refresh", 2))
        assert retained_action.summary_sources == {}
        assert "result" not in retained_action.response


@pytest.mark.asyncio
async def test_modal_replay_retains_labels_and_rejects_replacement_without_dispatch(env, channel, alice):
    calls = 0
    victim = env.guild.add_member(env.create_user("original-picker-label"))

    class Picker(discord.ui.Modal, title="Picker"):
        member = discord.ui.Label(text="Member", component=discord.ui.UserSelect(custom_id="modal-member"))

    class OpenPicker(discord.ui.View):
        @discord.ui.button(label="Open", custom_id="open-picker")
        async def open_picker(self, interaction: discord.Interaction, button: discord.ui.Button):
            nonlocal calls
            calls += 1
            await interaction.response.send_modal(Picker())

    message = await env.bot.get_channel(channel.id).send("Picker", view=OpenPicker())
    opened = await alice.click(message, custom_id="open-picker")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(opened)
        page = preview._python
        key = page.snapshot["modal"]["payload"]["components"][0]["component"]["control_key"]
        body = action_body(
            page,
            "browse_candidates",
            1,
            control_key=key,
            modal_handle=page.modal_handle,
            query="original-picker-label",
        )
        original = await preview._action(page.id, body)
        assert original["result"]["candidate"]["entries"][0]["label"] == "original-picker-label"
        await env.bot.get_guild(env.guild.id).get_member(victim.id).edit(nick="unpublished-picker-label")
        assert (await preview._action(page.id, body))["result"] == original["result"]
        assert calls == 1
        replacement = await alice.click(message, custom_id="open-picker")
        await preview.show(replacement)
        replay = await preview._action(page.id, body)
        assert "result" not in replay
        assert replay["settlement"] == original["settlement"]
        assert replay["outcomes"] == original["outcomes"]
        assert calls == 2


@pytest.mark.asyncio
async def test_command_candidate_replay_retains_original_schema_after_publication(env, channel, alice):
    calls = 0

    async def callback(interaction: discord.Interaction, user: discord.Member) -> None:
        nonlocal calls
        calls += 1
        await interaction.response.send_message(user.display_name)

    command = app_commands.Command(name="provenance-picker", description="Original", callback=callback)
    env.bot.tree.add_command(command)
    await env.bot.tree.sync()
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        page = preview._python
        entry = next(item for item in page.command_catalog["entries"] if item["invocation"] == command.name)
        key = f"command:{entry['commandId']}:{command.name}:option:user"
        body = action_body(page, "browse_candidates", 1, control_key=key, query="")
        original = await preview._action(page.id, body)
        assert original["result"]["candidate"]["type"] == "users"
        env.backend.commands[None][(command.name, 1)]["description"] = "Replacement schema"
        preview._publish(page, reason="refresh")
        replay = await preview._action(page.id, body)
        assert "result" not in replay
        assert replay["settlement"] == original["settlement"] == "settled"
        assert replay["outcomes"] == original["outcomes"]
        assert calls == 0


@pytest.mark.asyncio
async def test_disabled_published_candidate_is_not_a_new_dependency_loss(env, channel, alice):
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.UserSelect(
            custom_id="disabled-member",
            disabled=True,
            default_values=[
                discord.SelectDefaultValue(
                    id=alice.id,
                    type=discord.SelectDefaultValueType.user,
                )
            ],
        )
    )
    message = await env.bot.get_channel(channel.id).send("Frozen disabled selection", view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        key = control_key(page.snapshot, "disabled-member")
        assert preview._page_payload(page)["candidates"][key]["selected"][0]["id"] == str(alice.id)
        pin = preview._pin_capture(alice, message, 0.0, (960, 720), "message")
        try:
            preview._assert_capture_live(pin.page)
        finally:
            preview._close_page(pin.page.id)


@pytest.mark.asyncio
async def test_latest_result_retains_its_asset_sources_until_supersession(env, channel, alice):
    user = env.create_user("source-author", avatar="original")
    author = env.guild.add_member(user)
    source = await author.send(channel, "Original row")
    other = await env.bot.get_channel(channel.id).send("Other row")
    url = f"{CDN_BASE}/avatars/{user.id}/original.png"
    async with env.preview(
        channel, viewers=[alice], assets={url: ("original-avatar.png", png_bytes())}
    ) as preview:
        await preview.show(source)
        page = preview._python
        body = action_body(page, "browse_messages", 1, query="", filter="all")
        original = await preview._action(page.id, body)
        row = next(item for item in original["result"]["messageIndex"] if item["id"] == str(source.id))
        asset_id = row["author"]["avatar"]
        page.target_id = other.id
        page.navigation_query = "other row"
        preview._publish(page, reason="navigation")
        assert asset_id not in page.snapshot["assets"]
        assert asset_id in page.assets
        replay = await preview._action(page.id, body)
        recovered = next(item for item in replay["result"]["messageIndex"] if item["id"] == str(source.id))
        assert recovered["author"] == row["author"]
        assert (await preview._prepare_asset(page.id, asset_id, download=True))[1] == png_bytes()
        await preview._action(page.id, action_body(page, "refresh", 2))
        assert asset_id not in page.assets


@pytest.mark.asyncio
async def test_dm_bot_navigation_avatar_survives_human_focus_and_capture(env, alice, tmp_path):
    user = alice.user
    await user.send_dm("Open the DM")
    channel = user.dm_channel
    native_channel = await env.bot.fetch_channel(channel.id)
    bot_message = await native_channel.send("Earlier bot message")
    human = await user.send_dm("Focused human message")
    async with env.preview(channel, viewers=[user]) as preview:
        await preview.show(human)
        page = preview._python
        frozen = deepcopy(page.snapshot)
        assert str(bot_message.id) not in frozen["messages"]
        row = next(item for item in frozen["messageIndex"] if item["id"] == str(bot_message.id))
        avatar_id = row["author"]["avatar"]
        observed = preview._page_payload(page)
        observed_row = next(item for item in observed["messageIndex"] if item["id"] == str(bot_message.id))
        assert observed_row["author"]["avatar"] == avatar_id
        assert page.snapshot == frozen
        capture = await preview.screenshot(tmp_path / "dm.png", target=human)
        assert capture.complete and capture.target_id == str(human.id)
        env.backend.delete_channel(channel.id)
        with pytest.raises(SetupError):
            preview._authorize_asset(page, avatar_id, content=False)
        revoked = tmp_path / "revoked.png"
        with pytest.raises(SetupError):
            await preview.screenshot(revoked, target=human.id, allow_incomplete=True)
        assert not revoked.exists()
