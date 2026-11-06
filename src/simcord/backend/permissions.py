"""Effective-permission computation, per Discord's documented algorithm.

https://discord.com/developers/docs/topics/permissions#permission-overwrites

Order: base (union of @everyone + role permissions, with owner and
administrator short-circuits) → @everyone channel overwrite → aggregated role
overwrites → member overwrite → timeout masking.
"""

from __future__ import annotations

import datetime
from typing import Any

import discord

from ..enums import OverwriteType
from .models import Channel, Guild

_ALL = discord.Permissions.all().value
_ADMINISTRATOR = discord.Permissions.administrator.flag
_TIMEOUT_MASK = discord.Permissions.view_channel.flag | discord.Permissions.read_message_history.flag


def _is_timed_out(guild: Guild, user_id: int, now: datetime.datetime) -> bool:
    member = guild.members.get(user_id)
    if member is None or member.timed_out_until is None:
        return False
    until = datetime.datetime.fromisoformat(member.timed_out_until)
    return until > now


def compute(guild: Guild, user_id: int, channel: Channel | None = None, *, now: datetime.datetime) -> int:
    if guild.owner_id == user_id:
        return _ALL
    member = guild.members.get(user_id)
    if member is None:
        return 0

    base = guild.everyone_role.permissions
    for role_id in member.role_ids:
        role = guild.roles.get(role_id)
        if role is not None:
            base |= role.permissions

    if base & _ADMINISTRATOR:
        return _ALL

    if channel is not None:
        by_target = {o.target_id: o for o in channel.overwrites}
        everyone = by_target.get(guild.id)
        if everyone is not None:
            base = (base & ~everyone.deny) | everyone.allow
        allow = deny = 0
        for role_id in member.role_ids:
            overwrite = by_target.get(role_id)
            if overwrite is not None and overwrite.type == OverwriteType.ROLE:
                allow |= overwrite.allow
                deny |= overwrite.deny
        base = (base & ~deny) | allow
        member_overwrite = by_target.get(user_id)
        if member_overwrite is not None and member_overwrite.type == OverwriteType.MEMBER:
            base = (base & ~member_overwrite.deny) | member_overwrite.allow

    if _is_timed_out(guild, user_id, now):
        base &= _TIMEOUT_MASK

    return base


def explain(
    guild: Guild, user_id: int, channel: Channel | None = None, *, now: datetime.datetime
) -> dict[str, Any]:
    """JSON-friendly trace: actor/guild/channel IDs, ordered steps, effective bits.

    Each step has ``source``, ``before``, ``allow``, ``deny``, and ``after``.
    IDs and permission bitsets are integers. The ordinary compute path does not
    allocate a trace; tests keep these two implementations in agreement.
    """
    steps: list[dict[str, Any]] = []
    value = 0

    def step(source: str, allow: int = 0, deny: int = 0, **detail: Any) -> None:
        nonlocal value
        before = value
        value = (value & ~deny) | allow
        steps.append(
            {"source": source, "before": before, "allow": allow, "deny": deny, "after": value, **detail}
        )

    member = guild.members.get(user_id)
    if guild.owner_id == user_id:
        step("owner", _ALL)
    elif member is None:
        step("not_member")
    else:
        step("everyone", guild.everyone_role.permissions, role_id=guild.id)
        for role_id in member.role_ids:
            role = guild.roles.get(role_id)
            if role is not None:
                step("role", role.permissions, role_id=role_id)
        if value & _ADMINISTRATOR:
            step("administrator", _ALL)
        else:
            if channel is not None:
                by_target = {o.target_id: o for o in channel.overwrites}
                everyone = by_target.get(guild.id)
                if everyone is not None:
                    step("everyone_overwrite", everyone.allow, everyone.deny, target_id=guild.id)
                allow = deny = 0
                role_ids = []
                for role_id in member.role_ids:
                    overwrite = by_target.get(role_id)
                    if overwrite is not None and overwrite.type == OverwriteType.ROLE:
                        allow |= overwrite.allow
                        deny |= overwrite.deny
                        role_ids.append(role_id)
                step("role_overwrites", allow, deny, role_ids=role_ids)
                overwrite = by_target.get(user_id)
                if overwrite is not None and overwrite.type == OverwriteType.MEMBER:
                    step("member_overwrite", overwrite.allow, overwrite.deny, target_id=user_id)
            if _is_timed_out(guild, user_id, now):
                step("timeout", deny=value & ~_TIMEOUT_MASK)
    return {
        "actor_id": user_id,
        "guild_id": guild.id,
        "channel_id": channel.id if channel is not None else None,
        "steps": steps,
        "effective": value,
    }


def flag(name: str) -> int:
    return getattr(discord.Permissions, name).flag
