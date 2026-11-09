"""Native original-response and followup lifecycle, including pending originals."""

import datetime
import io

import discord
import pytest
from discord import app_commands
from discord.ext import commands
from discord.http import Route

import simcord


async def _interaction_callback(
    bot: commands.Bot,
    interaction: discord.Interaction,
    callback_type: int,
    data: dict | None = None,
    *,
    interaction_id: int | None = None,
    token: str | None = None,
) -> dict:
    route = Route(
        "POST",
        "/interactions/{interaction_id}/{token}/callback",
        interaction_id=interaction.id if interaction_id is None else interaction_id,
        token=interaction.token if token is None else token,
    )
    payload = {"type": callback_type}
    if data is not None:
        payload["data"] = data
    return await bot.http.request(route, json=payload)


def _make_bot() -> commands.Bot:
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.tree.command(name="followups", description="exercise followup edit/fetch/delete")
    async def followups(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("original")
        msg = await interaction.followup.send("first", wait=True)
        await msg.edit(content="edited-followup")
        fetched = await interaction.followup.fetch_message(msg.id)
        assert fetched.content == "edited-followup"
        await msg.delete()

    @bot.tree.command(name="deferedit", description="defer then edit the original in")
    async def deferedit(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.edit_original_response(content="completed")

    @bot.tree.command(name="sendedit", description="send, edit, then delete the original")
    async def sendedit(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("orig")
        await interaction.edit_original_response(content="edited-orig")
        await interaction.delete_original_response()

    return bot


async def test_slash_option_validation_errors():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

    @bot.tree.command(name="echo")
    @app_commands.choices(mode=[app_commands.Choice(name="Loud", value="loud")])
    async def echo(interaction: discord.Interaction, text: str, count: int, mode: str) -> None:
        await interaction.response.send_message(text)

    async with simcord.run(bot, strict_sync=False) as env:
        env.create_guild()
        ch = env.guild.create_text_channel("general")
        alice = env.guild.add_member(env.create_user("alice"))

        # Unknown option.
        with pytest.raises(simcord.SetupError):
            await alice.slash(ch, "echo", text="hi", count=1, mode="loud", bogus="x")
        # Scalar type mismatch (text expects str).
        with pytest.raises(simcord.SetupError):
            await alice.slash(ch, "echo", text=123, count=1, mode="loud")
        # Value outside the declared choices.
        with pytest.raises(simcord.SetupError):
            await alice.slash(ch, "echo", text="hi", count=1, mode="whisper")
        # Missing a required option.
        with pytest.raises(simcord.SetupError):
            await alice.slash(ch, "echo", text="hi")


async def test_message_context_menu():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

    @bot.tree.context_menu(name="Pin It")
    async def pin_it(interaction: discord.Interaction, message: discord.Message) -> None:
        await interaction.response.send_message(f"Pinned {message.id}", ephemeral=True)

    async with simcord.run(bot, strict_sync=False) as env:
        env.create_guild()
        ch = env.guild.create_text_channel("general")
        alice = env.guild.add_member(env.create_user("alice"))
        msg = await alice.send(ch, "target")

        result = await alice.context_menu(ch, "Pin It", msg)
        assert f"Pinned {msg.id}" == result.response.content


async def test_followup_edit_fetch_delete():
    bot = _make_bot()
    async with simcord.run(bot, strict_sync=False) as env:
        env.create_guild()
        ch = env.guild.create_text_channel("general")
        alice = env.guild.add_member(env.create_user("alice"))

        result = await alice.slash(ch, "followups")
        # Only the independent followup was deleted.
        assert result.followups == []
        assert [message.content for message in ch.history()] == ["original"]


async def test_deferred_original_is_completed_on_edit():
    bot = _make_bot()
    async with simcord.run(bot, strict_sync=False) as env:
        env.create_guild()
        ch = env.guild.create_text_channel("general")
        alice = env.guild.add_member(env.create_user("alice"))

        result = await alice.slash(ch, "deferedit")
        assert result.response is not None
        assert result.response.content == "completed"


async def test_original_response_edit_then_delete():
    bot = _make_bot()
    async with simcord.run(bot, strict_sync=False) as env:
        env.create_guild()
        ch = env.guild.create_text_channel("general")
        alice = env.guild.add_member(env.create_user("alice"))

        await alice.slash(ch, "sendedit")
        # Sent, edited, then deleted: the channel ends up empty.
        assert ch.history() == []


async def test_native_callback_rejections_preserve_ack_and_response_lifecycle():
    admitted: list[discord.Interaction] = []
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

    @bot.tree.command(name="admit", description="admit a callback lifecycle test")
    async def admit(interaction: discord.Interaction) -> None:
        admitted.append(interaction)

    async with simcord.run(bot, strict_sync=False) as env:
        env.create_guild()
        ch = env.guild.create_text_channel("general")
        alice = env.guild.add_member(env.create_user("alice"))
        first_result = await alice.slash(ch, "admit")
        second_result = await alice.slash(ch, "admit")
        first, second = admitted

        # Callback credentials are paired: neither a mismatched pair nor an
        # unknown id/token can reveal an interaction or acknowledge either one.
        for interaction_id, token in (
            (first.id, second.token),
            (first.id, f"{first.token}-unknown"),
            (first.id + 1, first.token),
        ):
            with pytest.raises(discord.NotFound) as error:
                await _interaction_callback(
                    bot,
                    first,
                    discord.InteractionResponseType.channel_message.value,
                    {"content": "must not appear"},
                    interaction_id=interaction_id,
                    token=token,
                )
            assert error.value.code == 10062
            assert token not in str(error.value)
            assert not first_result.acknowledged
            assert not second_result.acknowledged
            assert ch.history() == []

        # The original-response endpoint also rejects an admitted interaction
        # that has not yet produced a message.
        with pytest.raises(discord.NotFound) as error:
            await first.original_response()
        assert error.value.code == 10008

        # Invalid callbacks are safe to reject and do not consume this admission.
        for callback_type, data in (
            (
                discord.InteractionResponseType.modal.value,
                {"custom_id": "bad", "title": "Bad", "components": []},
            ),
            (99, {}),
            (discord.InteractionResponseType.channel_message.value, {"content": "x" * 2001}),
        ):
            with pytest.raises(discord.HTTPException) as error:
                await _interaction_callback(bot, first, callback_type, data)
            assert error.value.status == 400
            assert error.value.code == 50035
            assert not first.response.is_done()
            assert not first_result.acknowledged
            assert ch.history() == []

        await first.response.send_message("accepted")
        assert first_result.acknowledged
        assert first_result.response.content == "accepted"
        assert [message.content for message in ch.history()] == ["accepted"]

        with pytest.raises(discord.HTTPException) as error:
            await _interaction_callback(
                bot,
                first,
                discord.InteractionResponseType.channel_message.value,
                {"content": "duplicate"},
            )
        assert error.value.code == 40060
        assert first_result.response.content == "accepted"
        assert [message.content for message in ch.history()] == ["accepted"]
        assert not second_result.acknowledged

        original = await first.original_response()
        assert original.content == "accepted"
        edited = await first.edit_original_response(content="edited")
        assert edited.content == "edited"
        assert first_result.response.content == "edited"
        await first.delete_original_response()
        assert ch.history() == []


async def test_interaction_followup_token_cannot_access_an_ordinary_message():
    admitted: list[discord.Interaction] = []
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

    @bot.tree.command(name="admit", description="admit a followup boundary test")
    async def admit(interaction: discord.Interaction) -> None:
        admitted.append(interaction)

    async with simcord.run(bot, strict_sync=False) as env:
        env.create_guild()
        ch = env.guild.create_text_channel("general")
        alice = env.guild.add_member(env.create_user("alice"))
        result = await alice.slash(ch, "admit")
        interaction = admitted[0]
        ordinary = await alice.send(ch, "ordinary")

        errors = []
        for request in (
            interaction.followup.fetch_message(ordinary.id),
            interaction.followup.edit_message(ordinary.id, content="tampered"),
            interaction.followup.delete_message(ordinary.id),
        ):
            try:
                await request
            except discord.HTTPException as error:
                errors.append(error)
            else:
                errors.append(None)

        assert all(isinstance(error, discord.NotFound) and error.code == 10008 for error in errors)
        assert all(interaction.token not in str(error) for error in errors if error is not None)
        assert not result.acknowledged
        assert ordinary.content == "ordinary"
        assert [(message.id, message.content) for message in ch.history()] == [(ordinary.id, "ordinary")]


@pytest.mark.parametrize("ephemeral", [False, True])
@pytest.mark.parametrize("requested_ephemeral", [False, True])
@pytest.mark.parametrize("completion", ["followup", "original", "message_id"])
async def test_pending_original_identity_visibility_and_completion(
    env, channel, alice, ephemeral, requested_ephemeral, completion
):
    admitted = []

    @env.bot.tree.command(name="pending")
    async def pending(interaction: discord.Interaction) -> None:
        admitted.append(interaction)
        callback = await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        original = await interaction.original_response()
        assert callback.message_id == original.id
        assert callback.resource is None
        assert callback.is_thinking()
        assert callback.is_ephemeral() is ephemeral
        assert original.flags.loading
        assert original.flags.ephemeral is ephemeral

    await env.bot.tree.sync()
    result = await alice.slash(channel, "pending")
    interaction = admitted[0]
    original = await interaction.original_response()
    canonical_original = env.backend.get_message(channel.id, original.id)
    assert result.response.id == original.id
    assert result.response.flags.loading
    assert result.deferred
    assert [item.id for item in channel.history()] == ([] if ephemeral else [original.id])
    bystander = env.guild.add_member(env.create_user("bystander"))
    assert [item.id for item in channel.history(viewer=alice)] == [original.id]
    assert [item.id for item in channel.history(viewer=bystander)] == ([] if ephemeral else [original.id])
    assert [item.id for item in env.bot.cached_messages] == ([] if ephemeral else [original.id])
    fetched = await interaction.followup.fetch_message(original.id)
    assert fetched.id == original.id and fetched.flags.loading

    # A malformed completion must leave both the loading message and protocol
    # state untouched, so the next valid completion still consumes the original.
    with pytest.raises(discord.HTTPException) as error:
        await interaction.followup.edit_message(original.id, content="x" * 2001)
    assert error.value.code == 50035
    with pytest.raises(discord.HTTPException) as error:
        await interaction.followup.send("x" * 2001, ephemeral=requested_ephemeral, wait=True)
    assert error.value.code == 50035
    assert result.deferred and result.response.flags.loading

    if completion == "followup":
        completed = await interaction.followup.send("completed", ephemeral=requested_ephemeral, wait=True)
    elif completion == "original":
        completed = await interaction.edit_original_response(content="completed")
    else:
        completed = await interaction.followup.edit_message(original.id, content="completed")
    assert completed.id == original.id
    assert env.backend.get_message(channel.id, original.id) is canonical_original
    assert completed.flags.ephemeral is ephemeral
    assert not completed.flags.loading
    assert result.response.id == original.id
    assert result.response.content == "completed"
    assert not result.deferred and not result.response.flags.loading
    assert result.followups == []
    await env.settle()
    cached = [item for item in env.bot.cached_messages if item.id == original.id]
    assert len(cached) == (0 if ephemeral else 1)
    if cached:
        assert cached[0].content == "completed" and not cached[0].flags.loading
    # discord.py caches original_response(); the cached instance is a snapshot,
    # not a reason to allocate a replacement original on the server.
    assert await interaction.original_response() is original
    assert original.flags.loading
    fetched = await interaction.followup.fetch_message(original.id)
    assert fetched.content == "completed" and not fetched.flags.loading

    later = await interaction.followup.send("independent", ephemeral=not ephemeral, wait=True)
    assert later.id != original.id
    assert later.flags.ephemeral is not ephemeral
    assert not later.flags.loading
    assert [item.id for item in result.followups] == [later.id]
    assert [item.id for item in channel.history()] == ([later.id] if ephemeral else [original.id])
    assert result.response.content == "completed"


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("by_id", [False, True])
async def test_deleted_original_is_not_resurrected_and_expiry_is_distinct(
    env, channel, alice, pending, by_id
):
    admitted = []

    @env.bot.tree.command(name="deleteprobe")
    async def deleteprobe(interaction: discord.Interaction) -> None:
        admitted.append(interaction)
        if pending:
            await interaction.response.defer(thinking=True)
        else:
            await interaction.response.send_message("original")

    await env.bot.tree.sync()
    result = await alice.slash(channel, "deleteprobe")
    interaction = admitted[0]
    original_id = result.response.id
    if by_id:
        await interaction.followup.delete_message(original_id)
    else:
        await interaction.delete_original_response()
    assert result.response is None
    assert channel.history() == []
    original_route = Route(
        "GET",
        "/webhooks/{webhook_id}/{token}/messages/@original",
        webhook_id=interaction.application_id,
        token=interaction.token,
    )
    for request in (
        env.bot.http.request(original_route),
        interaction.edit_original_response(content="resurrect"),
        interaction.followup.fetch_message(original_id),
        interaction.followup.edit_message(original_id, content="resurrect"),
        interaction.followup.delete_message(original_id),
        interaction.delete_original_response(),
    ):
        with pytest.raises(discord.NotFound) as error:
            await request
        assert error.value.code == 10008
    if pending:
        with pytest.raises(discord.NotFound) as error:
            await interaction.followup.send("resurrect", wait=True)
        assert error.value.code == 10008
    else:
        later = await interaction.followup.send("independent", ephemeral=True, wait=True)
        assert later.id != original_id
        await later.delete()
    assert result.response is None and channel.history() == []
    env.backend.advance_clock(15 * 60)
    for request in (
        env.bot.http.request(original_route),
        interaction.edit_original_response(content="expired"),
        interaction.followup.send("expired", wait=True),
        interaction.followup.fetch_message(original_id),
        interaction.delete_original_response(),
    ):
        with pytest.raises(discord.NotFound) as error:
            await request
        assert error.value.code == 10015
    assert result.response is None and channel.history() == []


async def test_deferred_update_followup_does_not_consume_source(env, channel, alice):
    class DeferredView(discord.ui.View):
        @discord.ui.button(label="defer", custom_id="defer-update")
        async def defer_update(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await interaction.response.defer()
            source = await interaction.original_response()
            assert source.id == original.id and not source.flags.loading
            followup = await interaction.followup.send("private followup", ephemeral=True, wait=True)
            assert followup.id != source.id and followup.flags.ephemeral

    view = DeferredView()
    original = await env.bot.get_channel(channel.id).send("source", view=view)
    try:
        result = await alice.click(original, custom_id="defer-update")
        assert result.deferred
        assert result.response.id == original.id and result.response.content == "source"
        assert len(result.followups) == 1 and result.followups[0].ephemeral
        assert [item.id for item in channel.history()] == [original.id]
    finally:
        view.stop()


async def test_pending_completion_preserves_native_uploads_and_poll(env, channel, alice):
    admitted = []

    @env.bot.tree.command(name="pendingpayload")
    async def pendingpayload(interaction: discord.Interaction) -> None:
        admitted.append(interaction)
        await interaction.response.defer(thinking=True)

    await env.bot.tree.sync()
    result = await alice.slash(channel, "pendingpayload")
    interaction = admitted[0]
    original_id = result.response.id
    poll = discord.Poll(question="Lunch?", duration=datetime.timedelta(hours=1))
    poll.add_answer(text="Pizza")
    poll.add_answer(text="Sushi")
    completed = await interaction.followup.send(
        "Choose lunch",
        file=discord.File(io.BytesIO(b"menu"), filename="menu.txt"),
        poll=poll,
        wait=True,
    )
    assert completed.id == original_id and not completed.flags.loading
    assert completed.poll.question == "Lunch?"
    assert [item.text for item in completed.poll.answers] == ["Pizza", "Sushi"]
    assert [item.filename for item in completed.attachments] == ["menu.txt"]
    assert await completed.attachments[0].read() == b"menu"
    assert result.response.id == original_id and result.followups == []
