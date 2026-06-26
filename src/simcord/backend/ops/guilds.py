"""Guilds, members and roles — one tightly-coupled mutation cluster."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import discord

from .. import errors, serializers
from ..models import Guild, Member, Role
from .base import BackendBase

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


class GuildMixin(BackendBase):
    # ---------------------------------------------------------------- guilds

    def create_guild(
        self, name: str, *, id: int | None = None, owner_id: int | None = None, **settings: Any
    ) -> Guild:
        guild_id = id if id is not None else self.snowflake()
        if owner_id is None:
            # A synthetic owner: the bot must never own guilds by default,
            # since owners bypass every permission check.
            owner_id = self.make_user(f"{name} Owner").id
        guild = Guild(id=guild_id, name=name, owner_id=owner_id, **settings)
        guild.roles[guild_id] = Role(
            id=guild_id, name="@everyone", permissions=DEFAULT_EVERYONE_PERMISSIONS, position=0
        )
        self.guilds[guild_id] = guild
        # The owner is always a member of their own guild on real Discord; without
        # the membership discord.py's ``Guild.owner`` (resolved via get_member)
        # would be None and the owner would be absent from ``guild.members``.
        self.add_member(guild_id, owner_id)
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

    def edit_guild(self, guild_id: int, changes: Mapping[str, Any]) -> Guild:
        """Apply validated field changes to a guild and announce the update."""
        guild = self.get_guild(guild_id)
        for attr, value in changes.items():
            setattr(guild, attr, value)
        self.emit("GUILD_UPDATE", serializers.guild_create_payload(self, guild))
        return guild

    def remove_guild(self, guild_id: int) -> None:
        """Drop a guild the bot has left (and its channels) and announce GUILD_DELETE.

        Mirrors a real removal: the gateway projection fires GUILD_DELETE so the
        bot's cache evicts the guild, exactly as when it is kicked or leaves.
        """
        guild = self.get_guild(guild_id)
        for channel_id in list(guild.channel_ids) + list(guild.thread_ids):
            self.channels.pop(channel_id, None)
            self.messages.pop(channel_id, None)
        del self.guilds[guild_id]
        self.emit("GUILD_DELETE", {"id": str(guild_id)})

    # --------------------------------------------------------------- members

    def get_member(self, guild_id: int, user_id: int) -> Member:
        member = self.get_guild(guild_id).members.get(user_id)
        if member is None:
            raise errors.unknown_member()
        return member

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

    def remove_member(self, guild_id: int, user_id: int) -> None:
        guild = self.get_guild(guild_id)
        if user_id not in guild.members:
            raise errors.unknown_member()
        del guild.members[user_id]
        # Leaving the guild leaves every thread in it, so thread member_count and
        # the thread-member listings stay correct after a kick/ban/prune.
        for thread_id in guild.thread_ids:
            thread = self.channels.get(thread_id)
            if thread is not None:
                thread.thread_members.pop(user_id, None)
        self.emit(
            "GUILD_MEMBER_REMOVE",
            {"guild_id": str(guild_id), "user": serializers.user_payload(self.get_user(user_id))},
        )

    def apply_ban(self, guild_id: int, user_id: int, reason: str | None) -> None:
        """Record a ban, evict the member if present, and announce GUILD_BAN_ADD.

        The single source of truth for the ban mutation, shared by the single and
        bulk ban routes. Audit logging stays in the route layer (the API-call
        path), like every other moderation action.
        """
        guild = self.get_guild(guild_id)
        guild.bans[user_id] = reason
        if user_id in guild.members:
            self.remove_member(guild_id, user_id)
        self.emit(
            "GUILD_BAN_ADD",
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

    def edit_role(self, guild_id: int, role_id: int, changes: Mapping[str, Any]) -> Role:
        """Apply validated field changes to a role and announce the update."""
        role = self.get_role(guild_id, role_id)
        for attr, value in changes.items():
            setattr(role, attr, value)
        self.emit("GUILD_ROLE_UPDATE", {"guild_id": str(guild_id), "role": serializers.role_payload(role)})
        return role

    def reorder_roles(self, guild_id: int, positions: Iterable[Mapping[str, Any]]) -> Guild:
        """Apply a list of ``{id, position}`` updates and announce each move.

        discord.py's ``Guild.edit_role_positions`` sends the whole new ordering
        through ``PATCH /guilds/{id}/roles``; only the roles whose position
        actually changes fire GUILD_ROLE_UPDATE.
        """
        guild = self.get_guild(guild_id)
        changed: list[Role] = []
        for item in positions:
            role = guild.roles.get(int(item["id"]))
            if role is None or item.get("position") is None:
                continue
            new_position = int(item["position"])
            if role.position != new_position:
                role.position = new_position
                changed.append(role)
        for role in changed:
            self.emit(
                "GUILD_ROLE_UPDATE", {"guild_id": str(guild_id), "role": serializers.role_payload(role)}
            )
        return guild

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
