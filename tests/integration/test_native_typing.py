"""Native SDK typing intervals are parked, not their HTTP work or parent."""

import asyncio

import discord
import pytest
from discord.http import HTTPClient

import simcord
from fixtures.sample_bot import create_bot


def typing_count(env):
    return sum(entry.path.endswith("/typing") for entry in env.http_requests)


async def test_native_typing_does_not_delay_actor_or_component_settlement():
    bot = create_bot()
    release = asyncio.Event()

    class Controls(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="Ready", custom_id="typing-ready")
        async def ready(self, interaction, button):
            await interaction.response.send_message("ready")

    @bot.listen("on_message")
    async def hold(message):
        if message.content == "hold":
            await message.channel.send("controls", view=Controls())
            async with message.channel.typing():
                await env.external_wait(release.wait(), reason="typing release")

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("typing")
        actor = guild.add_member(env.create_user("alice"))
        try:
            await asyncio.wait_for(actor.send(channel, "hold"), timeout=1)
            controls = channel.last_message
            await asyncio.wait_for(actor.send(channel, "follow-up"), timeout=1)
            result = await asyncio.wait_for(actor.click(controls, custom_id="typing-ready"), timeout=1)
            assert result.response.content == "ready"
            assert typing_count(env) == 1
        finally:
            release.set()
            await env.settle()


@pytest.mark.parametrize("finish", ["exit", "cancel", "restart", "shutdown"])
async def test_native_typing_refreshes_and_retires(finish):
    bot = create_bot()
    releases = {name: asyncio.Event() for name in ("one", "two")}
    parents = {}

    @bot.listen("on_message")
    async def hold(message):
        if message.content not in releases:
            return
        parents[message.content] = asyncio.current_task()
        async with message.channel.typing():
            await env.external_wait(releases[message.content].wait(), reason="typing release")
        await message.channel.send("finished " + message.content)

    async with simcord.run(bot, settle_timeout=0.2) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("typing")
        actor = guild.add_member(env.create_user("alice"))
        await actor.send(channel, "one")
        await actor.send(channel, "two")
        assert typing_count(env) == 2
        await env.advance_time(15)
        assert typing_count(env) == 8  # Three actual refreshes for each context.
        if finish == "exit":
            releases["one"].set()
            await env.settle()
            assert channel.last_message.content == "finished one"
            await env.advance_time(5)
            assert typing_count(env) == 9
            releases["two"].set()
            await env.settle()
        elif finish == "cancel":
            for parent in parents.values():
                parent.cancel()
            await env.settle()
        elif finish == "restart":
            await env.restart_bot(create_bot())
        else:
            await env.shutdown()
        before = typing_count(env)
        if finish != "shutdown":
            await env.advance_time(15)
        assert typing_count(env) == before


@pytest.mark.parametrize("message", ["unknown parent", "lookalike"])
async def test_native_typing_does_not_declare_parent_or_lookalike_waits(message):
    bot = create_bot()
    background = []

    async def do_typing():
        await asyncio.Event().wait()

    @bot.listen("on_message")
    async def hold(message):
        if message.content == "unknown parent":
            async with message.channel.typing():
                await asyncio.Event().wait()
        elif message.content == "lookalike":
            background.append(asyncio.create_task(do_typing()))

    async with simcord.run(bot, settle_timeout=0.03) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("typing")
        actor = guild.add_member(env.create_user("alice"))
        with pytest.raises(TimeoutError, match="unknown wait"):
            await actor.send(channel, message)


async def test_native_typing_refresh_http_wait_is_not_an_interval(monkeypatch):
    original = HTTPClient.send_typing
    calls = 0
    release = asyncio.Event()
    parent_release = asyncio.Event()

    async def send_typing(http, channel_id):
        nonlocal calls
        calls += 1
        if calls > 1:
            await release.wait()
        return await original(http, channel_id)

    monkeypatch.setattr(HTTPClient, "send_typing", send_typing)
    bot = create_bot()

    @bot.listen("on_message")
    async def hold(message):
        if message.content == "hold":
            async with message.channel.typing():
                await env.external_wait(parent_release.wait(), reason="typing release")

    async with simcord.run(bot, settle_timeout=0.03) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("typing")
        actor = guild.add_member(env.create_user("alice"))
        await actor.send(channel, "hold")
        with pytest.raises(TimeoutError, match="unknown wait"):
            await env.advance_time(5)
        assert calls == 2
        assert typing_count(env) == 1
        release.set()
        await env.settle()
        assert typing_count(env) == 2
        parent_release.set()
        await env.settle()


async def test_driver_native_typing_task_stays_on_real_clock(env, channel):
    cached = env.bot.get_channel(channel.id)
    async with cached.typing():
        await asyncio.sleep(0)  # Let the foreign SDK task install its real sleep.
        before = typing_count(env)
        await env.advance_time(15)
        assert typing_count(env) == before
    await asyncio.sleep(0)


async def test_typing_http_retry_sleep_keeps_its_real_fallback(monkeypatch):
    original = HTTPClient.send_typing
    calls = 0
    release = asyncio.Event()

    async def send_typing(http, channel_id):
        nonlocal calls
        calls += 1
        if calls > 1:
            await asyncio.sleep(0.01)
        return await original(http, channel_id)

    monkeypatch.setattr(HTTPClient, "send_typing", send_typing)
    bot = create_bot()

    @bot.listen("on_message")
    async def hold(message):
        if message.content == "retry":
            async with message.channel.typing():
                await env.external_wait(release.wait(), reason="typing release")

    async with simcord.run(bot, settle_timeout=0.5) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("typing")
        actor = guild.add_member(env.create_user("alice"))
        await actor.send(channel, "retry")
        await env.advance_time(5)
        await env.settle()
        assert typing_count(env) == 2
        release.set()
        await env.settle()
