"""Reported public-operation workloads, including fresh Env setup and shutdown.

The command round trip and local I/O beside live Views are complete-journey
controls; callback, view and virtual timer scenarios are stress/scaling evidence.
Every round reconstructs its world and includes operations, assertions and teardown.
These report trends, not machine-specific microsecond budgets. CPU/allocation
attribution remains a separate workflow.
"""

import asyncio

import discord
from discord.ext import commands
from pytest_benchmark.fixture import BenchmarkFixture

import simcord


def test_command_reply_round_trip(benchmark: BenchmarkFixture) -> None:
    async def scenario() -> None:
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

        @bot.command()
        async def ping(ctx: commands.Context) -> None:
            await ctx.reply("Pong!")

        async with simcord.run(bot) as env:
            guild = env.create_guild()
            channel = guild.create_text_channel("benchmark")
            actor = guild.add_member(env.create_user("sender"))
            await actor.send(channel, "!ping")
            history = channel.history()
            assert [message.content for message in history] == ["!ping", "Pong!"]
            assert history[0].author.id == actor.id
            assert history[1].author == bot.user
            assert history[1].reference is not None
            assert history[1].reference.message_id == history[0].id
            assert not env.errors
        assert not env.errors

    benchmark.pedantic(lambda: asyncio.run(scenario()), rounds=10, iterations=1)


def test_native_io_beside_live_views(benchmark: BenchmarkFixture) -> None:
    async def scenario() -> None:
        served = asyncio.Event()

        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while line := await reader.readline():
                    writer.write(line)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                served.set()

        server = await asyncio.start_server(echo, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        class Bot(discord.Client):
            async def on_message(self, message: discord.Message) -> None:
                if message.content != "query":
                    return
                for index in range(6):
                    view = discord.ui.View(timeout=86_400)
                    view.add_item(discord.ui.Button(label="Parked", custom_id=f"parked:{index}"))
                    await message.channel.send(f"panel-{index}", view=view)
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                try:
                    for index in range(20):
                        line = f"query-{index}\n".encode()
                        writer.write(line)
                        await writer.drain()
                        assert await reader.readline() == line
                finally:
                    writer.close()
                    await writer.wait_closed()
                await message.channel.send("20 complete")

        async with server, simcord.run(Bot(intents=discord.Intents.all())) as env:
            guild = env.create_guild()
            channel = guild.create_text_channel("benchmark")
            actor = guild.add_member(env.create_user("sender"))
            await actor.send(channel, "query")
            assert [message.content for message in channel.history()] == (
                ["query"] + [f"panel-{index}" for index in range(6)] + ["20 complete"]
            )
            assert not env.errors
        await asyncio.wait_for(served.wait(), timeout=2)
        assert not env.errors

    benchmark.pedantic(lambda: asyncio.run(scenario()), rounds=5, iterations=1)


def test_callback_burst_beside_parked_timers(benchmark: BenchmarkFixture) -> None:
    async def scenario() -> None:
        observed: list[int] = []

        class Bot(discord.Client):
            async def on_message(self, message: discord.Message) -> None:
                if message.content != "burst":
                    return
                loop = asyncio.get_running_loop()
                for _ in range(1_000):
                    loop.call_later(86_400, observed.append, -1)
                for index in range(1_000):
                    loop.call_soon(observed.append, index)

        async with simcord.run(Bot(intents=discord.Intents.all())) as env:
            guild = env.create_guild()
            channel = guild.create_text_channel("benchmark")
            actor = guild.add_member(env.create_user("sender"))
            await actor.send(channel, "burst")
            assert observed == list(range(1_000))
        assert not env.errors

    benchmark.pedantic(lambda: asyncio.run(scenario()), rounds=5, iterations=1)


def test_idle_view_settlement(benchmark: BenchmarkFixture) -> None:
    async def scenario() -> None:
        class Bot(discord.Client):
            async def on_message(self, message: discord.Message) -> None:
                if message.content != "views":
                    return
                for index in range(25):
                    view = discord.ui.View(timeout=86_400)
                    for button in range(5):
                        view.add_item(discord.ui.Button(label="B", custom_id=f"{index}:{button}"))
                    await message.channel.send("panel", view=view)

        async with simcord.run(Bot(intents=discord.Intents.all())) as env:
            guild = env.create_guild()
            channel = guild.create_text_channel("benchmark")
            actor = guild.add_member(env.create_user("sender"))
            await actor.send(channel, "views")
            for _ in range(10):
                await env.settle()
            assert [message.content for message in channel.history()] == ["views"] + ["panel"] * 25
        assert not env.errors

    benchmark.pedantic(lambda: asyncio.run(scenario()), rounds=5, iterations=1)


def test_staggered_virtual_callbacks(benchmark: BenchmarkFixture) -> None:
    async def scenario() -> None:
        observed: list[int] = []

        class Bot(discord.Client):
            async def on_message(self, message: discord.Message) -> None:
                if message.content != "timers":
                    return
                loop = asyncio.get_running_loop()
                for index in range(250):
                    loop.call_later((index + 1) * 1_000, observed.append, index)

        async with simcord.run(Bot(intents=discord.Intents.all())) as env:
            guild = env.create_guild()
            channel = guild.create_text_channel("benchmark")
            actor = guild.add_member(env.create_user("sender"))
            await actor.send(channel, "timers")
            await env.advance_time(250_000)
            assert observed == list(range(250))
        assert not env.errors

    benchmark.pedantic(lambda: asyncio.run(scenario()), rounds=5, iterations=1)
