"""Omnipotent world builders: handles for arranging the virtual Discord.

Builders construct state directly on the backend — the *test* is omnipotent;
only the bot and simulated users are permission-checked. Queries return real
discord.py model objects from the bot's own cache wherever possible, which
doubles as a continuous check that gateway dispatch populated the cache.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import discord

from .backend.errors import SetupError
from .backend.models import (
    AuditLogEntry,
    Channel,
    Guild,
    GuildEmoji,
    Invite,
    Overwrite,
    Role,
    ScheduledEvent,
    Sticker,
    User,
    VoiceState,
    Webhook,
)
from .enums import ChannelType, OverwriteType
from .results import to_discord_message

if TYPE_CHECKING:
    from .actors import MemberActor
    from .env import Env


class UserHandle:
    """A virtual human user (independent of any guild)."""

    def __init__(self, env: Env, user: User) -> None:
        self._env = env
        self._user = user

    @property
    def id(self) -> int:
        return self._user.id

    @property
    def name(self) -> str:
        return self._user.name

    @property
    def bot(self) -> bool:
        """Whether this user is a bot/application account."""
        return self._user.bot

    @property
    def system(self) -> bool:
        """Whether this user is an official Discord system account."""
        return self._user.system

    @property
    def global_name(self) -> str | None:
        """The user's display name, if distinct from the username."""
        return self._user.global_name

    @property
    def discriminator(self) -> str:
        """The legacy four-digit tag (``"0"`` for migrated accounts)."""
        return self._user.discriminator

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    @property
    def dm_channel(self) -> ChannelHandle:
        """The user's DM channel with the bot."""
        return ChannelHandle(self._env, None, self._env.backend.get_dm_channel(self.id))

    async def send_dm(self, content: str = "", **kwargs: Any) -> discord.Message:
        """DM the bot as this user."""
        channel = self._env.backend.get_dm_channel(self.id)
        message = self._env.backend.create_message(channel.id, self.id, content, **kwargs)
        await self._env.settle()
        return to_discord_message(self._env, message)

    def __repr__(self) -> str:
        return f"<UserHandle id={self.id} name={self.name!r}>"


class WebhookHandle:
    """An incoming webhook that can post messages into its channel.

    Created via :meth:`GuildHandle.create_webhook`. Messages it sends arrive at
    the bot under test with ``message.webhook_id`` set and
    ``message.author.bot`` True — distinct from a bot *member* posting, which
    has ``author.bot`` True but no ``webhook_id``.
    """

    def __init__(self, env: Env, webhook: Webhook) -> None:
        self._env = env
        self._webhook = webhook

    @property
    def id(self) -> int:
        return self._webhook.id

    @property
    def name(self) -> str:
        return self._webhook.name

    @property
    def channel_id(self) -> int:
        return self._webhook.channel_id

    async def send(
        self,
        content: str = "",
        *,
        username: str | None = None,
        embed: discord.Embed | None = None,
        embeds: Sequence[discord.Embed] = (),
        attachments: Sequence[tuple[str, bytes]] = (),
    ) -> discord.Message:
        """Post a message as this webhook, optionally overriding the display name.

        ``username`` mirrors a webhook execution's per-message ``username=``
        override (the message shows under that name, not the webhook's own).
        Pass ``embed`` or ``embeds`` (real ``discord.Embed`` objects) to post an
        embed — the common "service posts an embed" case.
        """
        if embed is not None and embeds:
            raise SetupError("Pass either embed= or embeds=, not both")
        chosen = [embed] if embed is not None else embeds
        embed_dicts: list[dict[str, Any]] = [dict(e.to_dict()) for e in chosen]
        backend = self._env.backend
        attachment_payloads = backend.store_attachments(self.channel_id, attachments)
        message = backend.create_message(
            self.channel_id,
            self._webhook.webhook_user_id,
            content,
            embeds=embed_dicts,
            attachments=attachment_payloads,
            webhook_id=self._webhook.id,
            author_name=username,
        )
        await self._env.settle()
        return to_discord_message(self._env, message)

    def __repr__(self) -> str:
        return f"<WebhookHandle id={self.id} name={self.name!r}>"


class RoleHandle:
    def __init__(self, env: Env, guild: GuildHandle, role: Role) -> None:
        self._env = env
        self._role = role
        self.guild = guild

    @property
    def id(self) -> int:
        return self._role.id

    @property
    def name(self) -> str:
        return self._role.name

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"

    def __repr__(self) -> str:
        return f"<RoleHandle id={self.id} name={self.name!r}>"


class GuildHandle:
    def __init__(self, env: Env, guild: Guild) -> None:
        self._env = env
        self._guild = guild

    @property
    def id(self) -> int:
        return self._guild.id

    @property
    def name(self) -> str:
        return self._guild.name

    @property
    def default_role(self) -> RoleHandle:
        return RoleHandle(self._env, self, self._guild.everyone_role)

    @property
    def owner(self) -> UserHandle:
        return UserHandle(self._env, self._env.backend.get_user(self._guild.owner_id))

    @property
    def me(self) -> discord.Member | None:
        cached = self._env.bot.get_guild(self.id)
        return cached.me if cached else None

    @property
    def channels(self) -> dict[str, ChannelHandle]:
        backend = self._env.backend
        return {
            backend.channels[cid].name or str(cid): ChannelHandle(self._env, self, backend.channels[cid])
            for cid in self._guild.channel_ids
        }

    @property
    def roles(self) -> dict[str, RoleHandle]:
        return {r.name: RoleHandle(self._env, self, r) for r in self._guild.roles.values()}

    def create_text_channel(
        self,
        name: str,
        *,
        overwrites: dict[RoleHandle | MemberActor, discord.PermissionOverwrite] | None = None,
        topic: str | None = None,
        category: ChannelHandle | None = None,
    ) -> ChannelHandle:
        model_overwrites = []
        for target, overwrite in (overwrites or {}).items():
            allow, deny = overwrite.pair()
            model_overwrites.append(
                Overwrite(
                    target_id=target.id,
                    type=OverwriteType.ROLE if isinstance(target, RoleHandle) else OverwriteType.MEMBER,
                    allow=allow.value,
                    deny=deny.value,
                )
            )
        channel = self._env.backend.create_channel(
            self.id,
            name,
            overwrites=model_overwrites,
            topic=topic,
            parent_id=category.id if category else None,
        )
        return ChannelHandle(self._env, self, channel)

    def create_voice_channel(
        self,
        name: str,
        *,
        category: ChannelHandle | None = None,
        user_limit: int = 0,
        bitrate: int = 64000,
    ) -> ChannelHandle:
        channel = self._env.backend.create_channel(
            self.id,
            name,
            type=ChannelType.VOICE,
            user_limit=user_limit,
            bitrate=bitrate,
            parent_id=category.id if category else None,
        )
        return ChannelHandle(self._env, self, channel)

    def create_stage_channel(self, name: str, *, category: ChannelHandle | None = None) -> ChannelHandle:
        channel = self._env.backend.create_channel(
            self.id, name, type=ChannelType.STAGE_VOICE, parent_id=category.id if category else None
        )
        return ChannelHandle(self._env, self, channel)

    def create_news_channel(self, name: str, *, category: ChannelHandle | None = None) -> ChannelHandle:
        channel = self._env.backend.create_channel(
            self.id, name, type=ChannelType.NEWS, parent_id=category.id if category else None
        )
        return ChannelHandle(self._env, self, channel)

    def create_category(self, name: str) -> ChannelHandle:
        channel = self._env.backend.create_channel(self.id, name, type=ChannelType.CATEGORY)
        return ChannelHandle(self._env, self, channel)

    def create_forum_channel(self, name: str) -> ChannelHandle:
        channel = self._env.backend.create_channel(self.id, name, type=ChannelType.FORUM)
        return ChannelHandle(self._env, self, channel)

    def create_scheduled_event(
        self,
        name: str,
        *,
        start_time: str,
        entity_type: int = 2,
        channel: ChannelHandle | None = None,
        description: str | None = None,
        end_time: str | None = None,
        location: str | None = None,
    ) -> ScheduledEvent:
        """Create a scheduled event directly (omnipotent setup)."""
        return self._env.backend.create_scheduled_event(
            self.id,
            name=name,
            entity_type=entity_type,
            scheduled_start_time=start_time,
            channel_id=channel.id if channel else None,
            description=description,
            scheduled_end_time=end_time,
            entity_metadata={"location": location} if location else None,
        )

    def create_webhook(self, channel: ChannelHandle, name: str = "Webhook") -> WebhookHandle:
        """Create an incoming webhook bound to ``channel`` (omnipotent setup).

        Use the returned handle's :meth:`~WebhookHandle.send` to post messages
        as the webhook would — they arrive with ``message.webhook_id`` set and
        ``message.author.bot`` True, which is how a webhook (a GitHub/CI
        integration, another service posting an embed, etc.) appears to the bot
        under test. This is the test-driven counterpart to the bot creating and
        executing a webhook itself over the API.
        """
        webhook = self._env.backend.create_webhook(channel.id, name, self._env.backend.bot_user.id)
        return WebhookHandle(self._env, webhook)

    def create_emoji(self, name: str, *, animated: bool = False) -> GuildEmoji:
        return self._env.backend.create_emoji(self.id, name, self._env.backend.bot_user.id, animated=animated)

    def create_sticker(self, name: str, *, description: str | None = None, tags: str = "") -> Sticker:
        return self._env.backend.create_sticker(
            self.id, name, self._env.backend.bot_user.id, description=description, tags=tags
        )

    def set_command_permissions(
        self, command: Any, permissions: dict[RoleHandle | ChannelHandle | UserHandle | MemberActor, bool]
    ) -> None:
        """Set an app command's per-guild permission overrides (omnipotent setup).

        ``command`` is a command id or any object with an ``id`` (e.g. a fetched
        ``discord.app_commands.AppCommand``); ``permissions`` maps role/user/
        channel handles to allow (``True``) / deny (``False``). The bot then
        reads these back via ``AppCommand.fetch_permissions``.
        """
        command_id = command if isinstance(command, int) else command.id
        entries = [
            {
                "id": str(target.id),
                "type": 1
                if isinstance(target, RoleHandle)
                else 3
                if isinstance(target, ChannelHandle)
                else 2,
                "permission": bool(allowed),
            }
            for target, allowed in permissions.items()
        ]
        self._env.backend.set_command_permissions(self.id, command_id, entries)

    def audit_log(self) -> list[AuditLogEntry]:
        """The guild's recorded audit-log entries, oldest first."""
        return list(self._guild.audit_log_entries)

    def voice_states(self) -> dict[int, VoiceState]:
        """Current voice states keyed by user id."""
        return dict(self._guild.voice_states)

    def invites(self) -> list[Invite]:
        return [inv for inv in self._env.backend.invites.values() if inv.guild_id == self.id]

    def set_vanity_url(self, code: str) -> None:
        """Give the guild a vanity invite code so ``Guild.vanity_invite()`` resolves.

        Discord backs a vanity URL with a real invite, so this stores one under
        ``code`` pointing at the guild's first channel — the populated path is
        genuine modelled state a test can read back, not a constant fake.
        """
        if not self._guild.channel_ids:
            raise SetupError("set_vanity_url needs a channel; create one first")
        backend = self._env.backend
        self._guild.vanity_url_code = code
        backend.invites[code] = Invite(
            code=code,
            guild_id=self.id,
            channel_id=self._guild.channel_ids[0],
            inviter_id=backend.bot_user.id,
            created_at=backend.now_iso(),
        )

    def create_role(
        self, name: str, *, permissions: discord.Permissions | None = None, **fields: Any
    ) -> RoleHandle:
        role = self._env.backend.create_role(
            self.id, name, permissions=permissions.value if permissions else 0, **fields
        )
        return RoleHandle(self._env, self, role)

    def add_member(
        self,
        user: UserHandle,
        *,
        roles: Sequence[RoleHandle] = (),
        nick: str | None = None,
    ) -> MemberActor:
        from .actors import MemberActor

        self._env.backend.add_member(self.id, user.id, roles=[r.id for r in roles], nick=nick, announce=True)
        return MemberActor(self._env, self, user)

    def remove_member(self, member: MemberActor | UserHandle) -> None:
        """The member leaves the guild (dispatches the leave event)."""
        self._env.backend.remove_member(self.id, member.id)

    def get_ban(self, user: UserHandle | MemberActor) -> dict[str, Any] | None:
        if user.id not in self._guild.bans:
            return None
        return {"user": user, "reason": self._guild.bans[user.id]}

    def member_ids(self) -> list[int]:
        return list(self._guild.members)

    def __repr__(self) -> str:
        return f"<GuildHandle id={self.id} name={self.name!r}>"


class ChannelHandle:
    def __init__(self, env: Env, guild: GuildHandle | None, channel: Channel) -> None:
        self._env = env
        self._channel = channel
        self.guild = guild

    @property
    def id(self) -> int:
        return self._channel.id

    @property
    def name(self) -> str | None:
        return self._channel.name

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    @property
    def is_thread(self) -> bool:
        return self._channel.is_thread

    @property
    def threads(self) -> list[ChannelHandle]:
        backend = self._env.backend
        return [
            ChannelHandle(self._env, self.guild, c)
            for c in backend.channels.values()
            if c.is_thread and c.parent_id == self.id
        ]

    def history(self, *, viewer: MemberActor | UserHandle | None = None) -> list[discord.Message]:
        """All messages, oldest first, as real ``discord.Message`` objects.

        With ``viewer=``, ephemeral messages not addressed to that user are
        hidden — exactly what that user would see in their client.
        """
        out = []
        viewer_id = viewer.id if viewer is not None else None
        for message in sorted(self._env.backend.messages.get(self.id, {}).values(), key=lambda m: m.id):
            if not message.visible_to(viewer_id):
                continue
            out.append(to_discord_message(self._env, message))
        return out

    @property
    def last_message(self) -> discord.Message | None:
        history = self.history()
        return history[-1] if history else None

    def pinned_messages(self) -> list[discord.Message]:
        return [m for m in self.history() if m.pinned]

    def __repr__(self) -> str:
        return f"<ChannelHandle id={self.id} name={self.name!r}>"
