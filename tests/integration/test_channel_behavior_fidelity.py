import discord
import pytest

import simcord
from simcord.backend.models import Overwrite
from simcord.enums import OverwriteType


def _deny(env, parent_id, *names):
    deny = discord.Permissions(**dict.fromkeys(names, True)).value
    env.backend.set_overwrite(
        parent_id,
        Overwrite(target_id=env.backend.bot_user.id, type=OverwriteType.MEMBER, deny=deny),
    )


async def _thread(env):
    parent = env.guild.create_text_channel("threads")
    await env.settle()
    cached = env.bot.get_channel(parent.id)
    thread = await cached.create_thread(name="discussion", type=discord.ChannelType.public_thread)
    return parent, thread


async def test_thread_owner_management_and_other_creator_denial(env):
    parent, thread = await _thread(env)
    _deny(env, parent.id, "manage_channels", "manage_threads")
    renamed = await thread.edit(name="renamed", archived=True, locked=True)
    assert renamed.name == "renamed"
    assert renamed.archived and renamed.locked
    with pytest.raises(discord.Forbidden) as caught:
        await renamed.edit(archived=False)
    assert caught.value.code == 160005
    # Live Discord requires manage_threads even for the creator.
    with pytest.raises(discord.Forbidden) as caught:
        await renamed.delete()
    assert caught.value.code == 50013
    env.backend.delete_overwrite(parent.id, env.backend.bot_user.id)
    await renamed.delete()
    assert thread.id not in env.backend.channels
    _deny(env, parent.id, "manage_channels", "manage_threads")

    other = env.guild.add_member(env.create_user("creator"))
    stored = env.backend.create_thread(parent.id, "other", other.id)
    await env.settle()
    cached = env.bot.get_channel(stored.id)
    for operation, code in ((lambda: cached.edit(name="forbidden"), 50001), (cached.delete, 50013)):
        with pytest.raises(discord.Forbidden) as caught:
            await operation()
        assert caught.value.code == code
    assert stored.name == "other"


async def test_thread_edit_requires_send_but_leave_needs_no_view(env):
    parent, thread = await _thread(env)
    _deny(env, parent.id, "send_messages_in_threads")
    with pytest.raises(discord.Forbidden) as caught:
        await thread.edit(name="forbidden")
    assert caught.value.code == 50001
    assert env.backend.get_channel(thread.id).name == "discussion"
    _deny(env, parent.id, "view_channel")
    await thread.leave()
    assert env.backend.bot_user.id not in env.backend.get_channel(thread.id).thread_members


async def test_thread_empty_name_edit_retains_name_and_limits_are_atomic(env):
    _, thread = await _thread(env)
    edited = await thread.edit(name="")
    assert edited.name == "discussion"
    with pytest.raises(discord.HTTPException) as caught:
        await thread.edit(name="n" * 101, archived=True)
    assert caught.value.code == 50035
    assert "Must be 100 or fewer in length." in caught.value.text
    stored = env.backend.get_channel(thread.id)
    assert stored.name == "discussion"
    assert not stored.thread_metadata.archived


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("topic", "t" * 1025, "Must be 1024 or fewer in length."),
        ("slowmode_delay", -1, "int value should be greater than or equal to 0."),
        ("slowmode_delay", 21601, "int value should be less than or equal to 21600."),
    ],
)
async def test_channel_limits_do_not_partially_apply(env, field, value, message):
    parent = env.guild.create_text_channel("before")
    await env.settle()
    cached = env.bot.get_channel(parent.id)
    with pytest.raises(discord.HTTPException) as caught:
        await cached.edit(name="after", **{field: value})
    assert caught.value.code == 50035
    assert message in caught.value.text
    assert env.backend.get_channel(parent.id).name == "before"


async def test_thread_slowmode_round_trips_on_creation(env):
    parent = env.guild.create_text_channel("threads")
    await env.settle()
    cached = env.bot.get_channel(parent.id)
    thread = await cached.create_thread(
        name="slow", type=discord.ChannelType.public_thread, slowmode_delay=21600
    )
    assert thread.slowmode_delay == 21600
    with pytest.raises(discord.HTTPException) as caught:
        await cached.create_thread(name="invalid", slowmode_delay=21601)
    assert caught.value.code == 50035
    assert all(channel.name != "invalid" for channel in env.backend.channels.values())


async def test_forum_require_tag_flags_and_atomic_post_validation(env):
    parent = env.guild.create_forum_channel("forum")
    await env.settle()
    cached = env.bot.get_channel(parent.id)
    tag = discord.ForumTag(name="required")
    cached = await cached.edit(available_tags=[tag], require_tag=True)
    assert cached.flags.require_tag
    before = set(env.backend.channels)
    with pytest.raises(discord.HTTPException) as caught:
        await cached.create_thread(name="missing tag", content="starter")
    assert caught.value.code == 40067
    assert set(env.backend.channels) == before
    post = await cached.create_thread(name="tagged", content="starter", applied_tags=cached.available_tags)
    assert post.thread.applied_tags[0].id == cached.available_tags[0].id


async def test_voice_typing_requires_connect_but_channel_edit_does_not(env):
    parent = env.guild.create_voice_channel("voice")
    await env.settle()
    cached = env.bot.get_channel(parent.id)
    _deny(env, parent.id, "connect")
    with pytest.raises(discord.Forbidden) as caught:
        await cached.typing()
    assert caught.value.code == 50001
    edited = await cached.edit(name="renamed")
    assert edited.name == "renamed"


async def test_forum_consumer_create_validates_topic_and_assigns_tags(env):
    guild = env.bot.get_guild(env.guild.id)
    forum = await guild.create_forum(
        "created", topic="t" * 4096, available_tags=[discord.ForumTag(name="tag")]
    )
    assert forum.topic == "t" * 4096
    assert forum.available_tags[0].id
    before = set(env.backend.channels)
    with pytest.raises(discord.HTTPException) as caught:
        await guild.create_forum("invalid", topic="t" * 4097)
    assert caught.value.code == 50035
    assert "Must be 4096 or fewer in length." in caught.value.text
    assert set(env.backend.channels) == before


async def test_actor_send_locked_thread_guard_is_scoped_to_sending(env, alice):
    parent, thread = await _thread(env)
    stored = env.backend.get_channel(thread.id)
    handle = simcord.ChannelHandle(env, env.guild, stored)
    seed = await alice.send(handle, "before locking")
    await thread.edit(locked=True)
    env.backend.set_overwrite(
        parent.id,
        Overwrite(
            target_id=alice.id,
            type=OverwriteType.MEMBER,
            deny=discord.Permissions.manage_threads.flag,
        ),
    )
    with pytest.raises(simcord.BackendError) as caught:
        await alice.send(handle, "denied")
    assert caught.value.code == 50001
    # User-actor reactions retain their pre-existing semantics.
    await alice.react(seed, "🔥")


async def test_manager_can_unarchive_locked_thread_without_unlocking(env):
    parent, thread = await _thread(env)
    await thread.edit(archived=True, locked=True)
    _deny(env, parent.id, "manage_channels")
    edited = await thread.edit(archived=False)
    assert not edited.archived
    assert edited.locked
    assert not env.backend.get_channel(thread.id).thread_metadata.archived
