import discord
import pytest
from discord.ext import commands

import simcord
from fixtures.sample_bot import create_bot


async def test_ready_and_cache_population():
    intents = discord.Intents.default()
    intents.members = True  # without it discord.py (correctly) won't cache members
    bot = commands.Bot(command_prefix="!", intents=intents)
    async with simcord.run(bot) as env:
        assert bot.user is not None
        assert bot.user.bot
        guild = env.create_guild("My Server")
        channel = guild.create_text_channel("general")
        user = env.create_user("alice")
        guild.add_member(user)

        cached = bot.get_guild(guild.id)
        assert cached is not None and cached.name == "My Server"
        assert bot.get_channel(channel.id) is not None
        assert cached.get_member(user.id) is not None
        assert cached.me is not None  # the bot is a member of its guilds


async def test_persistent_view_survives_restart(env, channel, alice):
    result = await alice.slash(channel, "panel")
    panel = result.response.message

    # A brand-new bot instance, built from the same factory, must re-attach its
    # persistent view (registered in setup_hook) to a message it never created.
    await env.restart_bot(create_bot())

    click = await alice.click(panel, custom_id="persistent:ping")
    assert click.response.content == "pong"


async def test_restart_preserves_world(env, channel, alice):
    await alice.send(channel, "before restart")

    await env.restart_bot(create_bot())

    cached = env.bot.get_guild(env.guild.id)
    assert cached is not None and cached.get_channel(channel.id) is not None
    assert channel.history()[-1].content == "before restart"


async def test_restart_preserves_errors(env, channel, alice):
    # A bug the old bot hit must not be laundered away by a restart: the
    # un-inspected error has to still be there to fail the test at teardown.
    env.inject_error("POST", "/channels/*/messages", status=500, message="boom")
    await alice.send(channel, "!ping")  # the reply fails and is captured
    assert env.errors

    before = list(env.errors)
    await env.restart_bot(create_bot())

    assert env.errors == before


async def test_restart_before_start_is_rejected():
    bot = create_bot()
    env = simcord.Env(bot)
    with pytest.raises(simcord.SetupError, match="not started"):
        await env.restart_bot()


async def test_unknown_route_is_loud(env, channel):
    ch = env.bot.get_channel(channel.id)
    # The signal must not masquerade as a discord.HTTPException, or a bot's
    # broad `except discord.HTTPException` would silently swallow it.
    try:
        await ch.guild.integrations()
    except discord.HTTPException:
        raise AssertionError("RouteNotImplemented must not be a discord.HTTPException") from None
    except simcord.BackendError as exc:
        assert "does not implement" in str(exc)
    else:
        raise AssertionError("expected a loud RouteNotImplemented error")
