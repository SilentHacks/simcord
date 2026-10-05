"""Full interaction follow-up lifecycle: edit/delete of followups and of the
original response, plus deferred-then-edit materialisation. The shared sample
bot only defers+sends, so these routes need a purpose-built bot.
"""

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
        await interaction.response.defer()
        msg = await interaction.followup.send("first", wait=True)
        await msg.edit(content="edited-followup")
        fetched = await interaction.followup.fetch_message(msg.id)
        assert fetched.content == "edited-followup"
        await msg.delete()

    @bot.tree.command(name="deferedit", description="defer then edit the original in")
    async def deferedit(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.edit_original_response(content="materialised")

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
        # The followup was sent, edited, then deleted — nothing lingers.
        assert result.followups == []
        assert ch.history() == []


async def test_deferred_original_is_materialised_on_edit():
    bot = _make_bot()
    async with simcord.run(bot, strict_sync=False) as env:
        env.create_guild()
        ch = env.guild.create_text_channel("general")
        alice = env.guild.add_member(env.create_user("alice"))

        result = await alice.slash(ch, "deferedit")
        assert result.response is not None
        assert result.response.content == "materialised"


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
            assert error.value.code == 10015
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
