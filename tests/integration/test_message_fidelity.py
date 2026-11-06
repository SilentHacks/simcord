"""Bot message behavior calibrated against the independent live capture."""

import datetime
import io

import discord
import pytest

import simcord
from simcord.http import router


@pytest.mark.parametrize("permission", ["embed_links", "attach_files", "send_polls"])
async def test_send_payload_permissions_are_atomic(env, channel, permission):
    cached = env.bot.get_channel(channel.id)
    await cached.set_permissions(env.guild.me, **{permission: False})
    kwargs = {}
    if permission == "embed_links":
        kwargs["embed"] = discord.Embed(description="embed")
    elif permission == "attach_files":
        kwargs["file"] = discord.File(io.BytesIO(b"upload"), filename="file.txt")
    else:
        poll = discord.Poll(question="Question", duration=datetime.timedelta(hours=1))
        poll.add_answer(text="Only answer")
        kwargs["poll"] = poll
    with pytest.raises(discord.Forbidden) as caught:
        await cached.send(**kwargs)
    assert caught.value.code == 50013
    assert [message async for message in cached.history()] == []
    await cached.set_permissions(env.guild.me, **{permission: True})
    if permission == "attach_files":
        kwargs["file"] = discord.File(io.BytesIO(b"upload"), filename="file.txt")
    sent = await cached.send(**kwargs)
    fetched = await cached.fetch_message(sent.id)
    assert bool(fetched.embeds or fetched.attachments or fetched.poll)


async def test_missing_history_fetch_reply_pin_but_not_reactions(env, channel):
    cached = env.bot.get_channel(channel.id)
    message = await cached.send("seed")
    await message.pin()
    await cached.set_permissions(env.guild.me, read_message_history=False, add_reactions=False)
    assert [item async for item in cached.history()] == []
    assert [item async for item in cached.pins()] == []
    for operation, code in (
        (lambda: cached.fetch_message(message.id), 50001),
        (lambda: message.reply("reply"), 160002),
        (message.unpin, 50001),
    ):
        with pytest.raises(discord.Forbidden) as caught:
            await operation()
        assert caught.value.code == code
    with pytest.raises(discord.Forbidden) as caught:
        await message.add_reaction("🔥")
    assert caught.value.code == 50013
    await cached.set_permissions(env.guild.me, read_message_history=False, add_reactions=True)
    await message.add_reaction("🔥")
    await cached.set_permissions(env.guild.me, read_message_history=False, add_reactions=False)
    await message.add_reaction("🔥")
    await message.remove_reaction("🔥", env.guild.me)
    await cached.set_permissions(env.guild.me, read_message_history=True)
    fetched = await cached.fetch_message(message.id)
    assert fetched.reactions == []
    assert fetched.pinned


@pytest.mark.parametrize("kind", ["voice", "stage"])
async def test_voice_text_connect_and_pin_precedence(env, kind):
    handle = getattr(env.guild, f"create_{kind}_channel")("chat")
    await env.settle()
    cached = env.bot.get_channel(handle.id)
    message = await cached.send("seed")
    await cached.set_permissions(env.guild.me, connect=False, pin_messages=False)
    for operation in (
        lambda: cached.send("blocked"),
        lambda: cached.fetch_message(message.id),
        message.pin,
        lambda: message.add_reaction("🔥"),
        lambda: message.edit(content="blocked"),
        message.delete,
    ):
        with pytest.raises(discord.Forbidden) as caught:
            await operation()
        assert caught.value.code == 50001
    await cached.set_permissions(env.guild.me, connect=True, pin_messages=False)
    with pytest.raises(discord.HTTPException) as caught:
        await message.pin()
    assert (caught.value.status, caught.value.code) == (400, 50019)
    assert (await cached.fetch_message(message.id)).content == "seed"


async def test_archived_thread_mutations_and_atomic_send(env, channel):
    cached = env.bot.get_channel(channel.id)
    thread = await cached.create_thread(name="discussion", type=discord.ChannelType.public_thread)
    message = await thread.send("seed")
    await thread.edit(archived=True)
    for operation in (
        lambda: message.edit(content="blocked"),
        message.delete,
        lambda: message.add_reaction("🔥"),
        message.pin,
    ):
        with pytest.raises(discord.HTTPException) as caught:
            await operation()
        assert caught.value.code == 50083
    with pytest.raises(discord.HTTPException) as caught:
        await thread.send()
    assert caught.value.code == 50006
    assert (await env.bot.fetch_channel(thread.id)).archived
    sent = await thread.send("unarchive")
    assert not (await env.bot.fetch_channel(thread.id)).archived
    assert (await thread.fetch_message(sent.id)).content == "unarchive"
    assert (await thread.fetch_message(message.id)).content == "seed"


@pytest.mark.parametrize(
    "duration, code, error_code",
    [(0, 50035, "NUMBER_TYPE_MIN"), (769, 50035, "NUMBER_TYPE_MAX"), (768, 0, None)],
)
def test_poll_duration_wire_errors_and_single_answer(env, channel, duration, code, error_code):
    body = {
        "poll": {
            "question": {"text": "Question"},
            "duration": duration,
            "answers": [{"poll_media": {"text": "Only answer"}}],
        }
    }
    if code:
        with pytest.raises(simcord.BackendError) as caught:
            router.dispatch(env.backend, "POST", f"/channels/{channel.id}/messages", json=body)
        assert caught.value.code == code
        assert caught.value.errors["poll"]["duration"]["_errors"][0]["code"] == error_code
        assert channel.history() == []
    else:
        response = router.dispatch(env.backend, "POST", f"/channels/{channel.id}/messages", json=body)
        assert len(response["poll"]["answers"]) == 1


async def test_dm_send_bypasses_guild_payload_permissions(env):
    user = env.create_user("dm recipient")
    cached = await env.bot.fetch_channel(user.dm_channel.id)
    poll = discord.Poll(question="Question", duration=datetime.timedelta(hours=1))
    poll.add_answer(text="Answer")
    for kwargs in (
        {"embed": discord.Embed(description="embed")},
        {"file": discord.File(io.BytesIO(b"upload"), filename="file.txt")},
        {"poll": poll},
    ):
        sent = await cached.send(**kwargs)
        assert (await cached.fetch_message(sent.id)).id == sent.id


async def test_normal_send_silent_fallbacks_do_not_make_ephemeral_messages(env, channel):
    cached = env.bot.get_channel(channel.id)
    await cached.set_permissions(env.guild.me, send_tts_messages=False)
    response = router.dispatch(
        env.backend,
        "POST",
        f"/channels/{channel.id}/messages",
        json={"content": "ordinary", "tts": True, "flags": 64},
    )
    fetched = await cached.fetch_message(int(response["id"]))
    assert not fetched.tts
    assert not fetched.flags.ephemeral
    assert {item["kind"] for item in env.degradations} == {
        "normal_message_ephemeral_ignored",
        "tts_permission_dropped",
    }


async def test_crosspost_send_denial_is_missing_access(env):
    handle = env.guild.create_news_channel("announcements")
    await env.settle()
    cached = env.bot.get_channel(handle.id)
    message = await cached.send("publish")
    await cached.set_permissions(env.guild.me, read_message_history=False)
    with pytest.raises(discord.Forbidden) as caught:
        await message.publish()
    assert caught.value.code == 50001
    await cached.set_permissions(env.guild.me, send_messages=False, read_message_history=True)
    with pytest.raises(discord.Forbidden) as caught:
        await message.publish()
    assert caught.value.code == 50001
    with pytest.raises(discord.Forbidden) as caught:
        await cached.send("ordinary")
    assert caught.value.code == 50013
    assert not (await cached.fetch_message(message.id)).flags.crossposted
    await cached.set_permissions(env.guild.me, send_messages=True, manage_messages=False)
    await message.publish()
    assert (await cached.fetch_message(message.id)).flags.crossposted


async def test_unmentionable_role_ping_degrades_without_rejecting(env, channel):
    role = env.guild.create_role("Unmentionable", mentionable=False)
    cached = env.bot.get_channel(channel.id)
    await cached.set_permissions(env.guild.me, mention_everyone=False)
    sent = await cached.send(role.mention)
    assert (await cached.fetch_message(sent.id)).content == role.mention
    assert env.backend.get_message(channel.id, sent.id).ping_role_ids == []
    assert env.degradations == [{"kind": "unmentionable_role_ping_dropped", "channel_id": channel.id}]


async def test_thread_send_permission_and_locked_unarchive(env, channel):
    cached = env.bot.get_channel(channel.id)
    thread = await cached.create_thread(name="locked", type=discord.ChannelType.public_thread)
    await cached.set_permissions(env.guild.me, send_messages=False, send_messages_in_threads=True)
    assert (await thread.send("thread permission")).content == "thread permission"
    await cached.set_permissions(env.guild.me, send_messages_in_threads=False)
    with pytest.raises(discord.Forbidden) as caught:
        await thread.send("denied")
    assert caught.value.code == 50001
    await cached.set_permissions(env.guild.me, send_messages_in_threads=True)
    await thread.edit(archived=True, locked=True)
    await cached.set_permissions(env.guild.me, send_messages_in_threads=True, manage_threads=False)
    with pytest.raises(discord.Forbidden) as caught:
        await thread.send("locked")
    assert caught.value.code == 50001
    assert (await env.bot.fetch_channel(thread.id)).archived
    await cached.set_permissions(env.guild.me, send_messages_in_threads=True, manage_threads=True)
    sent = await thread.send("manager")
    fetched_thread = await env.bot.fetch_channel(thread.id)
    assert not fetched_thread.archived
    assert fetched_thread.locked
    assert (await thread.fetch_message(sent.id)).content == "manager"


def test_unknown_send_sticker_is_invalid_sticker_not_catalog_not_found(env, channel):
    with pytest.raises(simcord.BackendError) as caught:
        router.dispatch(
            env.backend,
            "POST",
            f"/channels/{channel.id}/messages",
            json={"content": "sticker", "sticker_ids": ["123456789"]},
        )
    assert (caught.value.status, caught.value.code) == (400, 50081)
    assert channel.history() == []


async def test_one_answer_poll_and_max_duration_round_trip(env, channel):
    cached = env.bot.get_channel(channel.id)
    poll = discord.Poll(question="One answer works", duration=datetime.timedelta(hours=768))
    poll.add_answer(text="Only answer")
    sent = await cached.send(poll=poll)
    fetched = await cached.fetch_message(sent.id)
    assert fetched.poll.question == "One answer works"
    assert [answer.text for answer in fetched.poll.answers] == ["Only answer"]


@pytest.mark.parametrize("kind", ["category", "forum"])
def test_nonmessage_channel_send_does_not_store_a_message(env, kind):
    handle = getattr(env.guild, f"create_{kind}" if kind == "category" else "create_forum_channel")(
        "container"
    )
    with pytest.raises(simcord.BackendError) as caught:
        router.dispatch(
            env.backend, "POST", f"/channels/{handle.id}/messages", json={"content": "not a post"}
        )
    assert caught.value.code == 50008
    assert handle.history() == []


async def test_mentions_literal_output_respects_allowed_mentions(env, channel, alice):
    cached = env.bot.get_channel(channel.id)
    suppressed = await cached.send(alice.mention, allowed_mentions=discord.AllowedMentions.none())
    assert suppressed.mentions == []
    assert (await cached.fetch_message(suppressed.id)).mentions == []
    allowed = await cached.send(
        alice.mention, allowed_mentions=discord.AllowedMentions(users=[discord.Object(alice.id)])
    )
    assert [user.id for user in allowed.mentions] == [alice.id]
    assert [user.id for user in (await cached.fetch_message(allowed.id)).mentions] == [alice.id]


async def test_allowed_tts_is_create_only_projection(env, channel):
    cached = env.bot.get_channel(channel.id)
    await cached.set_permissions(env.guild.me, send_tts_messages=True)
    sent = await cached.send("spoken", tts=True)
    assert sent.tts
    assert not (await cached.fetch_message(sent.id)).tts
    assert not (await anext(cached.history())).tts


async def test_known_external_emoji_is_stripped_only_when_denied(env, channel):
    other_guild = env.create_guild("Other guild")
    emoji = other_guild.create_emoji("known")
    local = env.guild.create_emoji("local")
    cached = env.bot.get_channel(channel.id)
    content = f"<:{emoji.name}:{emoji.id}> <:{local.name}:{local.id}> <:unknown:123456789>"
    await cached.set_permissions(env.guild.me, external_emojis=True)
    assert (await cached.send(content)).content == content
    await cached.set_permissions(env.guild.me, external_emojis=False)
    sent = await cached.send(content)
    expected = f":known: <:{local.name}:{local.id}> <:unknown:123456789>"
    assert sent.content == expected
    assert (await cached.fetch_message(sent.id)).content == expected
    assert env.degradations[-1]["kind"] == "external_emoji_permission_stripped"


@pytest.mark.parametrize("field, maximum", [("question", 300), ("answer", 55)])
async def test_poll_text_limits_have_authentic_trees(env, channel, field, maximum):
    question = "q" * maximum if field == "question" else "Question"
    answer = "a" * maximum if field == "answer" else "Answer"
    body = {
        "poll": {"question": {"text": question}, "duration": 1, "answers": [{"poll_media": {"text": answer}}]}
    }
    response = router.dispatch(env.backend, "POST", f"/channels/{channel.id}/messages", json=body)
    cached = env.bot.get_channel(channel.id)
    fetched = await cached.fetch_message(int(response["id"]))
    assert fetched.poll.question == question
    assert fetched.poll.answers[0].text == answer
    if field == "question":
        body["poll"]["question"]["text"] += "q"
        expected = {
            "poll": {
                "question": {
                    "text": {
                        "_errors": [
                            {
                                "code": "BASE_TYPE_BAD_LENGTH",
                                "message": "Must be between 1 and 300 in length.",
                            }
                        ]
                    }
                }
            }
        }
    else:
        body["poll"]["answers"][0]["poll_media"]["text"] += "a"
        expected = {
            "proto_data": {
                "poll": {
                    "answers": {
                        "0": {
                            "_errors": [
                                {
                                    "code": "POLL_ANSWER_TEXT_INVALID_LENGTH",
                                    "message": "Poll answer text cannot exceed 55 characters",
                                }
                            ]
                        }
                    }
                }
            }
        }
    with pytest.raises(simcord.BackendError) as caught:
        router.dispatch(env.backend, "POST", f"/channels/{channel.id}/messages", json=body)
    assert caught.value.errors == expected
    assert len([item async for item in cached.history()]) == 1


@pytest.mark.parametrize("case", ["content", "mentions"])
def test_normal_message_field_error_trees_are_atomic(env, channel, alice, case):
    if case == "content":
        body = {"content": "c" * 2001}
        expected = {
            "content": {
                "_errors": [
                    {
                        "code": "BASE_TYPE_MAX_LENGTH",
                        "message": "Must be 2000 or fewer in length.",
                    }
                ]
            }
        }
    else:
        body = {"content": alice.mention, "allowed_mentions": {"parse": ["users"], "users": [str(alice.id)]}}
        expected = {
            "allowed_mentions": {
                "_errors": [
                    {
                        "code": "MESSAGE_ALLOWED_MENTIONS_PARSE_EXCLUSIVE",
                        "message": 'parse:["users"] and users: [ids...] are mutually exclusive.',
                    }
                ]
            }
        }
    with pytest.raises(simcord.BackendError) as caught:
        router.dispatch(env.backend, "POST", f"/channels/{channel.id}/messages", json=body)
    assert caught.value.errors == expected
    assert channel.history() == []


async def test_reply_history_requirement_is_observed_and_attributed(env, channel):
    cached = env.bot.get_channel(channel.id)
    message = await cached.send("seed")
    await cached.set_permissions(env.guild.me, read_message_history=False)
    with pytest.raises(discord.Forbidden) as caught:
        await message.reply("reply")
    assert caught.value.code == 160002
    assert env.observed_permissions().read_message_history
    assert env.http_requests[-1].denial["missing"] == ["read_message_history"]
