import discord
import pytest
from discord.ext import commands

import discord_py_test as dpt


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


async def test_raise_errors_groups_captured_errors(env, channel, alice):
    env.inject_error("POST", "/channels/*/messages", status=500, message="boom")
    await alice.send(channel, "!ping")  # the bot's reply fails and is captured
    assert env.errors

    with pytest.raises(ExceptionGroup) as exc_info:
        env.raise_errors()
    assert len(exc_info.value.exceptions) == len(env.errors)


async def test_raise_errors_is_a_noop_when_clean(env, channel, alice):
    await alice.send(channel, "!ping")
    env.raise_errors()  # nothing captured → does not raise


async def test_unimplemented_route_attaches_parity_note(env, channel):
    ch = env.bot.get_channel(channel.id)
    with pytest.raises(dpt.BackendError) as exc_info:
        await ch.create_invite()
    notes = getattr(exc_info.value, "__notes__", [])
    assert any("parity matrix" in note for note in notes)


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
