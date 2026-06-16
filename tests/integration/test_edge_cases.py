"""Edge-case branches the happy-path feature tests don't reach: idempotent and
invalid poll votes, overwrite deletion, real role reordering, and the
one-instance-per-stage guard.
"""

import datetime

import discord
import pytest

import simcord


def _make_poll() -> discord.Poll:
    poll = discord.Poll(question="Lunch?", duration=datetime.timedelta(hours=1))
    poll.add_answer(text="Pizza")
    poll.add_answer(text="Sushi")
    return poll


async def test_poll_vote_edges(env, channel, alice):
    ch = env.bot.get_channel(channel.id)
    message = await ch.send(poll=_make_poll())
    await env.settle()

    # Voting the same answer twice is idempotent (no duplicate vote, no second event).
    await alice.vote(message, answer=1)
    await alice.vote(message, answer=1)
    assert env.backend.get_message(channel.id, message.id).poll.votes[1] == {alice.id}

    # Removing a vote that was never cast is a no-op.
    await alice.remove_vote(message, answer=2)

    # An answer id that doesn't exist fails loudly.
    with pytest.raises(simcord.BackendError):
        env.backend.add_poll_vote(channel.id, message.id, 999, alice.id)

    # Removing a vote on a message that has no poll fails loudly.
    plain = await ch.send("no poll here")
    await env.settle()
    with pytest.raises(simcord.BackendError):
        env.backend.remove_poll_vote(channel.id, plain.id, 1, alice.id)


async def test_overwrite_set_then_delete(env, channel):
    guild = env.bot.get_guild(env.guild.id)
    role = await guild.create_role(name="Muted")
    cached = env.bot.get_channel(channel.id)

    await cached.set_permissions(role, send_messages=False)
    await env.settle()
    backend_channel = env.backend.get_channel(channel.id)
    assert any(o.target_id == role.id for o in backend_channel.overwrites)

    await cached.set_permissions(role, overwrite=None)  # delete the overwrite
    await env.settle()
    assert not any(o.target_id == role.id for o in env.backend.get_channel(channel.id).overwrites)


async def test_role_reorder_changes_positions(env):
    guild = env.bot.get_guild(env.guild.id)
    low = await guild.create_role(name="Low")
    high = await guild.create_role(name="High")
    await env.settle()

    low_pos = guild.get_role(low.id).position
    high_pos = guild.get_role(high.id).position
    await guild.edit_role_positions({low: high_pos, high: low_pos})
    await env.settle()

    assert env.backend.get_role(env.guild.id, low.id).position == high_pos
    assert "GUILD_ROLE_UPDATE" in env.transcript()


async def test_second_stage_instance_rejected(env):
    stage = env.guild.create_stage_channel("Stage")
    await env.settle()
    cached = env.bot.get_channel(stage.id)

    await cached.create_instance(topic="First")
    await env.settle()
    with pytest.raises(discord.HTTPException):
        await cached.create_instance(topic="Second")
