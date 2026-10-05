import io

import discord
import pytest

import simcord
from simcord.http import router


async def test_create_forum_post(env):
    forum = env.guild.create_forum_channel("help")
    await env.settle()

    cached = env.bot.get_channel(forum.id)
    twm = await cached.create_thread(name="How do I X?", content="please help")
    await env.settle()

    assert isinstance(twm.thread, discord.Thread)
    assert twm.thread.name == "How do I X?"
    assert twm.message.content == "please help"
    # The post shows up as a thread of the forum, carrying its starter message.
    posts = forum.threads
    assert [p.name for p in posts] == ["How do I X?"]
    assert posts[0].history()[-1].content == "please help"


async def test_configure_forum_tags(env):
    forum = env.guild.create_forum_channel("help")
    await env.settle()

    cached = await env.bot.fetch_channel(forum.id)
    await cached.edit(available_tags=[discord.ForumTag(name="bug"), discord.ForumTag(name="feature")])
    await env.settle()

    refetched = await env.bot.fetch_channel(forum.id)
    assert [t.name for t in refetched.available_tags] == ["bug", "feature"]


async def test_forum_post_with_applied_tag(env):
    forum = env.guild.create_forum_channel("help")
    await env.settle()

    cached = await env.bot.fetch_channel(forum.id)
    await cached.edit(available_tags=[discord.ForumTag(name="bug")])
    await env.settle()

    cached = await env.bot.fetch_channel(forum.id)
    tag = cached.available_tags[0]
    twm = await cached.create_thread(name="It crashes", content="stack trace inside", applied_tags=[tag])
    await env.settle()

    assert env.backend.get_channel(twm.thread.id).applied_tags == [tag.id]
    assert tag.id in {t.id for t in twm.thread.applied_tags}


async def test_forum_post_rejects_unknown_tag(env):
    # A tag that belongs to a different forum is not valid here.
    other = env.guild.create_forum_channel("other")
    target = env.guild.create_forum_channel("help")
    await env.settle()

    cached_other = await env.bot.fetch_channel(other.id)
    await cached_other.edit(available_tags=[discord.ForumTag(name="bug")])
    await env.settle()
    foreign_tag = (await env.bot.fetch_channel(other.id)).available_tags[0]

    cached_target = await env.bot.fetch_channel(target.id)
    with pytest.raises(discord.HTTPException) as exc_info:
        await cached_target.create_thread(name="oops", content="body", applied_tags=[foreign_tag])
    assert exc_info.value.code == 50035


async def test_forum_post_requires_send_messages(env):
    forum = env.guild.create_forum_channel("help")
    await env.settle()

    mask = ~discord.Permissions(send_messages=True).value
    for role in env.backend.get_guild(env.guild.id).roles.values():
        role.permissions &= mask

    cached = env.bot.get_channel(forum.id)
    with pytest.raises(discord.Forbidden) as exc_info:
        await cached.create_thread(name="nope", content="blocked")
    assert exc_info.value.code == 50013


def _forum_state(backend, forum_id):
    return (
        set(backend.channels),
        {key: set(messages) for key, messages in backend.messages.items()},
        list(backend.get_guild(backend.get_channel(forum_id).guild_id).thread_ids),
        backend.get_channel(forum_id).last_message_id,
        dict(backend.cdn._blobs),
        [item for item in backend.transcript if item[0] == "GATEWAY"],
    )


@pytest.mark.parametrize(
    "message",
    [
        {},
        {"content": "x" * 2001},
        {"embeds": [{"description": "x" * 3001}] * 2},
        {"embeds": [{"footer": {"text": "x" * 2049}}]},
        {"content": "not allowed", "flags": 32768, "components": [{"type": 10, "content": "v2"}]},
        {"components": [{"type": 1, "components": [{"type": 2, "style": 1}]}]},
        {"flags": 32768, "components": [{"type": 13, "file": {"url": "attachment://missing.bin"}}]},
        {"content": "ok", "unmodelled": True},
        {"content": "ok", "sticker_ids": [1]},
    ],
)
def test_failed_forum_validation_leaves_no_world_cdn_or_event_state(env, message):
    forum = env.guild.create_forum_channel("help")
    before = _forum_state(env.backend, forum.id)
    upload = discord.File(io.BytesIO(b"unused"), filename="upload.bin")
    # Empty starters must not accidentally be rescued by the test's upload.
    files = [] if not message else [upload]
    with pytest.raises(simcord.BackendError):
        router.dispatch(
            env.backend,
            "POST",
            f"/channels/{forum.id}/threads",
            json={"name": "invalid", "message": message},
            files=files,
        )
    assert _forum_state(env.backend, forum.id) == before
    assert upload.fp.tell() == 0


def test_forum_reads_every_upload_before_mutating_state(env):
    class BrokenRead(io.BytesIO):
        def read(self, *args):
            raise OSError("upload read failed")

    forum = env.guild.create_forum_channel("help")
    before = _forum_state(env.backend, forum.id)
    first = discord.File(io.BytesIO(b"first"), filename="first.bin")
    second = discord.File(BrokenRead(b"second"), filename="second.bin")
    with pytest.raises(OSError, match="upload read failed"):
        router.dispatch(
            env.backend,
            "POST",
            f"/channels/{forum.id}/threads",
            json={"name": "invalid", "message": {}},
            files=[first, second],
        )
    assert first.fp.tell() == len(b"first")
    assert _forum_state(env.backend, forum.id) == before


@pytest.mark.parametrize("starter", ["attachment", "embed", "component", "poll"])
def test_forum_subscribers_see_complete_contentless_starter(env, starter):
    backend = env.backend
    forum = env.guild.create_forum_channel("help")
    upload = discord.File(io.BytesIO(b"starter"), filename="starter.bin")
    messages = {
        "attachment": {},
        "embed": {"embeds": [{"title": "starter"}]},
        "component": {
            "flags": 32768,
            "components": [{"type": 13, "file": {"url": "attachment://starter.bin"}}],
        },
        "poll": {"poll": {"question": {"text": "Lunch?"}, "answers": [{"poll_media": {"text": "Pizza"}}]}},
    }
    files = [upload] if starter in {"attachment", "component"} else []
    seen = []

    def observe(event, payload):
        if event not in {"THREAD_CREATE", "MESSAGE_CREATE"}:
            return
        thread_id = int(payload["id"] if event == "THREAD_CREATE" else payload["channel_id"])
        thread = backend.get_channel(thread_id)
        assert thread_id in backend.get_guild(env.guild.id).thread_ids
        assert thread.message_count == 1
        assert forum.id == thread.parent_id
        assert backend.get_channel(forum.id).last_message_id == thread_id
        message = backend.get_message(thread_id, thread.last_message_id)
        assert message.content == ""
        if files:
            assert backend.cdn.get(message.attachments[0]["url"]) == b"starter"
        if starter == "component":
            assert message.components[0]["file"]["attachment_id"] == message.attachments[0]["id"]
        seen.append(event)

    backend.subscribers.append(observe)
    try:
        result = router.dispatch(
            backend,
            "POST",
            f"/channels/{forum.id}/threads",
            json={"name": "complete", "message": messages[starter]},
            files=files,
        )
    finally:
        backend.subscribers.remove(observe)
    assert seen == ["THREAD_CREATE", "MESSAGE_CREATE"]
    assert result["message"]["channel_id"] == result["id"]
    assert result["message"]["content"] == ""
