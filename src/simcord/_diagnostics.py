"""Pytest-scoped discovery and mutation-boundary permission revocation."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

from .backend import serializers
from .backend.models import Overwrite
from .enums import OverwriteType

if TYPE_CHECKING:
    from .backend.ops.base import BackendBase
    from .env import Env


class UnsupportedProjection(RuntimeError):
    """Stop a rerun before it exercises authority projection cannot preserve."""


@dataclass
class PermissionRun:
    """One fresh pytest lifecycle; never shared across tests or reruns."""

    flag: str | None = None
    guild_scope: bool = False
    envs: list[Env] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    baseline_env_ids: set[int] = field(default_factory=set)

    def register(self, env: Env) -> None:
        if env in self.envs:
            return
        self.envs.append(env)
        if id(env) in self.baseline_env_ids:
            self.unsupported.append("Env reused from baseline; each rerun requires a fresh environment")
            return
        if self.flag is not None:
            projection = PermissionProjection(self)
            env.backend.permission_projection = projection
            for guild_id in tuple(env.backend.guilds):
                projection(env.backend, guild_id, channels=True, initializing=True)


ACTIVE_RUN: ContextVar[PermissionRun | None] = ContextVar("simcord_permission_run", default=None)


class PermissionProjection:
    def __init__(self, run: PermissionRun) -> None:
        self.run = run
        # Only the bit actually removed is ours; explicit permission edits relinquish it.
        self.suppressed_roles: dict[int, set[int]] = {}

    def unsupported(self, reason: str, *, initializing: bool) -> None:
        if reason not in self.run.unsupported:
            self.run.unsupported.append(reason)
        if not initializing:
            raise UnsupportedProjection(reason)

    def __call__(
        self,
        backend: BackendBase,
        guild_id: int,
        *,
        channel_id: int | None = None,
        role_id: int | None = None,
        channels: bool = False,
        announce: bool = True,
        initializing: bool = False,
        permissions_edited: bool = False,
    ) -> None:
        assert self.run.flag is not None
        if self.run.unsupported and not initializing:
            raise UnsupportedProjection(self.run.unsupported[0])
        guild = backend.get_guild(guild_id)
        bit = getattr(discord.Permissions, self.run.flag).flag
        admin = discord.Permissions.administrator.flag
        bot_id = backend.bot_user.id
        suppressed = self.suppressed_roles.setdefault(guild_id, set())
        if permissions_edited and role_id is not None:
            suppressed.discard(role_id)
        member = guild.members.get(bot_id)
        bot_roles = set(member.role_ids) if member is not None else set()
        for rid in tuple(suppressed):
            role = guild.roles.get(rid)
            if role is None:
                suppressed.discard(rid)
                continue
            shared = any(rid in other.role_ids for uid, other in guild.members.items() if uid != bot_id)
            if rid not in bot_roles or shared:
                role.permissions |= bit
                suppressed.discard(rid)
                if announce:
                    backend.emit(
                        "GUILD_ROLE_UPDATE",
                        {"guild_id": str(guild_id), "role": serializers.role_payload(role)},
                    )
                if shared and rid in bot_roles:
                    self.unsupported(
                        f"guild {guild_id}: role {role.id} shares a grant requiring revocation",
                        initializing=initializing,
                    )
                    return
        if member is None:
            return
        reason = None
        if guild.owner_id == bot_id:
            reason = f"guild {guild_id}: bot is owner; Discord owners bypass revocation"
        elif guild.everyone_role.permissions & admin:
            reason = f"guild {guild_id}: shared @everyone administrator bypass"
        elif self.run.guild_scope and guild.everyone_role.permissions & bit:
            reason = f"guild {guild_id}: guild-scoped flag granted by shared @everyone"
        elif any(guild.roles[rid].permissions & admin for rid in bot_roles if rid in guild.roles):
            reason = f"guild {guild_id}: bot Administrator bypass cannot be reduced to one revoked permission"
        if reason is not None:
            self.unsupported(reason, initializing=initializing)
            return
        if self.run.guild_scope:
            for rid in bot_roles:
                role = guild.roles.get(rid)
                if role is None or not role.permissions & bit:
                    continue
                if any(rid in other.role_ids for uid, other in guild.members.items() if uid != bot_id):
                    self.unsupported(
                        f"guild {guild_id}: role {rid} shares a grant requiring revocation",
                        initializing=initializing,
                    )
                    return
                role.permissions &= ~bit
                suppressed.add(rid)
                if announce and rid != role_id:
                    backend.emit(
                        "GUILD_ROLE_UPDATE",
                        {"guild_id": str(guild_id), "role": serializers.role_payload(role)},
                    )
        selected = guild.channel_ids if channels else (() if channel_id is None else (channel_id,))
        for ident in selected:
            channel = backend.channels.get(ident)
            if channel is None or channel.is_thread:
                continue
            overwrite = next(
                (
                    item
                    for item in channel.overwrites
                    if item.target_id == bot_id and item.type == OverwriteType.MEMBER
                ),
                None,
            )
            if overwrite is None:
                overwrite = Overwrite(target_id=bot_id, type=OverwriteType.MEMBER)
                channel.overwrites.append(overwrite)
            allow, deny = overwrite.allow & ~bit, overwrite.deny | bit
            if (overwrite.allow, overwrite.deny) == (allow, deny):
                continue
            overwrite.allow, overwrite.deny = allow, deny
            if announce and channel.id != channel_id:
                backend.emit("CHANNEL_UPDATE", serializers.channel_payload(backend, channel))
