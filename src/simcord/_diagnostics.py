"""Pytest-scoped environment discovery and authentic permission revocation.

No HTTP faults or SDK patches: projection mutates only bot-exclusive roles and
bot member overwrites, then emits the same canonical payloads as normal edits.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import discord

from .backend import serializers
from .backend.models import Overwrite
from .enums import OverwriteType

if TYPE_CHECKING:
    from .backend.ops.base import BackendBase
    from .env import Env


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
            env.backend.permission_projection = PermissionProjection(self)


ACTIVE_RUN: ContextVar[PermissionRun | None] = ContextVar("simcord_permission_run", default=None)


class PermissionProjection:
    def __init__(self, run: PermissionRun) -> None:
        self.run = run
        self.active = False

    def unsupported(self, reason: str) -> None:
        if reason not in self.run.unsupported:
            self.run.unsupported.append(reason)

    def __call__(
        self, backend: BackendBase, event: str = "", payload: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any] | None:
        if self.active:
            return payload
        self.active = True
        try:
            self.prepare(backend, event=event, payload=payload)
            if payload is None:
                return None
            guild_id = payload.get("guild_id")
            if event in ("GUILD_CREATE", "GUILD_UPDATE"):
                guild = backend.guilds.get(int(payload["id"]))
                if guild is not None:
                    return serializers.guild_create_payload(backend, guild)
            if event in ("CHANNEL_CREATE", "CHANNEL_UPDATE", "THREAD_CREATE", "THREAD_UPDATE"):
                channel = backend.channels.get(int(payload["id"]))
                if channel is not None:
                    return serializers.channel_payload(backend, channel)
            if event in ("GUILD_ROLE_CREATE", "GUILD_ROLE_UPDATE") and guild_id is not None:
                role = backend.get_guild(int(guild_id)).roles.get(int(payload["role"]["id"]))
                if role is not None:
                    return {**payload, "role": serializers.role_payload(role)}
            return payload
        finally:
            self.active = False

    def prepare(
        self, backend: BackendBase, *, event: str = "", payload: Mapping[str, Any] | None = None
    ) -> None:
        assert self.run.flag is not None
        creating_guild = int(payload["id"]) if payload is not None and event == "GUILD_CREATE" else None
        creating_channel = int(payload["id"]) if payload is not None and event == "CHANNEL_CREATE" else None
        bit = getattr(discord.Permissions, self.run.flag).flag
        admin = discord.Permissions.administrator.flag
        bot_id = backend.bot_user.id
        for guild in tuple(backend.guilds.values()):
            member = guild.members.get(bot_id)
            if member is None:
                continue
            if guild.owner_id == bot_id:
                self.unsupported(f"guild {guild.id}: bot is owner; Discord owners bypass revocation")
                continue
            if guild.everyone_role.permissions & admin:
                self.unsupported(f"guild {guild.id}: shared @everyone administrator bypass")
                continue
            if self.run.guild_scope and guild.everyone_role.permissions & bit:
                self.unsupported(f"guild {guild.id}: guild-scoped flag granted by shared @everyone")
                continue
            for role_id in member.role_ids:
                role = guild.roles.get(role_id)
                if role is None:
                    continue
                remove = admin | (bit if self.run.guild_scope else 0)
                if not role.permissions & remove:
                    continue
                if any(role_id in other.role_ids for uid, other in guild.members.items() if uid != bot_id):
                    self.unsupported(f"guild {guild.id}: role {role_id} shares a grant requiring revocation")
                    continue
                role.permissions &= ~remove
                if guild.id != creating_guild:
                    backend.emit(
                        "GUILD_ROLE_UPDATE",
                        {"guild_id": str(guild.id), "role": serializers.role_payload(role)},
                    )
            for channel_id in tuple(guild.channel_ids):
                channel = backend.channels.get(channel_id)
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
                if channel.id != creating_channel:
                    backend.emit("CHANNEL_UPDATE", serializers.channel_payload(backend, channel))
