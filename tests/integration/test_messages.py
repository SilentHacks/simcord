import discord
import pytest
from discord.ext import commands

import simcord
from simcord.enums import MessageType


async def test_prefix_command_round_trip(env, channel, alice):
    await alice.send(channel, "!ping")

    reply = channel.last_message
    assert reply.author == env.bot.user
    assert reply.content == "Pong!"


async def test_member_converter(env, channel, alice):
    await alice.send(channel, f"!whois {alice.mention}")
    assert channel.last_message.content == "That is alice"


async def test_bot_cannot_send_without_permission(env, alice):
    everyone = env.guild.default_role
    env.guild.create_text_channel(
        "locked", overwrites={everyone: discord.PermissionOverwrite(send_messages=False)}
    )
    general = env.guild.create_text_channel("general")

    await alice.send(general, "!announce-to-locked hello")

    assert env.errors, "the bot's Forbidden error should have been captured"
    error = env.errors[-1]
    assert isinstance(error, commands.CommandInvokeError)
    assert isinstance(error.original, discord.Forbidden)
    assert error.original.code == 50013


async def test_user_cannot_speak_where_not_allowed(env, alice):
    hidden = env.guild.create_text_channel(
        "hidden", overwrites={env.guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    )
    with pytest.raises(simcord.BackendError):
        await alice.send(hidden, "sneaky")


async def test_message_edit_and_delete(env, channel):
    ch = env.bot.get_channel(channel.id)
    message = await ch.send("v1")
    edited = await message.edit(content="v2")
    assert edited.content == "v2"
    await edited.delete()
    assert channel.history() == []


async def test_content_limit_enforced(env, channel):
    ch = env.bot.get_channel(channel.id)
    with pytest.raises(discord.HTTPException) as exc_info:
        await ch.send("x" * 2001)
    assert exc_info.value.code == 50035


async def test_user_edits_and_deletes_own_message(env, channel, alice):
    message = await alice.send(channel, "first try")
    await alice.edit(message, "second try")
    assert channel.last_message.content == "second try"

    await alice.delete(message)
    assert channel.history() == []


async def test_bot_cannot_edit_another_users_message(env, channel, alice):
    message = await alice.send(channel, "alice's message")
    ch = env.bot.get_channel(channel.id)
    fetched = await ch.fetch_message(message.id)
    with pytest.raises(discord.Forbidden) as exc_info:
        await fetched.edit(content="hijacked by the bot")
    assert exc_info.value.code == 50005


async def test_cannot_dm_a_bot(env):
    # The DM channel opens fine; the failure surfaces only on send (50007).
    other_bot = env.backend.make_user("OtherBot", bot=True)
    user = await env.bot.fetch_user(other_bot.id)
    dm = await user.create_dm()
    with pytest.raises(discord.Forbidden) as exc_info:
        await dm.send("hello fellow bot")
    assert exc_info.value.code == 50007


async def test_user_cannot_edit_others_messages(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    message = await bob.send(channel, "bob's message")
    with pytest.raises(simcord.SetupError, match="own messages"):
        await alice.edit(message, "hijacked")


async def test_typing_event_reaches_bot(env, channel, alice):
    seen = []

    @env.bot.listen()
    async def on_typing(ch, user, when):
        seen.append((ch.id, user.id))

    await alice.typing(channel)
    assert seen == [(channel.id, alice.id)]


async def test_unreact(env, channel, alice):
    message = await alice.send(channel, "hello")
    await alice.react(message, "🔥")
    await alice.unreact(message, "🔥")
    refetched = await env.bot.get_channel(channel.id).fetch_message(message.id)
    assert refetched.reactions == []


async def test_bulk_delete_messages(env, channel):
    ch = env.bot.get_channel(channel.id)
    messages = [await ch.send(f"msg {i}") for i in range(4)]

    await ch.delete_messages(messages)
    await env.settle()

    assert channel.history() == []
    assert "MESSAGE_DELETE_BULK" in env.transcript()
    bulk = [e for e in env.guild.audit_log() if e.action_type == 73]
    assert bulk and bulk[-1].options.get("count") == "4"


async def test_bulk_delete_dispatches_event(env, channel):
    seen: list[int] = []

    @env.bot.listen()
    async def on_bulk_message_delete(messages):
        seen.append(len(messages))

    ch = env.bot.get_channel(channel.id)
    messages = [await ch.send(f"m{i}") for i in range(3)]
    await ch.delete_messages(messages)
    await env.settle()

    assert seen == [3]


async def test_bulk_delete_below_minimum_rejected(env, channel):
    # discord.py uses single delete below 2, so reaching the bulk route with one
    # id is a malformed call; the backend rejects it with 50035.
    ch = env.bot.get_channel(channel.id)
    message = await ch.send("only one")
    with pytest.raises(discord.HTTPException) as exc_info:
        await env.bot.http.delete_messages(channel.id, [message.id])
    assert exc_info.value.code == 50035


async def test_bulk_delete_requires_manage_messages(env):
    locked = env.guild.create_text_channel(
        "locked", overwrites={env.guild.default_role: discord.PermissionOverwrite(manage_messages=False)}
    )
    ch = env.bot.get_channel(locked.id)
    messages = [await ch.send(f"m{i}") for i in range(2)]
    with pytest.raises(discord.Forbidden) as exc_info:
        await ch.delete_messages(messages)
    assert exc_info.value.code == 50013


async def test_tts_and_allowed_mentions_are_retained_without_claiming_pings(env, channel, alice):
    ch = env.bot.get_channel(channel.id)
    message = await ch.send(
        f"@everyone {alice.mention}",
        tts=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )

    stored = env.backend.get_message(channel.id, message.id)
    assert message.tts is True
    assert stored.tts is True
    assert stored.mention_everyone is False
    assert stored.ping_user_ids == []
    assert stored.ping_role_ids == []


async def test_guild_sticker_send_round_trips_message_metadata(env, channel):
    sticker = env.guild.create_sticker("wave", format_type=1)
    guild_sticker = await env.bot.get_guild(env.guild.id).fetch_sticker(sticker.id)
    message = await env.bot.get_channel(channel.id).send("wave", stickers=[guild_sticker])

    stored = env.backend.get_message(channel.id, message.id)
    assert [(item.id, item.name) for item in message.stickers] == [(sticker.id, "wave")]
    assert [(item.id, item.format_type) for item in stored.stickers] == [(sticker.id, 1)]


async def test_pin_and_thread_system_messages_are_typed_and_single(env, channel):
    ch = env.bot.get_channel(channel.id)
    message = await ch.send("start here")

    await message.pin()
    await message.pin()
    await message.unpin()
    pin_notices = [item for item in channel.history() if item.type.value == MessageType.PINS_ADD]
    assert len(pin_notices) == 1
    pin_model = env.backend.get_message(channel.id, pin_notices[0].id)
    assert pin_model.system_metadata.referenced_message_id == message.id

    await message.create_thread(name="discussion")
    thread_notices = [item for item in channel.history() if item.type.value == MessageType.THREAD_CREATED]
    assert len(thread_notices) == 1
    thread_model = env.backend.get_message(channel.id, thread_notices[0].id)
    assert thread_model.system_metadata.channel_id == message.id
