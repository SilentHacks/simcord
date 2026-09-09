import asyncio
import time

from discord.ext import commands

_ORIGINAL_MONOTONIC = time.monotonic


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
    assert time.monotonic is _ORIGINAL_MONOTONIC
    external_fired = asyncio.Event()
    bot_fired = asyncio.Event()
    external = loop.call_later(30, external_fired.set)

    async def bot_timer() -> None:
        await asyncio.sleep(10)
        bot_fired.set()

    with env._bot_scope():
        task = loop.create_task(bot_timer())
    before = env.backend.now_iso()
    try:
        await env.advance_time(10)
        assert bot_fired.is_set()
        assert task.done()
        assert not external_fired.is_set()
        assert not external.cancelled()
        assert env.backend.now_iso() > before
    finally:
        external.cancel()
