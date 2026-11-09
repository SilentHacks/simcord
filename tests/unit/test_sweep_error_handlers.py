"""Real SDK error handlers consume routed errors without hiding diagnostics."""

from __future__ import annotations

import pytest

BOT = """
import sys

import discord
import pytest
from discord.ext import commands


@pytest.fixture
def simcord_bot():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
    bot.consumed = []

    async def consume(error, interaction=None):
        if MODE == "reraised":
            raise error
        bot.consumed.append(error)
        if interaction is not None:
            await interaction.response.send_message("handled", ephemeral=True)

    if ROUTE == "event":
        @bot.event
        async def on_message(message):
            if not message.author.bot:
                await message.channel.send("probe")

        if MODE != "default":
            @bot.event
            async def on_error(event, *args, **kwargs):
                await consume(sys.exc_info()[1])

    elif ROUTE.startswith("prefix"):
        @bot.command()
        async def probe(ctx):
            await ctx.send("probe")

        if MODE != "default":
            if ROUTE == "prefix-local":
                @probe.error
                async def prefix_error(ctx, error):
                    await consume(error)
            else:
                @bot.event
                async def on_command_error(ctx, error):
                    await consume(error)

    elif ROUTE.startswith("app"):
        @bot.tree.command()
        async def probe(interaction: discord.Interaction):
            await interaction.channel.send("probe")
            await interaction.response.send_message("done", ephemeral=True)

        if MODE != "default":
            if ROUTE == "app-local":
                @probe.error
                async def app_error(interaction, error):
                    await consume(error, interaction)
            else:
                @bot.tree.error
                async def tree_error(interaction, error):
                    await consume(error, interaction)

    elif ROUTE == "view":
        class Panel(discord.ui.View):
            @discord.ui.button(label="Probe", custom_id="probe")
            async def probe(self, interaction, button):
                await interaction.channel.send("probe")
                await interaction.response.send_message("done", ephemeral=True)

            if MODE != "default":
                async def on_error(self, interaction, error, item):
                    await consume(error, interaction)

        @bot.tree.command()
        async def probe(interaction: discord.Interaction):
            # Interaction responses do not need the permission under test.
            await interaction.response.send_message("panel", view=Panel())

    else:
        class Form(discord.ui.Modal, title="Probe"):
            value = discord.ui.TextInput(label="Value", custom_id="value")

            async def on_submit(self, interaction):
                await interaction.channel.send("probe")
                await interaction.response.send_message("done", ephemeral=True)

            if MODE != "default":
                async def on_error(self, interaction, error):
                    await consume(error, interaction)

        @bot.tree.command()
        async def probe(interaction: discord.Interaction):
            await interaction.response.send_modal(Form())

    return bot
"""

DRIVER = """
import simcord


async def test_probe(simcord_bot):
    async with simcord.run(simcord_bot, check_errors=False) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(env.create_user("alice"))
        # Reattaching the same client must replace observers, not nest them.
        await env.restart_bot()
        await simcord_bot.tree.sync()
        if ROUTE == "event":
            await alice.send(channel, "probe")
        elif ROUTE.startswith("prefix"):
            await alice.send(channel, "!probe")
        elif ROUTE.startswith("app"):
            await alice.slash(channel, "probe")
        elif ROUTE == "view":
            shown = await alice.slash(channel, "probe")
            await alice.click(shown.response.message, custom_id="probe")
        else:
            shown = await alice.slash(channel, "probe")
            await alice.submit_modal(shown, {"value": "hello"})

        # Routed errors remain available through the public diagnostics API.
        errors = env.errors
        if MODE == "handled" and simcord_bot.consumed:
            assert all(any(error is captured for captured in errors)
                       for error in simcord_bot.consumed)
        native = simcord_bot.get_channel(channel.id)
        if not native.permissions_for(native.guild.me).send_messages:
            assert errors
            if MODE == "handled":
                assert simcord_bot.consumed
"""


@pytest.mark.parametrize(
    "route",
    ["event", "prefix-local", "prefix-global", "app-local", "app-global", "view", "modal"],
)
@pytest.mark.parametrize("mode", ["handled", "default", "reraised"])
def test_sweep_distinguishes_real_handlers_from_default_logging(pytester, route, mode):
    settings = f"ROUTE = {route!r}\nMODE = {mode!r}\n"
    pytester.makeconftest(settings + BOT)
    pytester.makepyfile(settings + DRIVER)
    result = pytester.runpytest_subprocess("-o", "asyncio_mode=auto", "--simcord-sweep")
    result.assert_outcomes(passed=1)
    if mode == "handled":
        assert result.ret == pytest.ExitCode.OK
        assert "0 unhandled bot error(s)" in result.stdout.str()
        assert "source: bot callback/task" not in result.stdout.str()
    else:
        assert result.ret == pytest.ExitCode.TESTS_FAILED
        result.stdout.fnmatch_lines(["*[[]send_messages]: unhandled Forbidden*source: bot callback/task*"])
