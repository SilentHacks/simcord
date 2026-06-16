"""Full interaction follow-up lifecycle: edit/delete of followups and of the
original response, plus deferred-then-edit materialisation. The shared sample
bot only defers+sends, so these routes need a purpose-built bot.
"""

import discord
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
