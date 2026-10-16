"""Effective-permission computation, per Discord's documented algorithm.

https://discord.com/developers/docs/topics/permissions#permission-overwrites

Order: base (union of @everyone + role permissions, with owner and
administrator short-circuits) → @everyone channel overwrite → aggregated role
overwrites → member overwrite → timeout masking.
"""

from __future__ import annotations

import datetime

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


def flag(name: str) -> int:
    return getattr(discord.Permissions, name).flag
