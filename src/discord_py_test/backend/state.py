"""The Backend: single in-memory source of truth for the virtual Discord.

REST handlers and gateway events are two projections of this one store: every
mutation that real Discord would announce over the gateway is broadcast to all
attached clients, so the bot's cache stays consistent with REST responses —
including for the bot's own actions.
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Callable, Iterable, Optional

import discord

from . import errors, permissions, serializers
from .cdn import CdnStore
from .models import (
    Channel,
    Guild,
    Member,
    Message,
    Overwrite,
    Reaction,
    Role,
    ThreadMetadata,
    User,
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

EventListener = Callable[[str, dict[str, Any]], None]


class Backend:
    def __init__(self) -> None:
        self._counter = 0
        self.users: dict[int, User] = {}
        self.guilds: dict[int, Guild] = {}
        self.channels: dict[int, Channel] = {}
        self.messages: dict[int, dict[int, Message]] = {}
        self.webhooks: dict[int, Webhook] = {}
        self.webhook_tokens: dict[str, int] = {}
        self.dm_channels: dict[int, int] = {}  # user id -> channel id
        self.commands: dict[Optional[int], dict[str, dict[str, Any]]] = {}
        self.interactions: dict[int, dict[str, Any]] = {}
        self.interaction_tokens: dict[str, int] = {}
        self.cdn = CdnStore()
        self.subscribers: list[EventListener] = []
        self.http_log: list[tuple[str, str, Optional[dict[str, Any]]]] = []
        self.faults: list[dict[str, Any]] = []
        self.application_id: int = self.snowflake()
        self.bot_user: User = self.make_user("TestBot", bot=True)

    # ------------------------------------------------------------------ core

    def snowflake(self) -> int:
        """Deterministic, monotonic snowflakes with valid embedded timestamps."""
        self._counter += 1
        ms = _VIRTUAL_EPOCH_MS - _DISCORD_EPOCH_MS + self._counter
        return (ms << 22) | (self._counter % 4096)

    def now_iso(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def emit(self, event: str, payload: dict[str, Any]) -> None:
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

    def create_guild(self, name: str, *, owner_id: Optional[int] = None) -> Guild:
        guild_id = self.snowflake()
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

    # --------------------------------------------------------------- members

    def add_member(
        self,
        guild_id: int,
        user_id: int,
        *,
        roles: Iterable[int] = (),
        nick: Optional[str] = None,
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

    def delete_role(self, guild_id: int, role_id: int) -> None:
        guild = self.get_guild(guild_id)
        self.get_role(guild_id, role_id)
        del guild.roles[role_id]
        for member in guild.members.values():
            if role_id in member.role_ids:
                member.role_ids.remove(role_id)
        self.emit("GUILD_ROLE_DELETE", {"guild_id": str(guild_id), "role_id": str(role_id)})

    # -------------------------------------------------------------- channels

    def create_channel(
        self,
        guild_id: Optional[int],
        name: Optional[str],
        *,
        type: int = 0,
        overwrites: Optional[list[Overwrite]] = None,
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

    def get_dm_channel(self, user_id: int) -> Channel:
        self.get_user(user_id)
        channel_id = self.dm_channels.get(user_id)
        if channel_id is not None:
            return self.channels[channel_id]
        channel = self.create_channel(None, None, type=1)
        channel.recipient_ids = [user_id]
        self.dm_channels[user_id] = channel.id
        return channel

    def create_thread(
        self,
        parent_id: int,
        name: str,
        owner_id: int,
        *,
        type: int = 11,
        auto_archive_duration: int = 1440,
        message_id: Optional[int] = None,
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
        embeds: Optional[list[dict[str, Any]]] = None,
        components: Optional[list[dict[str, Any]]] = None,
        attachments: Optional[list[dict[str, Any]]] = None,
        flags: int = 0,
        reference: Optional[dict[str, Any]] = None,
        interaction_metadata: Optional[dict[str, Any]] = None,
        broadcast: bool = True,
    ) -> Message:
        channel = self.get_channel(channel_id)
        if content and len(content) > 2000:
            raise errors.invalid_form_body("content must be 2000 or fewer in length")
        message = Message(
            id=self.snowflake(),
            channel_id=channel_id,
            author_id=author_id,
            content=content or "",
            timestamp=self.now_iso(),
            type=19 if reference else 0,
            flags=flags,
            embeds=embeds or [],
            components=components or [],
            attachments=attachments or [],
            reference=reference,
            interaction_metadata=interaction_metadata,
            mention_user_ids=[int(m) for m in _USER_MENTION.findall(content or "")],
            mention_role_ids=[int(m) for m in _ROLE_MENTION.findall(content or "")],
            mention_everyone="@everyone" in (content or ""),
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

    def _emit_reaction(self, event: str, message: Message, emoji: str, user_id: int) -> None:
        channel = self.get_channel(message.channel_id)
        payload: dict[str, Any] = {
            "user_id": str(user_id),
            "channel_id": str(message.channel_id),
            "message_id": str(message.id),
            "emoji": serializers._emoji_payload(emoji),
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
            token=f"dpt_webhook_{webhook_id}",
            channel_id=channel_id,
            guild_id=channel.guild_id,
            name=name,
            creator_id=creator_id,
            webhook_user_id=webhook_user.id,
        )
        self.webhooks[webhook.id] = webhook
        self.webhook_tokens[webhook.token] = webhook.id
        return webhook

    # --------------------------------------------------- application commands

    def register_commands(self, guild_id: Optional[int], payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        registered = {}
        for payload in payloads:
            cmd = dict(payload)
            cmd["id"] = str(self.snowflake())
            cmd["application_id"] = str(self.application_id)
            cmd.setdefault("type", 1)
            cmd.setdefault("description", "")
            cmd.setdefault("options", [])
            cmd.setdefault("default_member_permissions", None)
            cmd.setdefault("nsfw", False)
            cmd.setdefault("dm_permission", True)
            if guild_id is not None:
                cmd["guild_id"] = str(guild_id)
            registered[(cmd["name"], cmd["type"])] = cmd
        self.commands[guild_id] = {f"{name}\x00{type}": c for (name, type), c in registered.items()}
        return list(registered.values())

    def find_command(self, name: str, guild_id: Optional[int], type: int = 1) -> Optional[dict[str, Any]]:
        for scope in (guild_id, None):
            cmd = self.commands.get(scope, {}).get(f"{name}\x00{type}")
            if cmd is not None:
                return cmd
        return None

    # ----------------------------------------------------------- interactions

    def new_interaction(
        self, type: int, channel_id: int, user_id: int, guild_id: Optional[int]
    ) -> dict[str, Any]:
        interaction_id = self.snowflake()
        record: dict[str, Any] = {
            "id": interaction_id,
            "token": f"dpt_interaction_{interaction_id}",
            "type": type,
            "channel_id": channel_id,
            "guild_id": guild_id,
            "user_id": user_id,
            "responded": False,
            "response_kind": None,  # 'message' | 'deferred' | 'update' | 'modal' | 'autocomplete' | 'pong'
            "message_id": None,
            "source_message_id": None,
            "ephemeral": False,
            "followup_ids": [],
            "modal": None,
            "autocomplete_choices": None,
        }
        self.interactions[interaction_id] = record
        self.interaction_tokens[record["token"]] = interaction_id
        return record

    def interaction_by_token(self, token: str) -> dict[str, Any]:
        interaction_id = self.interaction_tokens.get(token)
        if interaction_id is None:
            raise errors.unknown_webhook()
        return self.interactions[interaction_id]

    # ------------------------------------------------------------ permissions

    def compute_permissions(self, guild_id: int, user_id: int, channel_id: Optional[int] = None) -> int:
        guild = self.get_guild(guild_id)
        channel = None
        if channel_id is not None:
            channel = self.get_channel(channel_id)
            channel = self.get_channel(channel.permission_channel_id())
        return permissions.compute(guild, user_id, channel)

    def require_permissions(
        self, guild_id: Optional[int], user_id: int, channel_id: Optional[int], *names: str
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
        if target_id == guild.owner_id or guild.top_role_position(target_id) >= guild.top_role_position(actor_id):
            raise errors.missing_permissions()
