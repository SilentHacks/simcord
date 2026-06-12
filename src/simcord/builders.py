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

from .backend.models import Channel, Guild, Overwrite, Role, User
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
    ) -> ChannelHandle:
        model_overwrites = []
        for target, overwrite in (overwrites or {}).items():
            allow, deny = overwrite.pair()
            model_overwrites.append(
                Overwrite(
                    target_id=target.id,
                    type=0 if isinstance(target, RoleHandle) else 1,
                    allow=allow.value,
                    deny=deny.value,
                )
            )
        channel = self._env.backend.create_channel(self.id, name, overwrites=model_overwrites, topic=topic)
        return ChannelHandle(self._env, self, channel)

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
