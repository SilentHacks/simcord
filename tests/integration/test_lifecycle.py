import discord
from discord.ext import commands

import discord_py_test as dpt


async def test_ready_and_cache_population():
    intents = discord.Intents.default()
    intents.members = True  # without it discord.py (correctly) won't cache members
    bot = commands.Bot(command_prefix="!", intents=intents)
    async with dpt.run(bot) as env:
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


async def test_unknown_route_is_loud(env, channel):
    ch = env.bot.get_channel(channel.id)
    # The signal must not masquerade as a discord.HTTPException, or a bot's
    # broad `except discord.HTTPException` would silently swallow it.
    try:
        await ch.create_invite()
    except discord.HTTPException:
        raise AssertionError("RouteNotImplemented must not be a discord.HTTPException") from None
    except dpt.BackendError as exc:
        assert "does not implement" in str(exc)
    else:
        raise AssertionError("expected a loud RouteNotImplemented error")
