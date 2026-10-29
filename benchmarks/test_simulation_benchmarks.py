"""Reported public-operation workloads, including Env setup/teardown.

These complement bare dispatch timings. They report trends rather than assert
machine-specific microsecond budgets; behavioral regression coverage lives in tests.
"""

import asyncio

import discord
from pytest_benchmark.fixture import BenchmarkFixture

import simcord


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
            assert len(channel.history()) == 26

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

    benchmark.pedantic(lambda: asyncio.run(scenario()), rounds=5, iterations=1)
