"""Unit tests for the server-side intent simulation helpers."""

import discord

from simcord import intents


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

    mentioned = _message(mentions=[{"id": "999"}])
    assert not intents.censor_message(mentioned, bot_id=999) and mentioned["content"] == "secret"


def test_censor_applies_to_referenced_message():
    payload = _message(referenced_message=_message())
    assert intents.censor_message(payload, bot_id=999)
    assert payload["referenced_message"]["content"] == ""


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
