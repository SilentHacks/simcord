"""Resolve whether a member can use an application command.

Conflicting duplicate channel, user, or @everyone overrides for an identical
target are unspecified by Discord; a deny is chosen conservatively. Conflicting
role overrides use the documented allow-beats-deny rule below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord


@dataclass(frozen=True)
class Access:
    allowed: bool
    reason: str | None


def _matching_override(entries: list[dict[str, Any]], *, target_id: int, target_type: int) -> bool | None:
    matches = [
        entry["permission"]
        for entry in entries
        if int(entry["id"]) == target_id and int(entry["type"]) == target_type
    ]
    if not matches:
        return None
    return not any(permission is False for permission in matches)


def _role_override(entries: list[dict[str, Any]], role_ids: list[int]) -> bool | None:
    matches = [
        entry["permission"] for entry in entries if int(entry["type"]) == 1 and int(entry["id"]) in role_ids
    ]
    if any(permission is True for permission in matches):
        return True
    if any(permission is False for permission in matches):
        return False
    return None


def _member_override(
    entries: list[dict[str, Any]], *, user_id: int, role_ids: list[int], guild_id: int
) -> bool | None:
    user = _matching_override(entries, target_id=user_id, target_type=2)
    if user is not None:
        return user
    roles = _role_override(entries, [role_id for role_id in role_ids if role_id != guild_id])
    if roles is not None:
        return roles
    return _matching_override(entries, target_id=guild_id, target_type=1)


def _result(allowed: bool, reason: str | None = None) -> Access:
    return Access(allowed=allowed, reason=None if allowed else reason)


def command_access(backend: Any, command: dict[str, Any], *, user_id: int, channel_id: int) -> Access:
    channel = backend.get_channel(channel_id)
    guild_id = channel.guild_id
    command_guild_id = command.get("guild_id")

    # Discord Developer Documentation, Application Commands: guild commands are scoped to that guild.
    if command_guild_id is not None and int(command_guild_id) != guild_id:
        return _result(False, "scope")

    contexts = command.get("contexts")
    # Discord Developer Documentation, Application Commands: GUILD=0 and BOT_DM=1 contexts;
    # dm_permission is the legacy global-command DM setting.
    if guild_id is not None:
        if contexts is not None and 0 not in contexts:
            return _result(False, "context")
    elif (
        command_guild_id is not None
        or (contexts is not None and 1 not in contexts)
        or (contexts is None and command.get("dm_permission", True) is False)
    ):
        return _result(False, "context")

    # Backend permission validators (backend/ops/permissions.py) likewise treat DMs as having no guild permissions.
    if guild_id is None:
        return _result(True)

    # Discord Permissions documentation: Administrator can use all commands; Use Application Commands gates access.
    perms = backend.compute_permissions(guild_id, user_id, channel_id)
    permissions = discord.Permissions(perms)
    if permissions.administrator:
        return _result(True)
    if not permissions.use_application_commands:
        return _result(False, "use-application-commands")

    # Discord Help Center, Updates to Command Permissions: command settings override conflicting application
    # settings; channel permissions also inherit into threads containing that channel.
    command_overrides = backend.get_command_permissions(guild_id, int(command["id"])) or []
    application_overrides = backend.get_command_permissions(guild_id, backend.application_id) or []
    permission_channel_id = channel.permission_channel_id()
    all_channels_id = guild_id - 1
    for entries, target_id in (
        (command_overrides, permission_channel_id),
        (command_overrides, all_channels_id),
        (application_overrides, permission_channel_id),
        (application_overrides, all_channels_id),
    ):
        channel_permission = _matching_override(entries, target_id=target_id, target_type=3)
        if channel_permission is None:
            continue
        if not channel_permission:
            return _result(False, "channel-denied")
        break

    guild = backend.get_guild(guild_id)
    member = guild.members.get(user_id)
    role_ids = member.role_ids if member is not None else []

    # Discord Help Center, Updates to Command Permissions: command-level member settings override application
    # settings; explicit users and role allows bypass default_member_permissions.
    command_member = _member_override(
        command_overrides, user_id=user_id, role_ids=role_ids, guild_id=guild_id
    )
    if command_member is False:
        return _result(False, "override-denied")
    if command_member is True:
        return _result(True)

    # Discord Help Center, Updates to Command Permissions: application-level allows do not bypass command defaults.
    application_member = _member_override(
        application_overrides, user_id=user_id, role_ids=role_ids, guild_id=guild_id
    )
    if application_member is False:
        return _result(False, "override-denied")

    # Discord Developer Documentation, Application Commands: default_member_permissions are required permission bits.
    default_member_permissions = command.get("default_member_permissions")
    if default_member_permissions is None:
        return _result(True)
    required_permissions = int(default_member_permissions)
    if required_permissions == 0 or perms & required_permissions != required_permissions:
        return _result(False, "default-member-permissions")
    return _result(True)
