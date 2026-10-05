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


def test_empty_interaction_deferral_does_not_create_message(env, channel, alice):
    record = env.backend.new_interaction(2, channel.id, alice.id, env.guild.id)
    router.dispatch(
        env.backend,
        "POST",
        f"/interactions/{record.id}/{record.token}/callback",
        json={"type": 5, "data": {}},
    )
    assert record.responded and record.loading
    assert record.message_id is None
    assert not env.backend.messages[channel.id]


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
