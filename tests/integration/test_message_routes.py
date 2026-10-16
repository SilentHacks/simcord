"""Message-route coverage: history pagination, cross-author delete, unpin, and
poll answer/expire endpoints — the paths the actor-level helpers don't reach.
"""

import datetime
import io

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


@pytest.mark.parametrize(
    "embeds",
    [
        [{"description": "x" * 3001}] * 2,
        [{"title": "x" * 257}],
        [{"description": "x" * 4097}],
        [{"fields": [{"name": "x" * 257, "value": "v"}]}],
        [{"fields": [{"name": "n", "value": "x" * 1025}]}],
        [{"footer": {"text": "x" * 2049}}],
        [{"author": {"name": "x" * 257}}],
        [{"fields": [{"name": "n", "value": "v"}] * 26}],
        [{"title": "x"}] * 11,
    ],
)
def test_message_create_and_edit_share_embed_validation(env, channel, embeds):
    backend = env.backend
    author = backend.bot_user.id
    message = backend.create_message(channel.id, author, "keep")
    before_events = [item for item in backend.transcript if item[0] == "GATEWAY"]
    for operation in (
        lambda: backend.create_message(channel.id, author, embeds=embeds),
        lambda: backend.edit_message(channel.id, message.id, {"content": "changed", "embeds": embeds}),
        lambda: router.dispatch(backend, "POST", f"/channels/{channel.id}/messages", json={"embeds": embeds}),
        lambda: router.dispatch(
            backend,
            "PATCH",
            f"/channels/{channel.id}/messages/{message.id}",
            json={"content": "changed", "embeds": embeds},
        ),
    ):
        with pytest.raises(simcord.BackendError) as caught:
            operation()
        assert caught.value.code == 50035
        assert list(backend.messages[channel.id]) == [message.id]
        assert message.content == "keep"
        assert message.embeds == []
        assert [item for item in backend.transcript if item[0] == "GATEWAY"] == before_events


async def test_empty_creation_rejected_for_user_bot_and_backend(env, channel, alice):
    with pytest.raises(simcord.BackendError, match="empty message"):
        await alice.send(channel)
    with pytest.raises(discord.HTTPException) as caught:
        await env.bot.get_channel(channel.id).send()
    assert caught.value.code == 50035
    with pytest.raises(simcord.BackendError, match="empty message"):
        env.backend.create_message(channel.id, alice.id, " \n\t")
    assert not env.backend.messages[channel.id]


async def test_contentless_creates_and_nullable_partial_edits(env, channel, alice):
    ch = env.bot.get_channel(channel.id)
    attachment = await ch.send(file=discord.File(io.BytesIO(b"file"), filename="a.txt"))
    user_attachment = await alice.send(channel, attachments=[("b.txt", b"user")])
    embed_text = " \n" + "🙂" * 256 + "\t"
    embed = await ch.send(embed=discord.Embed(title=embed_text))
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Go", custom_id="go"))
    component = await ch.send(view=view)
    poll = await ch.send(poll=_make_poll())
    assert all(message.content == "" for message in (attachment, user_attachment, embed, component, poll))
    assert env.backend.get_message(channel.id, embed.id).embeds[0]["title"] == embed_text
    stored = env.backend.get_message(channel.id, embed.id)
    router.dispatch(
        env.backend,
        "PATCH",
        f"/channels/{channel.id}/messages/{embed.id}",
        json={"content": None, "embeds": None, "components": None, "attachments": None},
    )
    assert stored.content == ""
    assert stored.embeds == stored.components == stored.attachments == []
    router.dispatch(env.backend, "PATCH", f"/channels/{channel.id}/messages/{embed.id}", json={})
    assert stored.content == ""
    view.stop()


@pytest.mark.parametrize(
    "data",
    [{}, {"embeds": [{"description": "x" * 3001}] * 2}, {"embeds": [{"title": "x" * 257}]}],
)
def test_invalid_interaction_response_does_not_consume_ack(env, channel, alice, data):
    backend = env.backend
    record = backend.new_interaction(2, channel.id, alice.id, env.guild.id)
    path = f"/interactions/{record.id}/{record.token}/callback"
    before_events = [item for item in backend.transcript if item[0] == "GATEWAY"]
    with pytest.raises(simcord.BackendError) as caught:
        router.dispatch(backend, "POST", path, json={"type": 4, "data": data})
    assert caught.value.code == 50035
    assert not record.responded
    assert record.message_id is None
    assert not backend.messages[channel.id]
    assert [item for item in backend.transcript if item[0] == "GATEWAY"] == before_events
    router.dispatch(backend, "POST", path, json={"type": 4, "data": {"content": "retry"}})
    assert record.responded
    assert backend.get_message(channel.id, record.message_id).content == "retry"


def test_interaction_deferral_creates_pending_message(env, channel, alice):
    record = env.backend.new_interaction(2, channel.id, alice.id, env.guild.id)
    router.dispatch(
        env.backend,
        "POST",
        f"/interactions/{record.id}/{record.token}/callback",
        json={"type": 5, "data": {}},
    )
    assert record.responded and record.loading
    message = env.backend.get_message(channel.id, record.message_id)
    assert message.content == ""
    assert message.flags == 1 << 7


@pytest.mark.parametrize("interaction_type", [2, 5])
@pytest.mark.parametrize("data", [{}, {"content": "valid text"}])
def test_update_callback_requires_source_before_acknowledgement(env, channel, alice, interaction_type, data):
    backend = env.backend
    record = backend.new_interaction(interaction_type, channel.id, alice.id, env.guild.id)
    path = f"/interactions/{record.id}/{record.token}/callback"
    before_events = [item for item in backend.transcript if item[0] == "GATEWAY"]
    with pytest.raises(simcord.BackendError, match="requires a source message") as caught:
        router.dispatch(backend, "POST", path, json={"type": 7, "data": data})
    assert caught.value.code == 50035
    assert not record.responded
    assert record.response_kind is None
    assert record.message_id is None
    assert not backend.messages[channel.id]
    assert [item for item in backend.transcript if item[0] == "GATEWAY"] == before_events
    router.dispatch(backend, "POST", path, json={"type": 5, "data": {}})
    assert record.responded and record.loading


@pytest.mark.parametrize("interaction_type", [3, 5])
def test_source_update_validates_before_ack_and_accepts_nullable_edit(env, channel, alice, interaction_type):
    backend = env.backend
    message = backend.create_message(channel.id, backend.bot_user.id, "keep")
    record = backend.new_interaction(interaction_type, channel.id, alice.id, env.guild.id)
    record.source_message_id = message.id
    path = f"/interactions/{record.id}/{record.token}/callback"
    before_events = [item for item in backend.transcript if item[0] == "GATEWAY"]
    with pytest.raises(simcord.BackendError) as caught:
        router.dispatch(
            backend,
            "POST",
            path,
            json={"type": 7, "data": {"content": "changed", "embeds": [{"title": "x" * 257}]}},
        )
    assert caught.value.code == 50035
    assert not record.responded
    assert message.content == "keep"
    assert [item for item in backend.transcript if item[0] == "GATEWAY"] == before_events
    router.dispatch(backend, "POST", path, json={"type": 7, "data": {"content": None, "embeds": None}})
    assert record.responded
    assert record.message_id == message.id
    assert message.content == "" and message.embeds == []


@pytest.mark.parametrize("callback_type", [4, 5, 6])
@pytest.mark.parametrize("elapsed", [0, 2.999, 3, 3.001])
def test_initial_callback_deadline_and_unacknowledged_token(env, channel, alice, callback_type, elapsed):
    backend = env.backend
    source = backend.create_message(channel.id, backend.bot_user.id, "source")
    record = backend.new_interaction(3, channel.id, alice.id, env.guild.id)
    record.source_message_id = source.id
    callback = f"/interactions/{record.id}/{record.token}/callback"
    webhook = f"/webhooks/{backend.application_id}/{record.token}"
    before_events = [item for item in backend.transcript if item[0] == "GATEWAY"]
    backend.advance_clock(elapsed)
    if elapsed < 3:
        router.dispatch(
            backend, "POST", callback, json={"type": callback_type, "data": {"content": "accepted"}}
        )
        assert record.responded
        if callback_type == 6:
            assert record.message_id == source.id
            assert source.content == "source"
        else:
            assert record.message_id != source.id
            assert record.loading is (callback_type == 5)
        return

    with pytest.raises(simcord.BackendError) as caught:
        router.dispatch(backend, "POST", callback, json={"type": callback_type})
    assert caught.value.status == 404 and caught.value.code == 10062
    assert not record.responded and record.message_id is None
    # Missing the initial deadline invalidates the webhook token too, before
    # body validation or looking for either an original or a followup.
    for method, path in (
        ("POST", webhook),
        ("GET", f"{webhook}/messages/@original"),
        ("PATCH", f"{webhook}/messages/@original"),
        ("DELETE", f"{webhook}/messages/@original"),
        ("GET", f"{webhook}/messages/{source.id}"),
        ("PATCH", f"{webhook}/messages/{source.id}"),
        ("DELETE", f"{webhook}/messages/{source.id}"),
    ):
        with pytest.raises(simcord.BackendError) as caught:
            router.dispatch(backend, method, path, json={"content": "x" * 2001})
        assert caught.value.code == 10015
    assert list(backend.messages[channel.id]) == [source.id]
    assert [item for item in backend.transcript if item[0] == "GATEWAY"] == before_events


@pytest.mark.parametrize("elapsed", [899.999, 900, 900.001])
@pytest.mark.parametrize("callback_type", [4, 5, 6])
@pytest.mark.parametrize("ephemeral", [False, True])
def test_acknowledged_continuation_deadline_and_error_precedence(
    env, channel, alice, elapsed, callback_type, ephemeral
):
    backend = env.backend
    source = backend.create_message(channel.id, backend.bot_user.id, "source")
    record = backend.new_interaction(3, channel.id, alice.id, env.guild.id)
    record.source_message_id = source.id
    callback = f"/interactions/{record.id}/{record.token}/callback"
    webhook = f"/webhooks/{backend.application_id}/{record.token}"
    backend.advance_clock(2.999)
    router.dispatch(
        backend,
        "POST",
        callback,
        json={"type": callback_type, "data": {"content": "accepted", "flags": 64 if ephemeral else 0}},
    )
    original_id = record.message_id
    # Even beyond three seconds, an acknowledged callback is a duplicate,
    # not an unacknowledged timeout. Admission takes precedence over its body.
    backend.advance_clock(1)
    with pytest.raises(simcord.BackendError) as caught:
        router.dispatch(backend, "POST", callback, json={"type": 99})
    assert caught.value.code == 40060
    deadline = datetime.datetime.fromisoformat(record.expires_at)
    now = datetime.datetime.fromisoformat(backend.now_iso())
    target = deadline + datetime.timedelta(seconds=elapsed - 900)
    backend.advance_clock((target - now).total_seconds())
    original = f"{webhook}/messages/@original"
    by_id = f"{webhook}/messages/{original_id}"
    if elapsed >= 900:
        for method, path in (
            ("GET", original),
            ("PATCH", original),
            ("DELETE", original),
            ("POST", webhook),
            ("GET", by_id),
            ("PATCH", by_id),
            ("DELETE", by_id),
            ("GET", f"{webhook}/messages/{original_id + 1}"),
        ):
            with pytest.raises(simcord.BackendError) as caught:
                router.dispatch(backend, method, path, json={"content": "x" * 2001})
            assert caught.value.code == 10015
        with pytest.raises(simcord.BackendError) as caught:
            router.dispatch(backend, "POST", callback, json={"type": 99})
        assert caught.value.code == 10062
        assert record.message_id == original_id
        return

    fetched = router.dispatch(backend, "GET", original)
    assert int(fetched["id"]) == original_id
    completed = router.dispatch(backend, "PATCH", original, json={"content": "completed"})
    assert int(completed["id"]) == original_id
    assert completed["content"] == "completed"
    assert not record.loading
    assert bool(completed["flags"] & 64) is (ephemeral and callback_type != 6)
    router.dispatch(backend, "DELETE", original)
    for method in ("GET", "PATCH", "DELETE"):
        with pytest.raises(simcord.BackendError) as caught:
            router.dispatch(backend, method, original, json={"content": "must not recreate"})
        assert caught.value.code == 10008
    assert record.message_id == original_id
    backend.advance_clock(0.001)
    with pytest.raises(simcord.BackendError) as caught:
        router.dispatch(backend, "GET", original)
    assert caught.value.code == 10015


def test_invalid_callback_does_not_extend_initial_deadline(env, channel, alice):
    backend = env.backend
    record = backend.new_interaction(2, channel.id, alice.id, env.guild.id)
    callback = f"/interactions/{record.id}/{record.token}/callback"
    backend.advance_clock(2.999)
    with pytest.raises(simcord.BackendError) as caught:
        router.dispatch(backend, "POST", callback, json={"type": 99})
    assert caught.value.code == 50035
    backend.advance_clock(0.001)
    with pytest.raises(simcord.BackendError) as caught:
        router.dispatch(backend, "POST", callback, json={"type": 99})
    assert caught.value.code == 10062
    assert not record.responded and record.message_id is None
