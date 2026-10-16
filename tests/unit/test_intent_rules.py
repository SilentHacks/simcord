"""Unit tests for the server-side intent simulation helpers."""

import copy
from types import SimpleNamespace

import discord
import pytest

from simcord import intents
from simcord.backend import serializers
from simcord.backend.models import AllowedMentions
from simcord.gateway import FakeGateway


def test_every_table_entry_is_a_real_intents_flag():
    valid = set(discord.Intents.VALID_FLAGS)
    for event, (guild_intent, dm_intent) in intents.EVENT_INTENTS.items():
        assert guild_intent in valid, f"{event}: {guild_intent} is not an Intents flag"
        if dm_intent is not None:
            assert dm_intent in valid, f"{event}: {dm_intent} is not an Intents flag"


def test_required_intent_guild_vs_dm_context():
    assert intents.required_intent("MESSAGE_CREATE", {"guild_id": "1"}) == "guild_messages"
    assert intents.required_intent("MESSAGE_CREATE", {}) == "dm_messages"
    assert intents.required_intent("TYPING_START", {"guild_id": "1"}) == "guild_typing"
    assert intents.required_intent("TYPING_START", {}) == "dm_typing"
    # Guild-only events use the guild intent regardless of payload shape.
    assert intents.required_intent("GUILD_MEMBER_ADD", {"guild_id": "1"}) == "members"
    # Ungated events are always delivered.
    assert intents.required_intent("READY", {}) is None
    assert intents.required_intent("INTERACTION_CREATE", {"guild_id": "1"}) is None
    assert intents.required_intent("GUILD_MEMBERS_CHUNK", {"guild_id": "1"}) is None


def test_missing_privileged_intents():
    declared = discord.Intents.default()
    declared.members = True
    declared.message_content = True
    approved = discord.Intents.default()
    approved.members = True
    assert intents.missing_privileged_intents(declared, approved) == ["message_content"]
    assert intents.missing_privileged_intents(discord.Intents.default(), discord.Intents.none()) == []


def _message(**overrides):
    payload = {
        "guild_id": "1",
        "author": {"id": "100"},
        "content": "secret",
        "embeds": [{"title": "x"}],
        "attachments": [{"id": "5"}],
        "components": [{"type": 1}],
        "mentions": [],
    }
    payload.update(overrides)
    return payload


def test_censor_blanks_content_fields():
    payload = _message()
    assert intents.censor_message(payload, bot_id=999)
    assert payload["content"] == ""
    assert payload["embeds"] == [] and payload["attachments"] == [] and payload["components"] == []


def test_censor_exemptions():
    dm = _message(guild_id=None)
    assert not intents.censor_message(dm, bot_id=999) and dm["content"] == "secret"

    own = _message(author={"id": "999"})
    assert not intents.censor_message(own, bot_id=999) and own["content"] == "secret"

    mentioned = _message(content="<@999>", mentions=[{"id": "999"}])
    assert not intents.censor_message(mentioned, bot_id=999) and mentioned["content"] == "<@999>"


def test_censor_applies_to_referenced_message():
    payload = _message(referenced_message=_message())
    assert intents.censor_message(payload, bot_id=999)
    assert payload["referenced_message"]["content"] == ""


def test_exempt_root_does_not_exempt_references_or_remove_metadata():
    nested = _message(poll={"question": {"text": "secret"}}, id="10", channel_id="20")
    payload = _message(content="<@999>", referenced_message=nested)
    assert intents.censor_message(payload, 999)
    assert payload["content"] == "<@999>"
    assert nested["content"] == "" and "poll" not in nested
    assert nested["id"] == "10" and nested["channel_id"] == "20"


def test_prune_guild_create_keeps_only_the_bot_member():
    payload = {
        "large": False,
        "members": [{"user": {"id": "999"}}, {"user": {"id": "100"}}],
    }
    intents.prune_guild_create(payload, discord.Intents.default(), bot_id=999)
    assert payload["members"] == [{"user": {"id": "999"}}]


def test_prune_guild_create_keeps_full_list_with_presences_for_small_guilds():
    flags = discord.Intents.default()
    flags.presences = True
    payload = {
        "large": False,
        "members": [{"user": {"id": "999"}}, {"user": {"id": "100"}}],
    }
    intents.prune_guild_create(payload, flags, bot_id=999)
    assert len(payload["members"]) == 2


def test_unknown_context_and_reply_metadata_do_not_imply_exemption():
    payload = _message(mentions=[{"id": "999"}])
    del payload["guild_id"]
    assert intents.censor_message(payload, 999)
    partial = {"id": "1", "channel_id": "2", "embeds": [{"title": "hidden"}]}
    assert intents.censor_message(partial, 999)
    assert partial == {"id": "1", "channel_id": "2", "embeds": []}


@pytest.mark.parametrize("interaction_origin", [False, True])
@pytest.mark.parametrize("ping", [False, True])
async def test_reply_projection_and_deleted_target_edits(env, channel, alice, interaction_origin, ping):
    backend = env.backend
    bot_id = backend.bot_user.id
    source = backend.create_message(
        channel.id,
        bot_id,
        "bot source",
        interaction_metadata={
            "id": "123",
            "type": 2,
            "user": dict(serializers.user_payload(backend.users[alice.id])),
        }
        if interaction_origin
        else None,
    )
    reply = backend.create_message(
        channel.id,
        alice.id,
        "reply body",
        reference={"channel_id": str(channel.id), "message_id": str(source.id)},
        allowed_mentions=AllowedMentions(replied_user=ping),
    )
    original = copy.deepcopy(serializers.message_payload(backend, reply))
    assert (bot_id in reply.ping_user_ids) is ping
    assert (str(bot_id) in [user["id"] for user in original["mentions"]]) is ping

    def projected():
        payload = copy.deepcopy(serializers.message_payload(backend, reply))
        intents.censor_message(payload, bot_id, backend=backend)
        return payload

    assert projected()["content"] == ("reply body" if ping and not interaction_origin else "")
    assert serializers.message_payload(backend, reply) == original
    assert not any(key.startswith("reply_author") for key in original)
    backend.delete_message(channel.id, source.id)
    backend.edit_message(channel.id, reply.id, {"content": "edited"})
    assert projected()["content"] == ("edited" if ping and not interaction_origin else "")
    backend.edit_message(channel.id, reply.id, {"allowed_mentions": AllowedMentions(replied_user=False)})
    assert projected()["content"] == ""
    backend.edit_message(channel.id, reply.id, {"content": f"explicit <@{bot_id}>"})
    assert projected()["content"] == f"explicit <@{bot_id}>"
    assert reply.content == f"explicit <@{bot_id}>"
    backend.edit_message(channel.id, reply.id, {"content": "mention removed"})
    assert projected()["content"] == ""


async def test_components_v2_mentions_reevaluate_and_partial_updates_stay_partial(env, channel, alice):
    backend = env.backend
    bot_id = backend.bot_user.id
    message = backend.create_message(
        channel.id,
        alice.id,
        flags=1 << 15,
        components=[{"type": 10, "content": f"hello <@{bot_id}>"}],
    )
    payload = copy.deepcopy(serializers.message_payload(backend, message))
    assert not intents.censor_message(payload, bot_id, backend=backend)
    assert payload["components"] == message.components
    backend.edit_message(channel.id, message.id, {"components": [{"type": 10, "content": "no mention"}]})
    partial = {"id": str(message.id), "channel_id": str(channel.id), "components": message.components}
    assert intents.censor_message(partial, bot_id, backend=backend)
    assert partial == {"id": str(message.id), "channel_id": str(channel.id), "components": []}
    assert message.components[0]["content"] == "no mention"


@pytest.mark.parametrize("kind", ["command", "component", "modal"])
async def test_interaction_context_only_exempts_named_root(env, channel, alice, kind):
    backend = env.backend
    ordinary = backend.create_message(channel.id, alice.id, "ordinary target")
    source = backend.create_message(
        channel.id,
        alice.id,
        "selected body",
        embeds=[{"title": "selected embed"}],
        reference={"channel_id": str(channel.id), "message_id": str(ordinary.id)},
    )
    root = copy.deepcopy(serializers.message_payload(backend, source))
    root.pop("guild_id")
    root["referenced_message"].pop("guild_id")
    interaction = {"type": {"command": 2, "component": 3, "modal": 5}[kind], "data": {}}
    if kind == "command":
        interaction["data"] = {
            "type": 3,
            "target_id": str(source.id),
            "resolved": {"messages": {str(source.id): root}},
        }
    else:
        interaction["message"] = root
    delivered = []
    gateway = FakeGateway(
        backend,
        SimpleNamespace(intents=discord.Intents.default(), parsers={"INTERACTION_CREATE": delivered.append}),
    )
    gateway.feed("INTERACTION_CREATE", interaction)
    visible = (
        delivered[0]["data"]["resolved"]["messages"][str(source.id)]
        if kind == "command"
        else delivered[0]["message"]
    )
    assert visible["content"] == "selected body"
    assert visible["embeds"] == [{"title": "selected embed"}]
    assert visible["referenced_message"]["content"] == ""
    assert root["referenced_message"]["content"] == "ordinary target"
    later = copy.deepcopy(serializers.message_payload(backend, source))
    intents.censor_message(later, backend.bot_user.id, backend=backend)
    assert later["content"] == later["referenced_message"]["content"] == ""
