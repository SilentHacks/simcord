import datetime

import discord


def _make_poll() -> discord.Poll:
    poll = discord.Poll(question="Lunch?", duration=datetime.timedelta(hours=1))
    poll.add_answer(text="Pizza")
    poll.add_answer(text="Sushi")
    return poll


async def test_send_poll_and_vote(env, channel, alice):
    ch = env.bot.get_channel(channel.id)
    message = await ch.send(poll=_make_poll())
    await env.settle()

    stored = env.backend.get_message(channel.id, message.id)
    assert stored.poll is not None
    assert [a.text for a in stored.poll.answers] == ["Pizza", "Sushi"]

    await alice.vote(message, answer=1)
    assert alice.id in env.backend.get_message(channel.id, message.id).poll.votes[1]
    assert "MESSAGE_POLL_VOTE_ADD" in env.transcript()


async def test_single_select_moves_vote(env, channel, alice):
    ch = env.bot.get_channel(channel.id)
    message = await ch.send(poll=_make_poll())
    await env.settle()

    await alice.vote(message, answer=1)
    await alice.vote(message, answer=2)  # single-select: moves the vote
    poll = env.backend.get_message(channel.id, message.id).poll
    assert alice.id not in poll.votes.get(1, set())
    assert alice.id in poll.votes[2]

    await alice.remove_vote(message, answer=2)
    assert alice.id not in env.backend.get_message(channel.id, message.id).poll.votes.get(2, set())


async def test_poll_expires_on_advance_time(env, channel):
    ch = env.bot.get_channel(channel.id)
    message = await ch.send(poll=_make_poll())
    await env.settle()
    assert not env.backend.get_message(channel.id, message.id).poll.finalized

    await env.advance_time(3700)  # just past the 1-hour duration
    assert env.backend.get_message(channel.id, message.id).poll.finalized


async def test_end_poll_route(env, channel):
    ch = env.bot.get_channel(channel.id)
    message = await ch.send(poll=_make_poll())
    await env.settle()

    fetched = await ch.fetch_message(message.id)
    await fetched.end_poll()
    await env.settle()
    assert env.backend.get_message(channel.id, message.id).poll.finalized
