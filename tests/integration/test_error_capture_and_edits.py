"""Error capture from every bot surface (tree, prefix command, plain listener)
and message-edit field clearing — paths the standard fixtures don't trip.
"""

import discord
from discord.ext import commands

import simcord


def _failing_bot() -> commands.Bot:
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.tree.command(name="boom", description="raise from a slash command")
    async def boom(interaction: discord.Interaction) -> None:
        raise RuntimeError("tree boom")

    @bot.command(name="prefixboom")
    async def prefixboom(ctx: commands.Context) -> None:
        raise RuntimeError("prefix boom")

    @bot.listen("on_member_join")
    async def on_join(member: discord.Member) -> None:
        raise RuntimeError("listener boom")

    return bot


async def test_env_captures_errors_from_all_surfaces():
    async with simcord.run(_failing_bot(), strict_sync=False) as env:
        env.create_guild()
        ch = env.guild.create_text_channel("general")
        alice = env.guild.add_member(env.create_user("alice"))

        await alice.slash(ch, "boom")
        simcord.assert_error(env, RuntimeError, contains="tree boom")

        await alice.send(ch, "!prefixboom")
        simcord.assert_error(env, RuntimeError, contains="prefix boom")

        env.guild.add_member(env.create_user("bob"))  # fires on_member_join
        await env.settle()
        simcord.assert_error(env, RuntimeError, contains="listener boom")


async def test_message_edit_clears_embeds(env, channel):
    ch = env.bot.get_channel(channel.id)
    message = await ch.send(content="with embed", embed=discord.Embed(title="hi"))
    await env.settle()

    await message.edit(content="now plain", embeds=[])
    await env.settle()

    backend_message = env.backend.get_message(channel.id, message.id)
    assert backend_message.content == "now plain"
    assert backend_message.embeds == []
