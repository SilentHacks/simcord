import asyncio
import time

import discord
import pytest
from discord.ext import commands

import simcord
from fixtures.sample_bot import create_bot


async def test_view_timeout_fast_forward(env, channel, alice):
    result = await alice.slash(channel, "offer")
    assert result.response.content == "Claim within 3 minutes!"

    start = time.perf_counter()
    await env.advance_time(180)
    assert time.perf_counter() - start < 2, "advance_time must not wait in real time"

    refetched = await env.bot.get_channel(channel.id).fetch_message(result.response.id)
    assert refetched.content == "Offer expired."
    assert refetched.components == []


async def test_view_survives_partial_advance(env, channel, alice):
    result = await alice.slash(channel, "offer")
    await env.advance_time(100)  # not enough to expire
    refetched = await env.bot.get_channel(channel.id).fetch_message(result.response.id)
    assert refetched.content == "Claim within 3 minutes!"
    await env.advance_time(100)  # cumulative 200s > 180s timeout
    refetched = await env.bot.get_channel(channel.id).fetch_message(result.response.id)
    assert refetched.content == "Offer expired."


async def test_cooldown_reset_fast_forward(env, channel, alice):
    await alice.send(channel, "!daily")
    assert channel.last_message.content == "Claimed!"

    await alice.send(channel, "!daily")  # still on cooldown
    assert isinstance(env.errors[-1], commands.CommandOnCooldown)

    await env.advance_time(60)
    await alice.send(channel, "!daily")
    assert channel.last_message.content == "Claimed!"


async def test_external_timers_stay_on_real_clock(env):
    loop = asyncio.get_running_loop()
    real_start = time.monotonic()
    external_fired = asyncio.Event()
    bot_fired = asyncio.Event()
    external = loop.call_later(30, external_fired.set)

    async def bot_timer() -> None:
        await asyncio.sleep(10)
        bot_fired.set()

    with env._bot_scope():
        task = loop.create_task(bot_timer())
    before = env.backend.now_iso()
    with env._bot_scope():
        virtual_start = time.monotonic()
    try:
        await env.advance_time(10)
        assert bot_fired.is_set()
        assert task.done()
        assert not external_fired.is_set()
        assert not external.cancelled()
        assert env.backend.now_iso() > before
        assert time.monotonic() - real_start < 2
        with env._bot_scope():
            assert time.monotonic() - virtual_start == 10
    finally:
        external.cancel()


async def test_bot_timer_real_fallback_uses_remaining_delay(env):
    await env.advance_time(60)
    fired = asyncio.Event()

    async def bot_timer() -> None:
        await asyncio.sleep(0.01)
        fired.set()

    with env._bot_scope():
        task = asyncio.create_task(bot_timer())
    await asyncio.wait_for(fired.wait(), 1)
    await task


async def test_settle_joins_short_bot_sleep_after_advance(env):
    """A short sleep scheduled while virtual time is ahead must be joined, not
    parked: its real fallback fires well within the settlement deadline even
    though the virtual deadline looks far away."""
    await env.advance_time(60)
    fired = asyncio.Event()

    async def bot_timer() -> None:
        await asyncio.sleep(0.05)
        fired.set()

    with env._bot_scope():
        task = asyncio.create_task(bot_timer())
    await env.settle(timeout=1)
    assert fired.is_set()
    await task


async def test_view_timeout_expires_on_virtual_clock_only():
    """A registered View timeout is a parked wait: the real clock never fires
    it — only advance_time() does, exactly once."""
    bot = create_bot()
    timed_out = asyncio.Event()
    timeouts = 0

    class QuickView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=0.05)

        async def on_timeout(self) -> None:
            nonlocal timeouts
            timeouts += 1
            timed_out.set()

        # A dispatchable item registers the view in the store — only
        # registered views get a recognized (virtual-only) expiry timer.
        @discord.ui.button(label="Quick", custom_id="quick")
        async def quick(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
            await interaction.response.send_message("clicked")

    @bot.tree.command(name="quick-view")
    async def quick_view(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("quick", view=QuickView())

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.slash(channel, "quick-view")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(timed_out.wait(), 0.3)
        await env.advance_time(0.05)
        assert timed_out.is_set()
        assert timeouts == 1


async def test_zero_delay_timer_chain_respects_settle_deadline():
    """A call_later(0) self-chain drains in bounded batches per loop turn, so
    the settlement deadline still fires instead of being monopolized."""
    bot = create_bot()
    ran = 0

    @bot.listen("on_message")
    async def chain(message: discord.Message) -> None:
        if message.content != "chain":
            return
        loop = asyncio.get_running_loop()

        def step() -> None:
            nonlocal ran
            ran += 1
            if ran < 100_000:  # bounded, but far beyond the settle timeout
                loop.call_later(0, step)

        loop.call_later(0, step)

    async with simcord.run(bot, settle_timeout=0.01) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        with pytest.raises(asyncio.TimeoutError):
            await alice.send(channel, "chain")


async def test_second_environment_rejected_before_mutating_first():
    env1 = simcord.Env(create_bot())
    env2 = simcord.Env(create_bot())
    await env1.start()
    loop = asyncio.get_running_loop()
    hooks = (loop.create_task, loop.call_later, time.monotonic)
    try:
        with pytest.raises(simcord.SetupError, match="one live Env"):
            await env2.start()
        assert (loop.create_task, loop.call_later, time.monotonic) == hooks
        guild = env1.create_guild()
        await env1.settle()
        assert env1.bot.get_guild(guild.id) is not None
    finally:
        await env1.shutdown()


async def test_zero_delay_real_fallback_keeps_the_next_timer_live():
    bot = create_bot()
    observed = []

    @bot.listen("on_message")
    async def schedule(message):
        if message.content != "two callbacks":
            return
        loop = asyncio.get_running_loop()

        def first():
            observed.append("first")
            loop.call_later(0, observed.append, "second")

        loop.call_later(0, first)

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        actor = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await actor.send(channel, "two callbacks")
        assert observed == ["first", "second"]
