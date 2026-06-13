"""Serialization of backend models into Discord wire-format payloads.

Return annotations reference discord.py's own ``discord.types`` TypedDicts —
the exact contract its parsers consume — so payload shape drift against the
installed discord.py version is caught by static type checking rather than at
users' test runtime. Some payloads are intentionally partial (Discord itself
omits keys per context), hence the casts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from ..enums import VOICE_CHANNEL_TYPES, ChannelType
from .models import (
    AuditLogEntry,
    AutoModRule,
    Channel,
    Guild,
    GuildEmoji,
    Invite,
    Member,
    Message,
    Poll,
    Role,
    ScheduledEvent,
    Sticker,
    User,
    VoiceState,
    Webhook,
)

if TYPE_CHECKING:
    # discord.types modules are import-order sensitive at runtime (discord.py
    # itself only imports them under TYPE_CHECKING), so we do the same.
    from discord.types import (
        channel as channel_types,
    )
    from discord.types import (
        guild as guild_types,
    )
    from discord.types import (
        member as member_types,
    )
    from discord.types import (
        message as message_types,
    )
    from discord.types import (
        role as role_types,
    )
    from discord.types import (
        threads as thread_types,
    )
    from discord.types import (
        user as user_types,
    )

    from .state import Backend


def user_payload(user: User) -> user_types.User:
    return cast(
        "user_types.User",
        {
            "id": str(user.id),
            "username": user.name,
            "discriminator": "0",
            "global_name": user.name,
            "avatar": None,
            "bot": user.bot,
            "system": False,
            "public_flags": 0,
            "verified": True,
            "mfa_enabled": False,
            "locale": "en-US",
            "flags": 0,
        },
    )


def role_payload(role: Role) -> role_types.Role:
    return cast(
        "role_types.Role",
        {
            "id": str(role.id),
            "name": role.name,
            "permissions": str(role.permissions),
            "position": role.position,
            "color": role.color,
            "hoist": role.hoist,
            "managed": role.managed,
            "mentionable": role.mentionable,
            "flags": 0,
            "icon": None,
            "unicode_emoji": None,
        },
    )


def member_payload(
    backend: Backend, guild: Guild, member: Member, *, with_user: bool = True
) -> member_types.MemberWithUser:
    payload: dict[str, Any] = {
        "roles": [str(r) for r in member.role_ids],
        "nick": member.nick,
        "joined_at": member.joined_at,
        "premium_since": None,
        "deaf": member.deaf,
        "mute": member.mute,
        "pending": member.pending,
        "flags": 0,
        "communication_disabled_until": member.timed_out_until,
        "avatar": None,
    }
    if with_user:
        payload["user"] = user_payload(backend.users[member.user_id])
    return cast("member_types.MemberWithUser", payload)


def overwrite_payloads(channel: Channel) -> list[dict[str, Any]]:
    return [
        {"id": str(o.target_id), "type": o.type, "allow": str(o.allow), "deny": str(o.deny)}
        for o in channel.overwrites
    ]


def channel_payload(backend: Backend, channel: Channel) -> channel_types.GuildChannel:
    if channel.is_thread:
        return cast("channel_types.GuildChannel", thread_payload(backend, channel))
    payload: dict[str, Any] = {
        "id": str(channel.id),
        "type": channel.type,
        "name": channel.name,
        "position": channel.position,
        "permission_overwrites": overwrite_payloads(channel),
        "nsfw": channel.nsfw,
        "parent_id": str(channel.parent_id) if channel.parent_id else None,
        "topic": channel.topic,
        "rate_limit_per_user": channel.rate_limit_per_user,
        "last_message_id": str(channel.last_message_id) if channel.last_message_id else None,
    }
    if channel.guild_id is not None:
        payload["guild_id"] = str(channel.guild_id)
    if channel.type in VOICE_CHANNEL_TYPES:
        payload["bitrate"] = channel.bitrate
        payload["user_limit"] = channel.user_limit
        payload["rtc_region"] = channel.rtc_region
    if channel.type == ChannelType.FORUM:
        payload["available_tags"] = list(channel.available_tags)
        payload["default_reaction_emoji"] = channel.default_reaction_emoji
    if channel.type == ChannelType.DM:
        payload["recipients"] = [user_payload(backend.users[uid]) for uid in channel.recipient_ids]
        payload.pop("name")
        payload.pop("position")
        payload.pop("permission_overwrites")
    return cast("channel_types.GuildChannel", payload)


def thread_payload(backend: Backend, thread: Channel) -> thread_types.Thread:
    meta = thread.thread_metadata
    assert meta is not None
    return cast(
        "thread_types.Thread",
        {
            "id": str(thread.id),
            "type": thread.type,
            "guild_id": str(thread.guild_id),
            "name": thread.name,
            "parent_id": str(thread.parent_id),
            "owner_id": str(thread.owner_id),
            "last_message_id": str(thread.last_message_id) if thread.last_message_id else None,
            "rate_limit_per_user": thread.rate_limit_per_user,
            "message_count": thread.message_count,
            "member_count": (thread.message_count and 1) or 0,
            "total_message_sent": thread.message_count,
            "applied_tags": [str(t) for t in thread.applied_tags],
            "flags": 0,
            "thread_metadata": {
                "archived": meta.archived,
                "auto_archive_duration": meta.auto_archive_duration,
                "archive_timestamp": meta.archive_timestamp,
                "locked": meta.locked,
                "create_timestamp": meta.create_timestamp,
            },
        },
    )


def emoji_payload(emoji: str) -> dict[str, Any]:
    if ":" in emoji:
        name, _, emoji_id = emoji.partition(":")
        return {"id": emoji_id, "name": name}
    return {"id": None, "name": emoji}


def message_payload(
    backend: Backend,
    message: Message,
    *,
    for_user: int | None = None,
) -> message_types.Message:
    channel = backend.channels[message.channel_id]
    payload: dict[str, Any] = {
        "id": str(message.id),
        "channel_id": str(message.channel_id),
        "author": user_payload(backend.users[message.author_id]),
        "content": message.content,
        "timestamp": message.timestamp,
        "edited_timestamp": message.edited_timestamp,
        "tts": message.tts,
        "mention_everyone": message.mention_everyone,
        "mentions": [
            user_payload(backend.users[uid]) for uid in message.mention_user_ids if uid in backend.users
        ],
        "mention_roles": [str(r) for r in message.mention_role_ids],
        "attachments": list(message.attachments),
        "embeds": list(message.embeds),
        "components": list(message.components),
        "pinned": message.pinned,
        "type": message.type,
        "flags": message.flags,
        "nonce": None,
        "reactions": [
            {
                "emoji": emoji_payload(r.emoji),
                "count": len(r.user_ids),
                "me": for_user in r.user_ids if for_user is not None else False,
                "count_details": {"normal": len(r.user_ids), "burst": 0},
                "me_burst": False,
                "burst_colors": [],
            }
            for r in message.reactions
            if r.user_ids
        ],
    }
    if channel.guild_id is not None:
        payload["guild_id"] = str(channel.guild_id)
    if message.reference is not None:
        payload["message_reference"] = dict(message.reference)
        referenced = backend.messages.get(int(message.reference["channel_id"]), {}).get(
            int(message.reference["message_id"])
        )
        if referenced is not None:
            payload["referenced_message"] = message_payload(backend, referenced)
    if message.interaction_metadata is not None:
        payload["interaction_metadata"] = dict(message.interaction_metadata)
    if message.webhook_id is not None:
        payload["webhook_id"] = str(message.webhook_id)
    if message.poll is not None:
        payload["poll"] = poll_payload(message.poll, for_user=for_user)
    return cast("message_types.Message", payload)


def _poll_media(text: str | None, emoji: str | None) -> dict[str, Any]:
    media: dict[str, Any] = {"text": text}
    if emoji is not None:
        media["emoji"] = emoji_payload(emoji)
    return media


def poll_payload(poll: Poll, *, for_user: int | None = None) -> dict[str, Any]:
    counts = [
        {
            "id": answer.answer_id,
            "count": len(poll.votes.get(answer.answer_id, set())),
            "me_voted": for_user is not None and for_user in poll.votes.get(answer.answer_id, set()),
        }
        for answer in poll.answers
    ]
    return {
        "question": _poll_media(poll.question, None),
        "answers": [
            {"answer_id": answer.answer_id, "poll_media": _poll_media(answer.text, answer.emoji)}
            for answer in poll.answers
        ],
        "expiry": poll.expiry,
        "allow_multiselect": poll.allow_multiselect,
        "layout_type": poll.layout_type,
        "results": {"is_finalized": poll.finalized, "answer_counts": counts},
    }


def guild_create_payload(backend: Backend, guild: Guild) -> guild_types.Guild:
    return cast(
        "guild_types.Guild",
        {
            "id": str(guild.id),
            "name": guild.name,
            "owner_id": str(guild.owner_id),
            "icon": None,
            "splash": None,
            "discovery_splash": None,
            "banner": None,
            "description": guild.description,
            "afk_channel_id": str(guild.afk_channel_id) if guild.afk_channel_id is not None else None,
            "afk_timeout": guild.afk_timeout,
            "verification_level": guild.verification_level,
            "default_message_notifications": guild.default_message_notifications,
            "explicit_content_filter": guild.explicit_content_filter,
            "mfa_level": 0,
            "nsfw_level": 0,
            "premium_tier": 0,
            "premium_subscription_count": 0,
            "preferred_locale": guild.preferred_locale,
            "system_channel_id": str(guild.system_channel_id)
            if guild.system_channel_id is not None
            else None,
            "system_channel_flags": 0,
            "rules_channel_id": None,
            "public_updates_channel_id": None,
            "vanity_url_code": None,
            "application_id": None,
            "max_members": 500000,
            "max_presences": None,
            "features": [],
            "emojis": [guild_emoji_payload(backend, e) for e in guild.emojis.values()],
            "stickers": [sticker_payload(backend, s) for s in guild.stickers.values()],
            "roles": [role_payload(r) for r in guild.roles.values()],
            "member_count": len(guild.members),
            "large": False,
            "unavailable": False,
            "joined_at": backend.now_iso(),
            "members": [member_payload(backend, guild, m) for m in guild.members.values()],
            "channels": [channel_payload(backend, backend.channels[cid]) for cid in guild.channel_ids],
            "threads": [thread_payload(backend, backend.channels[tid]) for tid in guild.thread_ids],
            "presences": [],
            "voice_states": [
                voice_state_payload(backend, v, with_member=False) for v in guild.voice_states.values()
            ],
            "stage_instances": [],
            "guild_scheduled_events": [
                scheduled_event_payload(backend, e) for e in guild.scheduled_events.values()
            ],
            "soundboard_sounds": [],
            "premium_progress_bar_enabled": False,
        },
    )


def webhook_payload(backend: Backend, webhook: Webhook, *, include_token: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(webhook.id),
        "type": 1,
        "channel_id": str(webhook.channel_id),
        "guild_id": str(webhook.guild_id) if webhook.guild_id else None,
        "name": webhook.name,
        "avatar": None,
        "application_id": None,
        "user": user_payload(backend.users[webhook.creator_id]),
    }
    if include_token:
        payload["token"] = webhook.token
    return payload


def audit_log_entry_payload(entry: AuditLogEntry) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "action_type": entry.action_type,
        "user_id": str(entry.user_id),
        "target_id": str(entry.target_id) if entry.target_id is not None else None,
        "reason": entry.reason,
        "changes": list(entry.changes),
        "options": dict(entry.options) or None,
    }


def audit_log_payload(backend: Backend, entries: list[AuditLogEntry]) -> dict[str, Any]:
    """A GET /audit-logs response: entries (newest first) plus referenced users."""
    user_ids: set[int] = set()
    for entry in entries:
        user_ids.add(entry.user_id)
        if entry.target_id is not None and entry.target_id in backend.users:
            user_ids.add(entry.target_id)
    return {
        "audit_log_entries": [audit_log_entry_payload(e) for e in reversed(entries)],
        "users": [user_payload(backend.users[uid]) for uid in user_ids if uid in backend.users],
        "integrations": [],
        "webhooks": [],
        "threads": [],
        "application_commands": [],
        "auto_moderation_rules": [],
        "guild_scheduled_events": [],
    }


def scheduled_event_payload(backend: Backend, event: ScheduledEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "guild_id": str(event.guild_id),
        "channel_id": str(event.channel_id) if event.channel_id is not None else None,
        "creator_id": str(event.creator_id),
        "name": event.name,
        "description": event.description,
        "scheduled_start_time": event.scheduled_start_time,
        "scheduled_end_time": event.scheduled_end_time,
        "privacy_level": event.privacy_level,
        "status": event.status,
        "entity_type": event.entity_type,
        "entity_id": None,
        "entity_metadata": event.entity_metadata,
        "creator": user_payload(backend.users[event.creator_id])
        if event.creator_id in backend.users
        else None,
        "user_count": len(event.user_ids),
        "image": None,
    }


def voice_state_payload(backend: Backend, state: VoiceState, *, with_member: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "guild_id": str(state.guild_id),
        "channel_id": str(state.channel_id) if state.channel_id is not None else None,
        "user_id": str(state.user_id),
        "session_id": state.session_id,
        "deaf": state.deaf,
        "mute": state.mute,
        "self_deaf": state.self_deaf,
        "self_mute": state.self_mute,
        "self_stream": state.self_stream,
        "self_video": state.self_video,
        "suppress": state.suppress,
        "request_to_speak_timestamp": state.request_to_speak_timestamp,
    }
    guild = backend.guilds.get(state.guild_id)
    if with_member and guild is not None and state.user_id in guild.members:
        payload["member"] = member_payload(backend, guild, guild.members[state.user_id])
    return payload


def invite_payload(backend: Backend, invite: Invite, *, with_inviter: bool = True) -> dict[str, Any]:
    channel = backend.channels.get(invite.channel_id)
    payload: dict[str, Any] = {
        "code": invite.code,
        "guild_id": str(invite.guild_id),
        "channel_id": str(invite.channel_id),
        "channel": {
            "id": str(invite.channel_id),
            "name": channel.name if channel else None,
            "type": channel.type if channel else 0,
        },
        "created_at": invite.created_at,
        "uses": invite.uses,
        "max_uses": invite.max_uses,
        "max_age": invite.max_age,
        "temporary": invite.temporary,
        "expires_at": invite.expires_at,
    }
    if with_inviter and invite.inviter_id in backend.users:
        payload["inviter"] = user_payload(backend.users[invite.inviter_id])
    return payload


def guild_emoji_payload(backend: Backend, emoji: GuildEmoji) -> dict[str, Any]:
    return {
        "id": str(emoji.id),
        "name": emoji.name,
        "roles": [str(r) for r in emoji.role_ids],
        "user": user_payload(backend.users[emoji.user_id]) if emoji.user_id in backend.users else None,
        "require_colons": True,
        "managed": emoji.managed,
        "animated": emoji.animated,
        "available": emoji.available,
    }


def sticker_payload(backend: Backend, sticker: Sticker) -> dict[str, Any]:
    return {
        "id": str(sticker.id),
        "name": sticker.name,
        "description": sticker.description,
        "tags": sticker.tags,
        "type": 2,  # GUILD
        "format_type": sticker.format_type,
        "guild_id": str(sticker.guild_id),
        "available": sticker.available,
        "user": user_payload(backend.users[sticker.user_id]) if sticker.user_id in backend.users else None,
    }


def auto_mod_rule_payload(backend: Backend, rule: AutoModRule) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "guild_id": str(rule.guild_id),
        "name": rule.name,
        "creator_id": str(rule.creator_id),
        "event_type": rule.event_type,
        "trigger_type": rule.trigger_type,
        "trigger_metadata": dict(rule.trigger_metadata),
        "actions": list(rule.actions),
        "enabled": rule.enabled,
        "exempt_roles": [str(r) for r in rule.exempt_roles],
        "exempt_channels": [str(c) for c in rule.exempt_channels],
    }
