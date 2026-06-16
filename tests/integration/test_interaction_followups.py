"""Full interaction follow-up lifecycle: edit/delete of followups and of the
original response, plus deferred-then-edit materialisation. The shared sample
bot only defers+sends, so these routes need a purpose-built bot.
"""

import discord
import pytest
from discord import app_commands
from discord.ext import commands

import simcord


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
