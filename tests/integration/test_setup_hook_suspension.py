"""setup_hook may suspend on timers and sockets — e.g. opening a DB pool.

Regression tests for the 2.0.0 crash where ``Env.start()`` raised
``ValueError: ... was created in a different Context``: task resumption
callbacks were scheduled under a copied ``Context``, so a suspended
``setup_hook`` resumed in a different ``Context`` object and the
``_bot_scope`` ``ContextVar.reset`` became invalid.
"""

import asyncio
import contextvars

import pytest
from discord.ext import commands

import simcord
from fixtures.sample_bot import create_bot


def _suspending_bot(suspend) -> commands.Bot:
    """A sample bot whose setup_hook suspends via ``suspend`` first."""
    bot = create_bot()
    original_setup = bot.setup_hook

    async def setup_hook() -> None:
        await suspend()
        await original_setup()

    bot.setup_hook = setup_hook
    return bot


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [0, 0.01])
async def test_setup_hook_may_suspend_on_timers(delay: float):
    bot = _suspending_bot(lambda: asyncio.sleep(delay))
    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "!ping")
        assert channel.last_message is not None
        assert channel.last_message.content == "Pong!"


@pytest.mark.asyncio
async def test_setup_hook_may_suspend_on_a_socket():
    received: list[bytes] = []

    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        received.append(await reader.read(4))
        writer.write(b"pong")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]

        async def open_pool_like() -> None:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"ping")
            await writer.drain()
            assert await reader.read(4) == b"pong"
            writer.close()
            await writer.wait_closed()

        bot = _suspending_bot(open_pool_like)
        async with simcord.run(bot) as env:
            env.create_guild()
    finally:
        server.close()
        await server.wait_closed()
    assert received == [b"ping"]


@pytest.mark.asyncio
async def test_user_contextvar_survives_setup_hook_suspension():
    """A user ContextVar behaves as if setup_hook never left its own Context:
    a value set before an await is visible after it, and a value set after an
    await survives the next suspension (a copied resumption context would lose it)."""
    var: contextvars.ContextVar[str] = contextvars.ContextVar("simcord_test_var")
    observations: list[str] = []
    bot = create_bot()
    original_setup = bot.setup_hook

    async def setup_hook() -> None:
        var.set("before-await")
        await asyncio.sleep(0.01)
        observations.append(var.get())
        var.set("after-await")
        await asyncio.sleep(0.01)
        observations.append(var.get())
        await original_setup()

    bot.setup_hook = setup_hook
    async with simcord.run(bot):
        pass
    assert observations == ["before-await", "after-await"]


@pytest.mark.asyncio
async def test_restart_bot_with_suspending_setup_hook(env, channel, alice):
    await env.restart_bot(_suspending_bot(lambda: asyncio.sleep(0.01)))
    await alice.send(channel, "!ping")
    assert channel.last_message is not None
    assert channel.last_message.content == "Pong!"
