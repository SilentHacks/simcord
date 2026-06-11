import discord
from discord.ext import commands


async def test_inject_error(env, channel, alice):
    env.inject_error("POST", "/channels/*/messages", status=500, message="boom")

    await alice.send(channel, "!ping")

    assert env.errors
    error = env.errors[-1]
    assert isinstance(error, commands.CommandInvokeError)
    assert isinstance(error.original, discord.HTTPException)
    assert channel.history() != []  # alice's message went through; only the reply failed

    # The fault was one-shot: the next command works.
    await alice.send(channel, "!ping")
    assert channel.last_message.content == "Pong!"


async def test_http_log_records_bot_calls(env, channel, alice):
    await alice.send(channel, "!ping")
    sends = [(m, p) for (m, p, _body) in env.http_log if m == "POST" and p.endswith("/messages")]
    assert sends, env.http_log


async def test_embed_limits_enforced(env, channel):
    cached = env.bot.get_channel(channel.id)
    import pytest

    embed = discord.Embed(title="x" * 256, description="y" * 4096)
    embed.add_field(name="a" * 256, value="b" * 1024)
    embed.add_field(name="c" * 256, value="d" * 1024)
    with pytest.raises(discord.HTTPException) as exc_info:
        await cached.send(embed=embed)
    assert exc_info.value.code == 50035
