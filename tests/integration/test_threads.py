import discord
import pytest

from simcord.backend import errors


async def _make_thread(env, name="discussion"):
    channel = env.guild.create_text_channel("general")
    await env.settle()
    cached = env.bot.get_channel(channel.id)
    thread = await cached.create_thread(name=name, type=discord.ChannelType.public_thread)
    await env.settle()
    return thread


async def test_create_thread_adds_owner_as_member(env):
    thread = await _make_thread(env)
    members = await thread.fetch_members()
    assert [m.id for m in members] == [env.bot.user.id]


async def test_add_and_remove_user(env):
    thread = await _make_thread(env)
    alice = env.guild.add_member(env.create_user("alice"))

    await thread.add_user(discord.Object(id=alice.id))
    await env.settle()
    assert alice.id in {m.id for m in await thread.fetch_members()}

    await thread.remove_user(discord.Object(id=alice.id))
    await env.settle()
    assert alice.id not in {m.id for m in await thread.fetch_members()}


async def test_join_and_leave(env):
    thread = await _make_thread(env)
    await thread.leave()
    await env.settle()
    assert env.bot.user.id not in env.backend.get_channel(thread.id).thread_members

    await thread.join()
    await env.settle()
    assert env.bot.user.id in env.backend.get_channel(thread.id).thread_members


async def test_active_threads(env):
    thread = await _make_thread(env, name="live")
    guild = env.bot.get_guild(env.guild.id)
    active = await guild.active_threads()
    assert thread.id in {t.id for t in active}


async def test_edit_archive_persists(env):
    # Regression: thread.edit(archived=...) previously returned 200 but silently
    # dropped the field. It must round-trip.
    thread = await _make_thread(env)
    await thread.edit(archived=True, locked=True)
    await env.settle()

    meta = env.backend.get_channel(thread.id).thread_metadata
    assert meta.archived is True
    assert meta.locked is True


async def test_archived_threads_listing(env):
    thread = await _make_thread(env)
    await thread.edit(archived=True)
    await env.settle()

    parent = env.bot.get_channel(thread.parent_id)
    found = [t async for t in parent.archived_threads()]
    assert thread.id in {t.id for t in found}


async def test_fetch_single_thread_member(env):
    thread = await _make_thread(env)
    alice = env.guild.add_member(env.create_user("alice"))
    await thread.add_user(discord.Object(id=alice.id))
    await env.settle()

    member = await thread.fetch_member(alice.id)
    assert member.id == alice.id


async def test_fetch_unknown_thread_member_errors(env):
    thread = await _make_thread(env)
    with pytest.raises(discord.NotFound):
        await thread.fetch_member(99999)


async def test_private_and_joined_archived_threads(env):
    channel = env.guild.create_text_channel("priv")
    await env.settle()
    cached = env.bot.get_channel(channel.id)
    thread = await cached.create_thread(name="secret", type=discord.ChannelType.private_thread)
    await thread.edit(archived=True, auto_archive_duration=1440)
    await env.settle()

    parent = env.bot.get_channel(channel.id)
    private = [t async for t in parent.archived_threads(private=True)]
    assert thread.id in {t.id for t in private}

    joined = [t async for t in parent.archived_threads(private=True, joined=True)]
    assert thread.id in {t.id for t in joined}


async def test_leaving_guild_drops_thread_membership(env):
    # A kicked/banned member also leaves every thread, so member_count stays right.
    thread = await _make_thread(env)
    alice = env.guild.add_member(env.create_user("alice"))
    await thread.add_user(discord.Object(id=alice.id))
    await env.settle()
    assert alice.id in env.backend.get_channel(thread.id).thread_members

    await env.bot.get_guild(env.guild.id).kick(discord.Object(id=alice.id))
    await env.settle()
    assert alice.id not in env.backend.get_channel(thread.id).thread_members


async def test_thread_member_ops_reject_non_thread(env):
    # A normal channel is not a thread: the thread-member surface must 50024,
    # not crash on the thread_metadata assertion.
    channel = env.guild.create_text_channel("general")
    await env.settle()
    with pytest.raises(errors.BackendError) as exc:
        env.backend.add_thread_member(channel.id, env.bot.user.id)
    assert exc.value.code == 50024
