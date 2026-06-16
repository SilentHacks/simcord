"""Message-route coverage: history pagination, cross-author delete, unpin, and
poll answer/expire endpoints — the paths the actor-level helpers don't reach.
"""

import datetime

import discord
import pytest

import simcord
from simcord.http import router


async def test_history_before_after_around(env, channel, alice):
    sent = [await alice.send(channel, f"m{i}") for i in range(5)]
    await env.settle()
    ch = env.bot.get_channel(channel.id)

    before = {m.id async for m in ch.history(limit=10, before=discord.Object(sent[3].id))}
    assert before == {sent[0].id, sent[1].id, sent[2].id}

    after = {m.id async for m in ch.history(limit=10, after=discord.Object(sent[1].id))}
    assert after == {sent[2].id, sent[3].id, sent[4].id}

    around = {m.id async for m in ch.history(limit=3, around=discord.Object(sent[2].id))}
    assert sent[2].id in around
    assert len(around) == 3


async def test_bot_deletes_other_users_message(env, channel, alice):
    message = await alice.send(channel, "delete me")
    await env.settle()

    cached = await env.bot.get_channel(channel.id).fetch_message(message.id)
    await cached.delete()  # not the bot's message → manage_messages path
    await env.settle()

    assert channel.history() == []


async def test_pin_then_unpin(env, channel):
    ch = env.bot.get_channel(channel.id)
    message = await ch.send("pin me")
    await env.settle()

    await message.pin()
    await env.settle()
    assert env.backend.get_message(channel.id, message.id).pinned is True

    await message.unpin()
    await env.settle()
    assert env.backend.get_message(channel.id, message.id).pinned is False


def _make_poll() -> discord.Poll:
    poll = discord.Poll(question="Lunch?", duration=datetime.timedelta(hours=1))
    poll.add_answer(text="Pizza")
    poll.add_answer(text="Sushi")
    return poll


async def test_poll_answer_voters_and_expire(env, channel, alice):
    ch = env.bot.get_channel(channel.id)
    message = await ch.send(poll=_make_poll())
    await env.settle()

    await alice.vote(message, answer=1)
    await env.settle()

    # List the voters for the first answer (GET polls/{id}/answers/{answer_id}).
    refetched = await ch.fetch_message(message.id)
    answer = refetched.poll.get_answer(1)
    voter_ids = {user.id async for user in answer.voters()}
    assert alice.id in voter_ids

    # The bot ends its own poll (POST polls/{id}/expire).
    await refetched.end_poll()
    await env.settle()
    assert env.backend.get_message(channel.id, message.id).poll.finalized is True


async def test_expire_poll_of_other_author_rejected(env, channel, alice):
    ch = env.bot.get_channel(channel.id)
    message = await ch.send(poll=_make_poll())
    await env.settle()

    # Make the poll look authored by someone else: ending it must fail loudly.
    env.backend.get_message(channel.id, message.id).author_id = alice.id
    with pytest.raises(simcord.BackendError):
        router.dispatch(
            env.backend,
            "POST",
            f"/channels/{channel.id}/polls/{message.id}/expire",
        )
