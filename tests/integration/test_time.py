import time

from discord.ext import commands


async def test_view_timeout_fast_forward(env, channel, alice):
    result = await alice.slash(channel, "offer")
    assert result.response.content == "Claim within 3 minutes!"

    start = time.perf_counter()  # not time.monotonic — that's the patched virtual clock
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
