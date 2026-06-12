"""Serialization of backend models into Discord wire-format payloads.

Return annotations reference discord.py's own ``discord.types`` TypedDicts —
the exact contract its parsers consume — so payload shape drift against the
installed discord.py version is caught by static type checking rather than at
users' test runtime. Some payloads are intentionally partial (Discord itself
omits keys per context), hence the casts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .models import Channel, Guild, Member, Message, Role, User, Webhook

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
    if channel.type == 1:  # DM
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
    return cast("message_types.Message", payload)


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
            "description": None,
            "afk_channel_id": None,
            "afk_timeout": 300,
            "verification_level": 0,
            "default_message_notifications": 0,
            "explicit_content_filter": 0,
            "mfa_level": 0,
            "nsfw_level": 0,
            "premium_tier": 0,
            "premium_subscription_count": 0,
            "preferred_locale": "en-US",
            "system_channel_id": None,
            "system_channel_flags": 0,
            "rules_channel_id": None,
            "public_updates_channel_id": None,
            "vanity_url_code": None,
            "application_id": None,
            "max_members": 500000,
            "max_presences": None,
            "features": [],
            "emojis": [],
            "stickers": [],
            "roles": [role_payload(r) for r in guild.roles.values()],
            "member_count": len(guild.members),
            "large": False,
            "unavailable": False,
            "joined_at": backend.now_iso(),
            "members": [member_payload(backend, guild, m) for m in guild.members.values()],
            "channels": [channel_payload(backend, backend.channels[cid]) for cid in guild.channel_ids],
            "threads": [thread_payload(backend, backend.channels[tid]) for tid in guild.thread_ids],
            "presences": [],
            "voice_states": [],
            "stage_instances": [],
            "guild_scheduled_events": [],
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
