"""The Backend: single in-memory source of truth for the virtual Discord.

REST handlers and gateway events are two projections of this one store: every
mutation that real Discord would announce over the gateway is broadcast to all
attached clients, so the bot's cache stays consistent with REST responses —
including for the bot's own actions.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import discord

from ..enums import AppCommandType, ChannelType, MessageType
from . import errors, permissions, serializers
from .cdn import CdnStore
from .models import (
    AuditLogEntry,
    AutoModRule,
    Channel,
    Guild,
    GuildEmoji,
    Interaction,
    Invite,
    Member,
    Message,
    Overwrite,
    Poll,
    Reaction,
    Role,
    ScheduledEvent,
    Sticker,
    ThreadMetadata,
    User,
    VoiceState,
    Webhook,
)

# Fixed virtual clock epoch (2026-01-01 UTC): reproducible snowflakes/timestamps.
_VIRTUAL_EPOCH_MS = 1767225600000
_DISCORD_EPOCH_MS = 1420070400000

#: Default permissions for a fresh guild's @everyone role.
DEFAULT_EVERYONE_PERMISSIONS = discord.Permissions(
    view_channel=True,
    send_messages=True,
    send_messages_in_threads=True,
    read_message_history=True,
    add_reactions=True,
    embed_links=True,
    attach_files=True,
    external_emojis=True,
    change_nickname=True,
    connect=True,
    speak=True,
    use_application_commands=True,
    create_public_threads=True,
).value

_USER_MENTION = re.compile(r"<@!?(\d+)>")
_ROLE_MENTION = re.compile(r"<@&(\d+)>")

EventListener = Callable[[str, Mapping[str, Any]], None]


class Backend:
    def __init__(self) -> None:
        self._counter = 0
        self._clock_offset_ms = 0
        self.users: dict[int, User] = {}
        self.guilds: dict[int, Guild] = {}
        self.channels: dict[int, Channel] = {}
        self.messages: dict[int, dict[int, Message]] = {}
        self.webhooks: dict[int, Webhook] = {}
        self.webhook_tokens: dict[str, int] = {}
        self.dm_channels: dict[int, int] = {}  # user id -> channel id
        self.invites: dict[str, Invite] = {}  # code -> invite
        self.commands: dict[
            int | None, dict[tuple[str, int], dict[str, Any]]
        ] = {}  # scope -> (name, type) -> command
        self.interactions: dict[int, Interaction] = {}
        self.interaction_tokens: dict[str, int] = {}
        self.cdn = CdnStore()
        self.subscribers: list[EventListener] = []
        self.http_log: list[tuple[str, str, dict[str, Any] | None]] = []
        #: Interleaved record of everything that crossed either seam, in order:
        #: ("HTTP", "METHOD /path", body) and ("GATEWAY", event, payload).
        self.transcript: list[tuple[str, str, Any]] = []
        self.faults: list[dict[str, Any]] = []
        self.application_id: int = self.snowflake()
        self.bot_user: User = self.make_user("TestBot", bot=True)

    # ------------------------------------------------------------------ core

    def snowflake(self) -> int:
        """Deterministic, monotonic snowflakes with valid embedded timestamps."""
        self._counter += 1
        ms = _VIRTUAL_EPOCH_MS - _DISCORD_EPOCH_MS + self._clock_offset_ms + self._counter
        return (ms << 22) | (self._counter % 4096)

    def now_iso(self) -> str:
        """The current virtual time as an ISO timestamp.

        Driven by the same virtual clock as :meth:`snowflake` (epoch + counter
        ms) rather than the wall clock, so a message's ``created_at`` (which
        discord.py derives from its snowflake) and its serialized ``timestamp``
        agree, and timestamps stay deterministic across runs.
        """
        ms = _VIRTUAL_EPOCH_MS + self._clock_offset_ms + self._counter
        return datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC).isoformat()

    def iso_after(self, seconds: float) -> str:
        """An ISO timestamp ``seconds`` into the virtual future (for expiries)."""
        ms = _VIRTUAL_EPOCH_MS + self._clock_offset_ms + self._counter + int(seconds * 1000)
        return datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC).isoformat()

    def advance_clock(self, seconds: float) -> None:
        """Advance the virtual wall clock (snowflake timestamps, now_iso).

        Cooldowns and other age math in discord.py are computed from message/
        interaction timestamps, so fast-forwarding time must move this clock as
        well as the event loop's (:meth:`Env.advance_time` does both).
        """
        self._clock_offset_ms += int(seconds * 1000)

    def emit(self, event: str, payload: Mapping[str, Any]) -> None:
        self.transcript.append(("GATEWAY", event, payload))
        for listener in self.subscribers:
            listener(event, payload)

    # ----------------------------------------------------------------- users

    def make_user(self, name: str, *, bot: bool = False) -> User:
        user = User(id=self.snowflake(), name=name, bot=bot)
        self.users[user.id] = user
        return user

    def get_user(self, user_id: int) -> User:
        try:
            return self.users[user_id]
        except KeyError:
            raise errors.unknown_user() from None

    # ---------------------------------------------------------------- guilds

    def create_guild(self, name: str, *, id: int | None = None, owner_id: int | None = None) -> Guild:
        guild_id = id if id is not None else self.snowflake()
        if owner_id is None:
            # A synthetic owner: the bot must never own guilds by default,
            # since owners bypass every permission check.
            owner_id = self.make_user(f"{name} Owner").id
        guild = Guild(id=guild_id, name=name, owner_id=owner_id)
        guild.roles[guild_id] = Role(
            id=guild_id, name="@everyone", permissions=DEFAULT_EVERYONE_PERMISSIONS, position=0
        )
        self.guilds[guild_id] = guild
        # The bot is always a member of guilds it can see. It gets a managed
        # integration role with broad permissions — like a typical bot invite —
        # but not administrator, so channel overwrites still apply to it.
        bot_permissions = discord.Permissions.all()
        bot_permissions.administrator = False
        bot_role = Role(
            id=self.snowflake(),
            name=self.bot_user.name,
            permissions=bot_permissions.value,
            position=1,
            managed=True,
        )
        guild.roles[bot_role.id] = bot_role
        self.add_member(guild_id, self.bot_user.id, roles=[bot_role.id])
        self.emit("GUILD_CREATE", serializers.guild_create_payload(self, guild))
        return guild

    def get_guild(self, guild_id: int) -> Guild:
        try:
            return self.guilds[guild_id]
        except KeyError:
            raise errors.unknown_guild() from None

    def edit_guild(self, guild_id: int, changes: Mapping[str, Any]) -> Guild:
        """Apply validated field changes to a guild and announce the update."""
        guild = self.get_guild(guild_id)
        for attr, value in changes.items():
            setattr(guild, attr, value)
        self.emit("GUILD_UPDATE", serializers.guild_create_payload(self, guild))
        return guild

    # --------------------------------------------------------------- members

    def add_member(
        self,
        guild_id: int,
        user_id: int,
        *,
        roles: Iterable[int] = (),
        nick: str | None = None,
        announce: bool = False,
    ) -> Member:
        guild = self.get_guild(guild_id)
        member = Member(user_id=user_id, role_ids=list(roles), nick=nick, joined_at=self.now_iso())
        guild.members[user_id] = member
        if announce:
            payload = dict(serializers.member_payload(self, guild, member))
            payload["guild_id"] = str(guild_id)
            self.emit("GUILD_MEMBER_ADD", payload)
        return member

    def get_member(self, guild_id: int, user_id: int) -> Member:
        member = self.get_guild(guild_id).members.get(user_id)
        if member is None:
            raise errors.unknown_member()
        return member

    def remove_member(self, guild_id: int, user_id: int) -> None:
        guild = self.get_guild(guild_id)
        if user_id not in guild.members:
            raise errors.unknown_member()
        del guild.members[user_id]
        self.emit(
            "GUILD_MEMBER_REMOVE",
            {"guild_id": str(guild_id), "user": serializers.user_payload(self.get_user(user_id))},
        )

    def announce_member_update(self, guild_id: int, user_id: int) -> None:
        guild = self.get_guild(guild_id)
        payload = dict(serializers.member_payload(self, guild, guild.members[user_id]))
        payload["guild_id"] = str(guild_id)
        self.emit("GUILD_MEMBER_UPDATE", payload)

    def edit_member(self, guild_id: int, user_id: int, changes: Mapping[str, Any]) -> Member:
        """Apply validated field changes (nick/roles/timeout/…) and announce the update.

        ``changes`` maps :class:`Member` attribute names to values; callers
        validate permissions and hierarchy before calling, so this only writes
        and emits — keeping the mutation and its GUILD_MEMBER_UPDATE atomic.
        """
        member = self.get_member(guild_id, user_id)
        for attr, value in changes.items():
            setattr(member, attr, value)
        self.announce_member_update(guild_id, user_id)
        return member

    def add_member_role(self, guild_id: int, user_id: int, role_id: int) -> None:
        """Give a member a role (if absent) and announce the update."""
        member = self.get_member(guild_id, user_id)
        if role_id not in member.role_ids:
            member.role_ids.append(role_id)
        self.announce_member_update(guild_id, user_id)

    def remove_member_role(self, guild_id: int, user_id: int, role_id: int) -> None:
        """Take a role from a member (if present) and announce the update."""
        member = self.get_member(guild_id, user_id)
        if role_id in member.role_ids:
            member.role_ids.remove(role_id)
        self.announce_member_update(guild_id, user_id)

    # ----------------------------------------------------------------- roles

    def create_role(self, guild_id: int, name: str, *, permissions: int = 0, **fields: Any) -> Role:
        guild = self.get_guild(guild_id)
        position = fields.pop("position", None)
        if position is None:
            # New roles insert just above @everyone, pushing existing roles up
            # (so the bot's integration role stays on top), as on real Discord.
            position = 1
            for existing in guild.roles.values():
                if existing.position >= 1:
                    existing.position += 1
        role = Role(
            id=self.snowflake(),
            name=name,
            permissions=permissions,
            position=position,
            **fields,
        )
        guild.roles[role.id] = role
        self.emit(
            "GUILD_ROLE_CREATE",
            {"guild_id": str(guild_id), "role": serializers.role_payload(role)},
        )
        return role

    def get_role(self, guild_id: int, role_id: int) -> Role:
        role = self.get_guild(guild_id).roles.get(role_id)
        if role is None:
            raise errors.unknown_role()
        return role

    def edit_role(self, guild_id: int, role_id: int, changes: Mapping[str, Any]) -> Role:
        """Apply validated field changes to a role and announce the update."""
        role = self.get_role(guild_id, role_id)
        for attr, value in changes.items():
            setattr(role, attr, value)
        self.emit("GUILD_ROLE_UPDATE", {"guild_id": str(guild_id), "role": serializers.role_payload(role)})
        return role

    def delete_role(self, guild_id: int, role_id: int) -> None:
        guild = self.get_guild(guild_id)
        self.get_role(guild_id, role_id)
        if role_id == guild_id:  # the @everyone role shares the guild id and can't be deleted
            raise errors.invalid_form_body("Cannot delete the @everyone role")
        del guild.roles[role_id]
        for member in guild.members.values():
            if role_id in member.role_ids:
                member.role_ids.remove(role_id)
        self.emit("GUILD_ROLE_DELETE", {"guild_id": str(guild_id), "role_id": str(role_id)})

    # -------------------------------------------------------------- channels

    def create_channel(
        self,
        guild_id: int | None,
        name: str | None,
        *,
        type: int = ChannelType.TEXT,
        overwrites: list[Overwrite] | None = None,
        announce: bool = True,
        **fields: Any,
    ) -> Channel:
        channel = Channel(
            id=self.snowflake(),
            type=type,
            name=name,
            guild_id=guild_id,
            overwrites=overwrites or [],
            **fields,
        )
        self.channels[channel.id] = channel
        self.messages[channel.id] = {}
        if guild_id is not None:
            guild = self.get_guild(guild_id)
            channel.position = len(guild.channel_ids)
            guild.channel_ids.append(channel.id)
            if announce:
                self.emit("CHANNEL_CREATE", serializers.channel_payload(self, channel))
        return channel

    def get_channel(self, channel_id: int) -> Channel:
        try:
            return self.channels[channel_id]
        except KeyError:
            raise errors.unknown_channel() from None

    def delete_channel(self, channel_id: int) -> None:
        channel = self.get_channel(channel_id)
        payload = serializers.channel_payload(self, channel)
        del self.channels[channel_id]
        self.messages.pop(channel_id, None)
        if channel.guild_id is not None:
            guild = self.get_guild(channel.guild_id)
            if channel.id in guild.channel_ids:
                guild.channel_ids.remove(channel.id)
            if channel.id in guild.thread_ids:
                guild.thread_ids.remove(channel.id)
        self.emit("THREAD_DELETE" if channel.is_thread else "CHANNEL_DELETE", dict(payload))

    def announce_channel_update(self, channel_id: int) -> None:
        channel = self.get_channel(channel_id)
        self.emit("CHANNEL_UPDATE", serializers.channel_payload(self, channel))

    def edit_channel(
        self, channel_id: int, changes: Mapping[str, Any], *, overwrites: list[Overwrite] | None = None
    ) -> Channel:
        """Apply field/overwrite changes to a channel and announce the update."""
        channel = self.get_channel(channel_id)
        for attr, value in changes.items():
            setattr(channel, attr, value)
        if overwrites is not None:
            channel.overwrites = overwrites
        self.announce_channel_update(channel.id)
        return channel

    def set_overwrite(self, channel_id: int, overwrite: Overwrite) -> None:
        """Add or replace a single permission overwrite and announce the update."""
        channel = self.get_channel(channel_id)
        channel.overwrites = [o for o in channel.overwrites if o.target_id != overwrite.target_id]
        channel.overwrites.append(overwrite)
        self.announce_channel_update(channel.id)

    def delete_overwrite(self, channel_id: int, target_id: int) -> None:
        """Remove a permission overwrite and announce the update."""
        channel = self.get_channel(channel_id)
        channel.overwrites = [o for o in channel.overwrites if o.target_id != target_id]
        self.announce_channel_update(channel.id)

    def get_dm_channel(self, user_id: int) -> Channel:
        self.get_user(user_id)
        channel_id = self.dm_channels.get(user_id)
        if channel_id is not None:
            return self.channels[channel_id]
        channel = self.create_channel(None, None, type=ChannelType.DM)
        channel.recipient_ids = [user_id]
        self.dm_channels[user_id] = channel.id
        return channel

    def create_thread(
        self,
        parent_id: int,
        name: str,
        owner_id: int,
        *,
        type: int = ChannelType.PUBLIC_THREAD,
        auto_archive_duration: int = 1440,
        message_id: int | None = None,
        applied_tags: Iterable[int] = (),
    ) -> Channel:
        parent = self.get_channel(parent_id)
        guild = self.get_guild(parent.guild_id)  # type: ignore[arg-type]
        now = self.now_iso()
        thread = Channel(
            id=message_id or self.snowflake(),
            type=type,
            name=name,
            guild_id=parent.guild_id,
            parent_id=parent_id,
            owner_id=owner_id,
            applied_tags=list(applied_tags),
            thread_metadata=ThreadMetadata(
                auto_archive_duration=auto_archive_duration, archive_timestamp=now, create_timestamp=now
            ),
        )
        self.channels[thread.id] = thread
        self.messages.setdefault(thread.id, {})
        guild.thread_ids.append(thread.id)
        payload = dict(serializers.thread_payload(self, thread))
        payload["newly_created"] = True
        self.emit("THREAD_CREATE", payload)
        return thread

    # -------------------------------------------------------------- messages

    def create_message(
        self,
        channel_id: int,
        author_id: int,
        content: str = "",
        *,
        embeds: list[dict[str, Any]] | None = None,
        components: list[dict[str, Any]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        flags: int = 0,
        reference: dict[str, Any] | None = None,
        interaction_metadata: dict[str, Any] | None = None,
        webhook_id: int | None = None,
        poll: Poll | None = None,
        broadcast: bool = True,
    ) -> Message:
        channel = self.get_channel(channel_id)
        if content and len(content) > 2000:
            raise errors.invalid_form_body("content must be 2000 or fewer in length")
        if self._auto_mod_blocks(channel, author_id, content or ""):
            # Blocked by an auto-moderation rule: the execution event already
            # fired; build the message object for the caller but never store or
            # broadcast it, so it appears nowhere — exactly as on real Discord.
            return Message(
                id=self.snowflake(), channel_id=channel_id, author_id=author_id, content=content or ""
            )
        message = Message(
            id=self.snowflake(),
            channel_id=channel_id,
            author_id=author_id,
            content=content or "",
            timestamp=self.now_iso(),
            type=MessageType.REPLY if reference else MessageType.DEFAULT,
            flags=flags,
            embeds=embeds or [],
            components=components or [],
            attachments=attachments or [],
            reference=reference,
            interaction_metadata=interaction_metadata,
            webhook_id=webhook_id,
            poll=poll,
            mention_user_ids=[int(m) for m in _USER_MENTION.findall(content or "")],
            mention_role_ids=[int(m) for m in _ROLE_MENTION.findall(content or "")],
            mention_everyone=self._mentions_everyone(channel, author_id, content or ""),
        )
        self.messages[channel_id][message.id] = message
        channel.last_message_id = message.id
        if channel.is_thread:
            channel.message_count += 1
        if broadcast:
            payload = dict(serializers.message_payload(self, message))
            if channel.guild_id is not None:
                guild = self.guilds[channel.guild_id]
                if author_id in guild.members:
                    payload["member"] = serializers.member_payload(
                        self, guild, guild.members[author_id], with_user=False
                    )
            self.emit("MESSAGE_CREATE", payload)
        return message

    def _mentions_everyone(self, channel: Channel, author_id: int, content: str) -> bool:
        """An @everyone only actually pings if the author may mention everyone."""
        if "@everyone" not in content and "@here" not in content:
            return False
        if channel.guild_id is None:
            return False
        perms = self.compute_permissions(channel.guild_id, author_id, channel.id)
        return bool(perms & permissions.flag("mention_everyone"))

    def get_message(self, channel_id: int, message_id: int) -> Message:
        message = self.messages.get(channel_id, {}).get(message_id)
        if message is None:
            raise errors.unknown_message()
        return message

    def edit_message(self, channel_id: int, message_id: int, fields: dict[str, Any]) -> Message:
        message = self.get_message(channel_id, message_id)
        if "content" in fields and fields["content"] is not None:
            message.content = fields["content"]
        for key in ("embeds", "components", "attachments"):
            if key in fields and fields[key] is not None:
                setattr(message, key, fields[key])
        if "flags" in fields and fields["flags"] is not None:
            message.flags = int(fields["flags"])
        message.edited_timestamp = self.now_iso()
        self.emit("MESSAGE_UPDATE", dict(serializers.message_payload(self, message)))
        return message

    def delete_message(self, channel_id: int, message_id: int) -> None:
        self.get_message(channel_id, message_id)
        del self.messages[channel_id][message_id]
        channel = self.get_channel(channel_id)
        payload: dict[str, Any] = {"id": str(message_id), "channel_id": str(channel_id)}
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit("MESSAGE_DELETE", payload)

    def bulk_delete_messages(self, channel_id: int, message_ids: Iterable[int]) -> list[int]:
        """Delete several messages at once, announcing a single MESSAGE_DELETE_BULK.

        Ids that are not present are skipped (real Discord tolerates this), and
        only the ids that actually existed are reported and announced — so the
        bot's cache and the returned set agree.
        """
        channel = self.get_channel(channel_id)
        store = self.messages.get(channel_id, {})
        deleted = [mid for mid in message_ids if store.pop(mid, None) is not None]
        payload: dict[str, Any] = {
            "ids": [str(mid) for mid in deleted],
            "channel_id": str(channel_id),
        }
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit("MESSAGE_DELETE_BULK", payload)
        return deleted

    def set_pinned(self, channel_id: int, message_id: int, pinned: bool) -> None:
        message = self.get_message(channel_id, message_id)
        message.pinned = pinned
        channel = self.get_channel(channel_id)
        payload: dict[str, Any] = {"channel_id": str(channel_id), "last_pin_timestamp": self.now_iso()}
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit("CHANNEL_PINS_UPDATE", payload)

    # ------------------------------------------------------------- reactions

    def add_reaction(self, channel_id: int, message_id: int, emoji: str, user_id: int) -> None:
        message = self.get_message(channel_id, message_id)
        reaction = message.reaction_for(emoji)
        if reaction is None:
            reaction = Reaction(emoji=emoji)
            message.reactions.append(reaction)
        if user_id in reaction.user_ids:
            return
        reaction.user_ids.append(user_id)
        self._emit_reaction("MESSAGE_REACTION_ADD", message, emoji, user_id)

    def remove_reaction(self, channel_id: int, message_id: int, emoji: str, user_id: int) -> None:
        message = self.get_message(channel_id, message_id)
        reaction = message.reaction_for(emoji)
        if reaction is None or user_id not in reaction.user_ids:
            raise errors.unknown_message()
        reaction.user_ids.remove(user_id)
        if not reaction.user_ids:
            message.reactions.remove(reaction)
        self._emit_reaction("MESSAGE_REACTION_REMOVE", message, emoji, user_id)

    def clear_reactions(self, channel_id: int, message_id: int) -> None:
        """Remove every reaction from a message (MESSAGE_REACTION_REMOVE_ALL)."""
        message = self.get_message(channel_id, message_id)
        message.reactions.clear()
        channel = self.get_channel(channel_id)
        payload: dict[str, Any] = {"channel_id": str(channel_id), "message_id": str(message_id)}
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit("MESSAGE_REACTION_REMOVE_ALL", payload)

    def clear_reaction(self, channel_id: int, message_id: int, emoji: str) -> None:
        """Remove all of one emoji's reactions from a message (MESSAGE_REACTION_REMOVE_EMOJI)."""
        message = self.get_message(channel_id, message_id)
        reaction = message.reaction_for(emoji)
        if reaction is not None:
            message.reactions.remove(reaction)
        channel = self.get_channel(channel_id)
        payload: dict[str, Any] = {
            "channel_id": str(channel_id),
            "message_id": str(message_id),
            "emoji": serializers.emoji_payload(emoji),
        }
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit("MESSAGE_REACTION_REMOVE_EMOJI", payload)

    def _emit_reaction(self, event: str, message: Message, emoji: str, user_id: int) -> None:
        channel = self.get_channel(message.channel_id)
        payload: dict[str, Any] = {
            "user_id": str(user_id),
            "channel_id": str(message.channel_id),
            "message_id": str(message.id),
            "emoji": serializers.emoji_payload(emoji),
            "burst": False,
            "type": 0,
        }
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
            guild = self.guilds[channel.guild_id]
            if event == "MESSAGE_REACTION_ADD" and user_id in guild.members:
                payload["member"] = serializers.member_payload(self, guild, guild.members[user_id])
        self.emit(event, payload)

    # -------------------------------------------------------------- webhooks

    def create_webhook(self, channel_id: int, name: str, creator_id: int) -> Webhook:
        channel = self.get_channel(channel_id)
        webhook_id = self.snowflake()
        webhook_user = self.make_user(name, bot=True)
        webhook = Webhook(
            id=webhook_id,
            token=f"simcord_webhook_{webhook_id}",
            channel_id=channel_id,
            guild_id=channel.guild_id,
            name=name,
            creator_id=creator_id,
            webhook_user_id=webhook_user.id,
        )
        self.webhooks[webhook.id] = webhook
        self.webhook_tokens[webhook.token] = webhook.id
        return webhook

    def get_webhook(self, webhook_id: int) -> Webhook:
        webhook = self.webhooks.get(webhook_id)
        if webhook is None:
            raise errors.unknown_webhook()
        return webhook

    def edit_webhook(self, webhook_id: int, changes: Mapping[str, Any]) -> Webhook:
        """Edit a webhook's name and/or channel, announcing WEBHOOKS_UPDATE."""
        webhook = self.get_webhook(webhook_id)
        if changes.get("name") is not None:
            webhook.name = changes["name"]
        if changes.get("channel_id") is not None:
            webhook.channel_id = int(changes["channel_id"])
        self._emit_webhooks_update(webhook.channel_id)
        return webhook

    def delete_webhook(self, webhook_id: int) -> None:
        webhook = self.get_webhook(webhook_id)
        del self.webhooks[webhook_id]
        self.webhook_tokens.pop(webhook.token, None)
        self._emit_webhooks_update(webhook.channel_id)

    def _emit_webhooks_update(self, channel_id: int) -> None:
        channel = self.get_channel(channel_id)
        payload: dict[str, Any] = {"channel_id": str(channel_id)}
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit("WEBHOOKS_UPDATE", payload)

    # ------------------------------------------------------------ audit logs

    def record_audit_log(
        self,
        guild_id: int,
        action_type: int,
        *,
        target_id: int | None = None,
        changes: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
        reason: str | None = None,
        user_id: int | None = None,
    ) -> AuditLogEntry:
        """Record a privileged action in the guild's audit log and announce it.

        Called from route handlers (the API-call path), never from the backend
        mutation methods themselves — so omnipotent test/builder setup does not
        generate audit entries, exactly as real Discord only logs API actions.
        """
        guild = self.get_guild(guild_id)
        entry = AuditLogEntry(
            id=self.snowflake(),
            action_type=int(action_type),
            user_id=user_id if user_id is not None else self.bot_user.id,
            target_id=target_id,
            reason=reason,
            changes=list(changes or []),
            options=dict(options or {}),
        )
        guild.audit_log_entries.append(entry)
        payload = dict(serializers.audit_log_entry_payload(entry))
        payload["guild_id"] = str(guild_id)
        self.emit("GUILD_AUDIT_LOG_ENTRY_CREATE", payload)
        return entry

    # ------------------------------------------------------------ poll voting

    def add_poll_vote(self, channel_id: int, message_id: int, answer_id: int, user_id: int) -> None:
        message = self.get_message(channel_id, message_id)
        poll = message.poll
        if poll is None or poll.answer(answer_id) is None:
            raise errors.invalid_form_body("poll answer does not exist")
        if not poll.allow_multiselect:
            for other_id, voters in poll.votes.items():
                if other_id != answer_id and user_id in voters:
                    voters.discard(user_id)
                    self._emit_poll_vote("MESSAGE_POLL_VOTE_REMOVE", message, other_id, user_id)
        voters = poll.votes.setdefault(answer_id, set())
        if user_id in voters:
            return
        voters.add(user_id)
        self._emit_poll_vote("MESSAGE_POLL_VOTE_ADD", message, answer_id, user_id)

    def remove_poll_vote(self, channel_id: int, message_id: int, answer_id: int, user_id: int) -> None:
        message = self.get_message(channel_id, message_id)
        poll = message.poll
        if poll is None:
            raise errors.invalid_form_body("message has no poll")
        voters = poll.votes.get(answer_id, set())
        if user_id not in voters:
            return
        voters.discard(user_id)
        self._emit_poll_vote("MESSAGE_POLL_VOTE_REMOVE", message, answer_id, user_id)

    def _emit_poll_vote(self, event: str, message: Message, answer_id: int, user_id: int) -> None:
        channel = self.get_channel(message.channel_id)
        payload: dict[str, Any] = {
            "user_id": str(user_id),
            "channel_id": str(message.channel_id),
            "message_id": str(message.id),
            "answer_id": answer_id,
        }
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit(event, payload)

    def expire_poll(self, channel_id: int, message_id: int) -> Message:
        message = self.get_message(channel_id, message_id)
        if message.poll is not None and not message.poll.finalized:
            message.poll.finalized = True
            self.emit("MESSAGE_UPDATE", dict(serializers.message_payload(self, message)))
        return message

    def expire_due_polls(self) -> None:
        """Finalize any polls whose expiry has passed (driven by the virtual clock)."""
        now = datetime.datetime.fromisoformat(self.now_iso())
        for channel_messages in self.messages.values():
            for message in channel_messages.values():
                poll = message.poll
                if poll is None or poll.finalized:
                    continue
                if datetime.datetime.fromisoformat(poll.expiry) <= now:
                    self.expire_poll(message.channel_id, message.id)

    # ------------------------------------------------------- scheduled events

    def create_scheduled_event(
        self,
        guild_id: int,
        *,
        name: str,
        entity_type: int,
        scheduled_start_time: str,
        creator_id: int | None = None,
        channel_id: int | None = None,
        description: str | None = None,
        scheduled_end_time: str | None = None,
        entity_metadata: dict[str, str] | None = None,
    ) -> ScheduledEvent:
        guild = self.get_guild(guild_id)
        event = ScheduledEvent(
            id=self.snowflake(),
            guild_id=guild_id,
            name=name,
            creator_id=creator_id if creator_id is not None else self.bot_user.id,
            scheduled_start_time=scheduled_start_time,
            entity_type=entity_type,
            channel_id=channel_id,
            description=description,
            scheduled_end_time=scheduled_end_time,
            entity_metadata=entity_metadata,
        )
        guild.scheduled_events[event.id] = event
        self.emit("GUILD_SCHEDULED_EVENT_CREATE", serializers.scheduled_event_payload(self, event))
        return event

    def get_scheduled_event(self, guild_id: int, event_id: int) -> ScheduledEvent:
        event = self.get_guild(guild_id).scheduled_events.get(event_id)
        if event is None:
            raise errors.unknown_scheduled_event()
        return event

    def edit_scheduled_event(
        self, guild_id: int, event_id: int, changes: Mapping[str, Any]
    ) -> ScheduledEvent:
        event = self.get_scheduled_event(guild_id, event_id)
        for attr, value in changes.items():
            setattr(event, attr, value)
        self.emit("GUILD_SCHEDULED_EVENT_UPDATE", serializers.scheduled_event_payload(self, event))
        return event

    def delete_scheduled_event(self, guild_id: int, event_id: int) -> None:
        guild = self.get_guild(guild_id)
        event = self.get_scheduled_event(guild_id, event_id)
        del guild.scheduled_events[event_id]
        self.emit("GUILD_SCHEDULED_EVENT_DELETE", serializers.scheduled_event_payload(self, event))

    def activate_due_scheduled_events(self) -> None:
        """Auto-transition scheduled events whose start/end times have passed.

        1 (scheduled) -> 2 (active) at the start time, then 2 -> 3 (completed)
        at the end time (when one is set) — Discord's automatic lifecycle,
        driven by the virtual clock so ``advance_time`` carries events forward
        like real time. Manual status edits via ``PATCH`` still work too.
        """
        now = datetime.datetime.fromisoformat(self.now_iso())
        for guild in list(self.guilds.values()):
            for event in list(guild.scheduled_events.values()):
                if event.status == 1 and self._event_time_passed(event.scheduled_start_time, now):
                    self.edit_scheduled_event(guild.id, event.id, {"status": 2})
                if (
                    event.status == 2
                    and event.scheduled_end_time is not None
                    and self._event_time_passed(event.scheduled_end_time, now)
                ):
                    self.edit_scheduled_event(guild.id, event.id, {"status": 3})

    @staticmethod
    def _event_time_passed(iso: str, now: datetime.datetime) -> bool:
        moment = datetime.datetime.fromisoformat(iso)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.UTC)
        return moment <= now

    def set_scheduled_event_subscription(
        self, guild_id: int, event_id: int, user_id: int, subscribed: bool
    ) -> None:
        event = self.get_scheduled_event(guild_id, event_id)
        if subscribed:
            event.user_ids.add(user_id)
        else:
            event.user_ids.discard(user_id)
        self.emit(
            "GUILD_SCHEDULED_EVENT_USER_ADD" if subscribed else "GUILD_SCHEDULED_EVENT_USER_REMOVE",
            {
                "guild_scheduled_event_id": str(event_id),
                "user_id": str(user_id),
                "guild_id": str(guild_id),
            },
        )

    # ------------------------------------------------------------ voice state

    def set_voice_state(
        self, guild_id: int, user_id: int, channel_id: int | None, **flags: Any
    ) -> VoiceState | None:
        """Upsert a member's voice state (or disconnect when ``channel_id`` is None).

        Disconnecting a member who is not connected is a no-op (no event), so a
        stray ``leave_voice`` does not emit a spurious VOICE_STATE_UPDATE.
        """
        guild = self.get_guild(guild_id)
        state = guild.voice_states.get(user_id)
        if state is None and channel_id is None:
            return None
        if state is None:
            state = VoiceState(
                user_id=user_id,
                guild_id=guild_id,
                channel_id=channel_id,
                session_id=f"session_{user_id}",
            )
        state.channel_id = channel_id
        for attr, value in flags.items():
            setattr(state, attr, value)
        if channel_id is None:
            guild.voice_states.pop(user_id, None)
        else:
            guild.voice_states[user_id] = state
        self.emit("VOICE_STATE_UPDATE", serializers.voice_state_payload(self, state))
        return state

    # ---------------------------------------------------------------- invites

    def create_invite(
        self,
        channel_id: int,
        inviter_id: int,
        *,
        max_uses: int = 0,
        max_age: int = 86400,
        temporary: bool = False,
    ) -> Invite:
        channel = self.get_channel(channel_id)
        invite = Invite(
            code=f"sc{self.snowflake() % 100000000:08d}",
            guild_id=channel.guild_id,  # type: ignore[arg-type]
            channel_id=channel_id,
            inviter_id=inviter_id,
            created_at=self.now_iso(),
            max_uses=max_uses,
            max_age=max_age,
            temporary=temporary,
            expires_at=self.iso_after(max_age) if max_age else None,
        )
        self.invites[invite.code] = invite
        self.emit("INVITE_CREATE", serializers.invite_payload(self, invite, with_inviter=True))
        return invite

    def get_invite(self, code: str) -> Invite:
        invite = self.invites.get(code)
        if invite is None:
            raise errors.unknown_invite()
        return invite

    def delete_invite(self, code: str) -> Invite:
        invite = self.get_invite(code)
        del self.invites[code]
        self.emit(
            "INVITE_DELETE",
            {
                "code": code,
                "channel_id": str(invite.channel_id),
                "guild_id": str(invite.guild_id),
            },
        )
        return invite

    # ------------------------------------------------------- emojis / stickers

    def create_emoji(
        self, guild_id: int, name: str, user_id: int, *, animated: bool = False, role_ids: Iterable[int] = ()
    ) -> GuildEmoji:
        guild = self.get_guild(guild_id)
        emoji = GuildEmoji(
            id=self.snowflake(), name=name, user_id=user_id, animated=animated, role_ids=list(role_ids)
        )
        guild.emojis[emoji.id] = emoji
        self._emit_emojis_update(guild_id)
        return emoji

    def get_emoji(self, guild_id: int, emoji_id: int) -> GuildEmoji:
        emoji = self.get_guild(guild_id).emojis.get(emoji_id)
        if emoji is None:
            raise errors.unknown_emoji()
        return emoji

    def edit_emoji(self, guild_id: int, emoji_id: int, changes: Mapping[str, Any]) -> GuildEmoji:
        emoji = self.get_emoji(guild_id, emoji_id)
        for attr, value in changes.items():
            setattr(emoji, attr, value)
        self._emit_emojis_update(guild_id)
        return emoji

    def delete_emoji(self, guild_id: int, emoji_id: int) -> None:
        guild = self.get_guild(guild_id)
        self.get_emoji(guild_id, emoji_id)
        del guild.emojis[emoji_id]
        self._emit_emojis_update(guild_id)

    def _emit_emojis_update(self, guild_id: int) -> None:
        guild = self.get_guild(guild_id)
        self.emit(
            "GUILD_EMOJIS_UPDATE",
            {
                "guild_id": str(guild_id),
                "emojis": [serializers.guild_emoji_payload(self, e) for e in guild.emojis.values()],
            },
        )

    def create_sticker(
        self, guild_id: int, name: str, user_id: int, *, description: str | None = None, tags: str = ""
    ) -> Sticker:
        guild = self.get_guild(guild_id)
        sticker = Sticker(
            id=self.snowflake(),
            name=name,
            guild_id=guild_id,
            user_id=user_id,
            description=description,
            tags=tags,
        )
        guild.stickers[sticker.id] = sticker
        self._emit_stickers_update(guild_id)
        return sticker

    def get_sticker(self, guild_id: int, sticker_id: int) -> Sticker:
        sticker = self.get_guild(guild_id).stickers.get(sticker_id)
        if sticker is None:
            raise errors.unknown_sticker()
        return sticker

    def edit_sticker(self, guild_id: int, sticker_id: int, changes: Mapping[str, Any]) -> Sticker:
        sticker = self.get_sticker(guild_id, sticker_id)
        for attr, value in changes.items():
            setattr(sticker, attr, value)
        self._emit_stickers_update(guild_id)
        return sticker

    def delete_sticker(self, guild_id: int, sticker_id: int) -> None:
        guild = self.get_guild(guild_id)
        self.get_sticker(guild_id, sticker_id)
        del guild.stickers[sticker_id]
        self._emit_stickers_update(guild_id)

    def _emit_stickers_update(self, guild_id: int) -> None:
        guild = self.get_guild(guild_id)
        self.emit(
            "GUILD_STICKERS_UPDATE",
            {
                "guild_id": str(guild_id),
                "stickers": [serializers.sticker_payload(self, s) for s in guild.stickers.values()],
            },
        )

    # ------------------------------------------------------- auto-moderation

    def create_auto_mod_rule(self, guild_id: int, creator_id: int, body: Mapping[str, Any]) -> AutoModRule:
        guild = self.get_guild(guild_id)
        rule = AutoModRule(
            id=self.snowflake(),
            guild_id=guild_id,
            name=body["name"],
            creator_id=creator_id,
            event_type=int(body["event_type"]),
            trigger_type=int(body["trigger_type"]),
            trigger_metadata=dict(body.get("trigger_metadata") or {}),
            actions=list(body.get("actions") or []),
            enabled=bool(body.get("enabled", True)),
            exempt_roles=[int(r) for r in body.get("exempt_roles") or []],
            exempt_channels=[int(c) for c in body.get("exempt_channels") or []],
        )
        guild.auto_mod_rules[rule.id] = rule
        self.emit("AUTO_MODERATION_RULE_CREATE", serializers.auto_mod_rule_payload(self, rule))
        return rule

    def get_auto_mod_rule(self, guild_id: int, rule_id: int) -> AutoModRule:
        rule = self.get_guild(guild_id).auto_mod_rules.get(rule_id)
        if rule is None:
            raise errors.unknown_auto_mod_rule()
        return rule

    def edit_auto_mod_rule(self, guild_id: int, rule_id: int, changes: Mapping[str, Any]) -> AutoModRule:
        rule = self.get_auto_mod_rule(guild_id, rule_id)
        for attr, value in changes.items():
            setattr(rule, attr, value)
        self.emit("AUTO_MODERATION_RULE_UPDATE", serializers.auto_mod_rule_payload(self, rule))
        return rule

    def delete_auto_mod_rule(self, guild_id: int, rule_id: int) -> None:
        guild = self.get_guild(guild_id)
        rule = self.get_auto_mod_rule(guild_id, rule_id)
        del guild.auto_mod_rules[rule_id]
        self.emit("AUTO_MODERATION_RULE_DELETE", serializers.auto_mod_rule_payload(self, rule))

    def _auto_mod_blocks(self, channel: Channel, author_id: int, content: str) -> bool:
        """Evaluate enabled keyword rules; emit executions; return whether to block.

        Only non-bot messages in guild channels are evaluated, and only when the
        guild actually has rules — so guilds without auto-mod see no behaviour
        change.
        """
        if channel.guild_id is None or author_id == self.bot_user.id or not content:
            return False
        guild = self.guilds.get(channel.guild_id)
        if guild is None or not guild.auto_mod_rules:
            return False
        member = guild.members.get(author_id)
        role_ids = set(member.role_ids) if member else set()
        blocked = False
        for rule in guild.auto_mod_rules.values():
            if not rule.enabled:
                continue
            if channel.id in rule.exempt_channels or role_ids & set(rule.exempt_roles):
                continue
            matched = self._auto_mod_match(rule, content)
            if matched is None:
                continue
            for action in rule.actions:
                self._emit_auto_mod_execution(rule, channel, author_id, content, matched, action)
                if int(action.get("type", 0)) == 1:  # BLOCK_MESSAGE
                    blocked = True
        return blocked

    def _auto_mod_match(self, rule: AutoModRule, content: str) -> str | None:
        """Whether ``content`` trips ``rule``; returns the matched keyword.

        Keyword rules (trigger_type 1) return the offending keyword; mention-spam
        rules (trigger_type 5) return an empty string (no keyword) when the
        user+role mention count exceeds ``mention_total_limit``. Other trigger
        types are not evaluated yet.
        """
        if rule.trigger_type == 1:
            return self._match_keyword(rule, content)
        if rule.trigger_type == 5:  # MENTION_SPAM
            limit = rule.trigger_metadata.get("mention_total_limit")
            if limit is None:
                return None
            mentions = len(_USER_MENTION.findall(content)) + len(_ROLE_MENTION.findall(content))
            return "" if mentions > int(limit) else None
        return None

    @staticmethod
    def _match_keyword(rule: AutoModRule, content: str) -> str | None:
        """Match ``content`` against a keyword filter using Discord's wildcard rules.

        A bare ``word`` matches only as a whole word (bounded by non-alphanumeric
        characters); ``*`` is a wildcard, so ``word*`` is a prefix match, ``*word``
        a suffix match and ``*word*`` a substring match anywhere — mirroring
        Discord's keyword-filter semantics rather than a naive substring search.
        """
        for keyword in rule.trigger_metadata.get("keyword_filter") or []:
            bare = keyword.strip("*")
            if not bare:
                continue
            left = "" if keyword.startswith("*") else r"(?<![A-Za-z0-9])"
            right = "" if keyword.endswith("*") else r"(?![A-Za-z0-9])"
            if re.search(f"{left}{re.escape(bare)}{right}", content, re.IGNORECASE):
                return keyword
        return None

    def _emit_auto_mod_execution(
        self,
        rule: AutoModRule,
        channel: Channel,
        user_id: int,
        content: str,
        keyword: str,
        action: dict[str, Any],
    ) -> None:
        self.emit(
            "AUTO_MODERATION_ACTION_EXECUTION",
            {
                "guild_id": str(channel.guild_id),
                "action": action,
                "rule_id": str(rule.id),
                "rule_trigger_type": rule.trigger_type,
                "user_id": str(user_id),
                "channel_id": str(channel.id),
                "content": content,
                "matched_keyword": keyword or None,
                "matched_content": keyword or None,
            },
        )

    # --------------------------------------------------- application commands

    def register_commands(self, guild_id: int | None, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        registered = {}
        for payload in payloads:
            cmd = dict(payload)
            cmd["id"] = str(self.snowflake())
            cmd["application_id"] = str(self.application_id)
            cmd.setdefault("type", AppCommandType.CHAT_INPUT)
            cmd.setdefault("description", "")
            cmd.setdefault("options", [])
            cmd.setdefault("default_member_permissions", None)
            cmd.setdefault("nsfw", False)
            cmd.setdefault("dm_permission", True)
            if guild_id is not None:
                cmd["guild_id"] = str(guild_id)
            registered[(cmd["name"], cmd["type"])] = cmd
        self.commands[guild_id] = registered
        return list(registered.values())

    def find_command(
        self, name: str, guild_id: int | None, type: int = AppCommandType.CHAT_INPUT
    ) -> dict[str, Any] | None:
        for scope in (guild_id, None):
            cmd = self.commands.get(scope, {}).get((name, type))
            if cmd is not None:
                return cmd
        return None

    # ----------------------------------------------------------- interactions

    def new_interaction(self, type: int, channel_id: int, user_id: int, guild_id: int | None) -> Interaction:
        interaction_id = self.snowflake()
        record = Interaction(
            id=interaction_id,
            token=f"simcord_interaction_{interaction_id}",
            type=type,
            channel_id=channel_id,
            guild_id=guild_id,
            user_id=user_id,
        )
        self.interactions[interaction_id] = record
        self.interaction_tokens[record.token] = interaction_id
        return record

    def interaction_by_token(self, token: str) -> Interaction:
        interaction_id = self.interaction_tokens.get(token)
        if interaction_id is None:
            raise errors.unknown_webhook()
        return self.interactions[interaction_id]

    # ------------------------------------------------------------ permissions

    def compute_permissions(self, guild_id: int, user_id: int, channel_id: int | None = None) -> int:
        guild = self.get_guild(guild_id)
        channel = None
        if channel_id is not None:
            channel = self.get_channel(channel_id)
            channel = self.get_channel(channel.permission_channel_id())
        return permissions.compute(guild, user_id, channel)

    def require_permissions(
        self, guild_id: int | None, user_id: int, channel_id: int | None, *names: str
    ) -> None:
        if guild_id is None:  # DMs: no guild permissions apply
            return
        perms = self.compute_permissions(guild_id, user_id, channel_id)
        if channel_id is not None and not perms & permissions.flag("view_channel"):
            raise errors.missing_access()
        for name in names:
            if not perms & permissions.flag(name):
                raise errors.missing_permissions()

    def require_hierarchy(self, guild_id: int, actor_id: int, target_id: int) -> None:
        """Role hierarchy: you cannot moderate members at or above your top role."""
        guild = self.get_guild(guild_id)
        if actor_id == guild.owner_id or target_id not in guild.members:
            return
        if target_id == guild.owner_id or guild.top_role_position(target_id) >= guild.top_role_position(
            actor_id
        ):
            raise errors.missing_permissions()

    def require_role_assignable(self, guild_id: int, actor_id: int, role_id: int) -> None:
        """A role can only be assigned/edited if it sits below the actor's top role."""
        guild = self.get_guild(guild_id)
        if actor_id == guild.owner_id:
            return
        role = self.get_role(guild_id, role_id)
        if role.position >= guild.top_role_position(actor_id):
            raise errors.missing_permissions()

    def require_can_grant(self, guild_id: int, actor_id: int, role_permissions: int) -> None:
        """You cannot grant a role permissions you do not hold yourself (unless admin)."""
        guild = self.get_guild(guild_id)
        if actor_id == guild.owner_id:
            return
        actor_perms = self.compute_permissions(guild_id, actor_id)
        if actor_perms & permissions.flag("administrator"):
            return
        if role_permissions & ~actor_perms:
            raise errors.missing_permissions()
