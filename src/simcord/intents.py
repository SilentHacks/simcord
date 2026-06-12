"""Server-side gateway intent simulation.

Real Discord only delivers events whose intent the bot declared at IDENTIFY,
blanks message content without the ``message_content`` privileged intent, and
ships guild member lists via chunking rather than inline. discord.py handles
the client half of intents (cache policy, flag validation) on its own; this
module supplies the server half so a bot that would miss events in production
also misses them in tests.

Everything here operates on raw wire payloads at the gateway seam
(:class:`simcord.gateway.FakeGateway`), per attached client — the backend
itself stays intent-agnostic, exactly like the real API.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

import discord

#: The intents that require approval in the Discord developer portal.
PRIVILEGED_INTENTS = ("members", "presences", "message_content")

#: Gateway event -> (guild-context intent, DM-context intent). ``None`` as the
#: DM intent means the event only occurs in guilds; the guild intent applies
#: regardless. Events absent from this table (READY, INTERACTION_CREATE,
#: GUILD_MEMBERS_CHUNK, ...) are always delivered, as on real Discord.
#: Mirrors https://discord.com/developers/docs/events/gateway#list-of-intents.
EVENT_INTENTS: dict[str, tuple[str, str | None]] = {
    # GUILDS
    "GUILD_CREATE": ("guilds", None),
    "GUILD_UPDATE": ("guilds", None),
    "GUILD_DELETE": ("guilds", None),
    "GUILD_ROLE_CREATE": ("guilds", None),
    "GUILD_ROLE_UPDATE": ("guilds", None),
    "GUILD_ROLE_DELETE": ("guilds", None),
    "CHANNEL_CREATE": ("guilds", None),
    "CHANNEL_UPDATE": ("guilds", None),
    "CHANNEL_DELETE": ("guilds", None),
    "CHANNEL_PINS_UPDATE": ("guilds", "dm_messages"),
    "THREAD_CREATE": ("guilds", None),
    "THREAD_UPDATE": ("guilds", None),
    "THREAD_DELETE": ("guilds", None),
    "THREAD_LIST_SYNC": ("guilds", None),
    "THREAD_MEMBER_UPDATE": ("guilds", None),
    "THREAD_MEMBERS_UPDATE": ("guilds", None),
    "STAGE_INSTANCE_CREATE": ("guilds", None),
    "STAGE_INSTANCE_UPDATE": ("guilds", None),
    "STAGE_INSTANCE_DELETE": ("guilds", None),
    # GUILD_MEMBERS (privileged)
    "GUILD_MEMBER_ADD": ("members", None),
    "GUILD_MEMBER_UPDATE": ("members", None),
    "GUILD_MEMBER_REMOVE": ("members", None),
    # GUILD_MODERATION
    "GUILD_AUDIT_LOG_ENTRY_CREATE": ("moderation", None),
    "GUILD_BAN_ADD": ("moderation", None),
    "GUILD_BAN_REMOVE": ("moderation", None),
    # GUILD_EXPRESSIONS
    "GUILD_EMOJIS_UPDATE": ("emojis_and_stickers", None),
    "GUILD_STICKERS_UPDATE": ("emojis_and_stickers", None),
    "GUILD_SOUNDBOARD_SOUND_CREATE": ("emojis_and_stickers", None),
    "GUILD_SOUNDBOARD_SOUND_UPDATE": ("emojis_and_stickers", None),
    "GUILD_SOUNDBOARD_SOUND_DELETE": ("emojis_and_stickers", None),
    "GUILD_SOUNDBOARD_SOUNDS_UPDATE": ("emojis_and_stickers", None),
    # GUILD_INTEGRATIONS
    "GUILD_INTEGRATIONS_UPDATE": ("integrations", None),
    "INTEGRATION_CREATE": ("integrations", None),
    "INTEGRATION_UPDATE": ("integrations", None),
    "INTEGRATION_DELETE": ("integrations", None),
    # GUILD_WEBHOOKS
    "WEBHOOKS_UPDATE": ("webhooks", None),
    # GUILD_INVITES
    "INVITE_CREATE": ("invites", None),
    "INVITE_DELETE": ("invites", None),
    # GUILD_VOICE_STATES
    "VOICE_STATE_UPDATE": ("voice_states", None),
    "VOICE_CHANNEL_EFFECT_SEND": ("voice_states", None),
    # GUILD_PRESENCES (privileged)
    "PRESENCE_UPDATE": ("presences", None),
    # GUILD_MESSAGES / DIRECT_MESSAGES
    "MESSAGE_CREATE": ("guild_messages", "dm_messages"),
    "MESSAGE_UPDATE": ("guild_messages", "dm_messages"),
    "MESSAGE_DELETE": ("guild_messages", "dm_messages"),
    "MESSAGE_DELETE_BULK": ("guild_messages", "dm_messages"),
    # GUILD_MESSAGE_REACTIONS / DIRECT_MESSAGE_REACTIONS
    "MESSAGE_REACTION_ADD": ("guild_reactions", "dm_reactions"),
    "MESSAGE_REACTION_REMOVE": ("guild_reactions", "dm_reactions"),
    "MESSAGE_REACTION_REMOVE_ALL": ("guild_reactions", "dm_reactions"),
    "MESSAGE_REACTION_REMOVE_EMOJI": ("guild_reactions", "dm_reactions"),
    # GUILD_MESSAGE_TYPING / DIRECT_MESSAGE_TYPING
    "TYPING_START": ("guild_typing", "dm_typing"),
    # GUILD_SCHEDULED_EVENTS
    "GUILD_SCHEDULED_EVENT_CREATE": ("guild_scheduled_events", None),
    "GUILD_SCHEDULED_EVENT_UPDATE": ("guild_scheduled_events", None),
    "GUILD_SCHEDULED_EVENT_DELETE": ("guild_scheduled_events", None),
    "GUILD_SCHEDULED_EVENT_USER_ADD": ("guild_scheduled_events", None),
    "GUILD_SCHEDULED_EVENT_USER_REMOVE": ("guild_scheduled_events", None),
    # AUTO_MODERATION_CONFIGURATION / AUTO_MODERATION_EXECUTION
    "AUTO_MODERATION_RULE_CREATE": ("auto_moderation_configuration", None),
    "AUTO_MODERATION_RULE_UPDATE": ("auto_moderation_configuration", None),
    "AUTO_MODERATION_RULE_DELETE": ("auto_moderation_configuration", None),
    "AUTO_MODERATION_ACTION_EXECUTION": ("auto_moderation_execution", None),
    # GUILD_MESSAGE_POLLS / DIRECT_MESSAGE_POLLS
    "MESSAGE_POLL_VOTE_ADD": ("guild_polls", "dm_polls"),
    "MESSAGE_POLL_VOTE_REMOVE": ("guild_polls", "dm_polls"),
}


def required_intent(event: str, payload: Mapping[str, Any]) -> str | None:
    """The :class:`discord.Intents` flag name gating delivery of ``event``.

    Returns ``None`` for events that are always delivered. Context (guild vs
    DM) is decided by the presence of ``guild_id`` in the payload, as on the
    real gateway.
    """
    spec = EVENT_INTENTS.get(event)
    if spec is None:
        return None
    guild_intent, dm_intent = spec
    if dm_intent is not None and payload.get("guild_id") is None:
        return dm_intent
    return guild_intent


def missing_privileged_intents(declared: discord.Intents, approved: discord.Intents) -> list[str]:
    """Privileged intents the bot declared but the (simulated) portal has not approved."""
    return [name for name in PRIVILEGED_INTENTS if getattr(declared, name) and not getattr(approved, name)]


def censor_message(payload: MutableMapping[str, Any], bot_id: int) -> bool:
    """Blank content fields the bot may not see without ``message_content``.

    Mirrors Discord's documented exemptions: DMs, messages the bot authored,
    and messages that mention the bot keep their content. Applies recursively
    to ``referenced_message``, as the real gateway does. Returns True if
    anything was actually removed.
    """
    censored = False
    referenced = payload.get("referenced_message")
    if isinstance(referenced, MutableMapping):
        censored = censor_message(referenced, bot_id)
    if _content_exempt(payload, bot_id):
        return censored
    if payload.get("content"):
        payload["content"] = ""
        censored = True
    for key in ("embeds", "attachments", "components"):
        if payload.get(key):
            payload[key] = []
            censored = True
    if payload.pop("poll", None) is not None:
        censored = True
    return censored


def _content_exempt(payload: Mapping[str, Any], bot_id: int) -> bool:
    if payload.get("guild_id") is None:  # DMs are always visible
        return True
    bot_snowflake = str(bot_id)
    author = payload.get("author")
    if isinstance(author, Mapping) and str(author.get("id")) == bot_snowflake:
        return True
    return any(
        isinstance(user, Mapping) and str(user.get("id")) == bot_snowflake
        for user in payload.get("mentions") or ()
    )


def prune_guild_create(payload: MutableMapping[str, Any], intents: discord.Intents, bot_id: int) -> None:
    """Reduce GUILD_CREATE ``members`` to what the real gateway would inline.

    Discord sends only the connecting bot's own member in GUILD_CREATE; the
    rest arrives via member chunking (``members`` intent) — except that with
    the ``presences`` intent, small (non-large) guilds get the full member
    list inline (lazy-guild behaviour, which discord.py also relies on in
    ``_guild_needs_chunking``).
    """
    if intents.presences and not payload.get("large"):
        return
    bot_snowflake = str(bot_id)
    payload["members"] = [
        member
        for member in payload.get("members") or ()
        if str((member.get("user") or {}).get("id")) == bot_snowflake
    ]
