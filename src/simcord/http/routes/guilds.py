"""Guild routes: members, moderation (kick/ban/timeout), roles."""

from __future__ import annotations

from typing import Any

from ...backend import errors, serializers
from ..router import RequestContext, route


@route("GET", "/guilds/{guild_id}")
def get_guild(ctx: RequestContext) -> Any:
    return dict(serializers.guild_create_payload(ctx.backend, ctx.backend.get_guild(ctx.int_arg("guild_id"))))


@route("GET", "/guilds/{guild_id}/members/{user_id}")
def get_member(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild = backend.get_guild(ctx.int_arg("guild_id"))
    member = backend.get_member(guild.id, ctx.int_arg("user_id"))
    return dict(serializers.member_payload(backend, guild, member))


@route("PATCH", "/guilds/{guild_id}/members/{user_id}")
def edit_member(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    user_id = ctx.int_arg("user_id")
    guild = backend.get_guild(guild_id)
    member = backend.get_member(guild_id, user_id)
    body = ctx.body()
    bot_id = backend.bot_user.id

    # Validate every change before applying any of them, then hand the whole set
    # to the backend so the write and its GUILD_MEMBER_UPDATE stay atomic — a
    # partial edit that 403s halfway would desync the bot's cache.
    changes: dict[str, Any] = {}
    if "nick" in body:
        perm = "change_nickname" if user_id == bot_id else "manage_nicknames"
        ctx.require_guild_permissions(guild_id, perm)
        if user_id != bot_id:
            backend.require_hierarchy(guild_id, bot_id, user_id)
        changes["nick"] = body["nick"]
    if "roles" in body:
        ctx.require_guild_permissions(guild_id, "manage_roles")
        new_roles = [int(r) for r in body["roles"]]
        for role_id in set(new_roles) ^ set(member.role_ids):  # only the added/removed roles
            backend.require_role_assignable(guild_id, bot_id, role_id)
        changes["role_ids"] = new_roles
    if "communication_disabled_until" in body:
        ctx.require_guild_permissions(guild_id, "moderate_members")
        backend.require_hierarchy(guild_id, bot_id, user_id)
        changes["timed_out_until"] = body["communication_disabled_until"]
    for key in ("mute", "deaf"):
        if key in body:
            changes[key] = body[key]

    member = backend.edit_member(guild_id, user_id, changes)
    return dict(serializers.member_payload(backend, guild, member))


@route("PUT", "/guilds/{guild_id}/members/{user_id}/roles/{role_id}")
def add_member_role(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    user_id = ctx.int_arg("user_id")
    ctx.require_guild_permissions(guild_id, "manage_roles")
    backend.get_member(guild_id, user_id)
    role = backend.get_role(guild_id, ctx.int_arg("role_id"))
    backend.require_role_assignable(guild_id, backend.bot_user.id, role.id)
    backend.add_member_role(guild_id, user_id, role.id)


@route("DELETE", "/guilds/{guild_id}/members/{user_id}/roles/{role_id}")
def remove_member_role(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    user_id = ctx.int_arg("user_id")
    role_id = ctx.int_arg("role_id")
    ctx.require_guild_permissions(guild_id, "manage_roles")
    backend.get_member(guild_id, user_id)
    backend.require_role_assignable(guild_id, backend.bot_user.id, role_id)
    backend.remove_member_role(guild_id, user_id, role_id)


@route("DELETE", "/guilds/{guild_id}/members/{user_id}")
def kick(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    user_id = ctx.int_arg("user_id")
    ctx.require_guild_permissions(guild_id, "kick_members")
    backend.require_hierarchy(guild_id, backend.bot_user.id, user_id)
    backend.remove_member(guild_id, user_id)


@route("PUT", "/guilds/{guild_id}/bans/{user_id}")
def ban(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    user_id = ctx.int_arg("user_id")
    ctx.require_guild_permissions(guild_id, "ban_members")
    backend.require_hierarchy(guild_id, backend.bot_user.id, user_id)
    guild = backend.get_guild(guild_id)
    guild.bans[user_id] = None
    if user_id in guild.members:
        backend.remove_member(guild_id, user_id)
    backend.emit(
        "GUILD_BAN_ADD",
        {"guild_id": str(guild_id), "user": serializers.user_payload(backend.get_user(user_id))},
    )


@route("DELETE", "/guilds/{guild_id}/bans/{user_id}")
def unban(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    user_id = ctx.int_arg("user_id")
    ctx.require_guild_permissions(guild_id, "ban_members")
    guild = backend.get_guild(guild_id)
    if user_id not in guild.bans:
        raise errors.unknown_ban()
    del guild.bans[user_id]
    backend.emit(
        "GUILD_BAN_REMOVE",
        {"guild_id": str(guild_id), "user": serializers.user_payload(backend.get_user(user_id))},
    )


def _ban_payload(ctx: RequestContext, user_id: int, reason: Any) -> dict[str, Any]:
    return {"user": dict(serializers.user_payload(ctx.backend.get_user(user_id))), "reason": reason}


@route("GET", "/guilds/{guild_id}/bans")
def get_bans(ctx: RequestContext) -> Any:
    guild = ctx.backend.get_guild(ctx.int_arg("guild_id"))
    return [_ban_payload(ctx, uid, reason) for uid, reason in guild.bans.items()]


@route("GET", "/guilds/{guild_id}/bans/{user_id}")
def get_ban(ctx: RequestContext) -> Any:
    guild = ctx.backend.get_guild(ctx.int_arg("guild_id"))
    user_id = ctx.int_arg("user_id")
    if user_id not in guild.bans:
        raise errors.unknown_ban()
    return _ban_payload(ctx, user_id, guild.bans[user_id])


@route("POST", "/guilds/{guild_id}/roles")
def create_role(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    bot_id = backend.bot_user.id
    ctx.require_guild_permissions(guild_id, "manage_roles")
    body = ctx.body()
    new_permissions = int(body.get("permissions") or 0)
    backend.require_can_grant(guild_id, bot_id, new_permissions)
    role = backend.create_role(
        guild_id,
        body.get("name") or "new role",
        permissions=new_permissions,
        hoist=bool(body.get("hoist", False)),
        mentionable=bool(body.get("mentionable", False)),
        color=int(body.get("color") or 0),
    )
    return dict(serializers.role_payload(role))


@route("PATCH", "/guilds/{guild_id}/roles/{role_id}")
def edit_role(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    role_id = ctx.int_arg("role_id")
    bot_id = backend.bot_user.id
    ctx.require_guild_permissions(guild_id, "manage_roles")
    backend.get_role(guild_id, role_id)
    body = ctx.body()

    # Validate before mutating: a role above the bot can't be edited, and the
    # bot can't grant permissions it lacks.
    backend.require_role_assignable(guild_id, bot_id, role_id)
    if "permissions" in body and body["permissions"] is not None:
        backend.require_can_grant(guild_id, bot_id, int(body["permissions"]))

    changes: dict[str, Any] = {
        key: body[key] for key in ("name", "hoist", "mentionable", "color") if body.get(key) is not None
    }
    if "permissions" in body and body["permissions"] is not None:
        changes["permissions"] = int(body["permissions"])
    role = backend.edit_role(guild_id, role_id, changes)
    return dict(serializers.role_payload(role))


@route("DELETE", "/guilds/{guild_id}/roles/{role_id}")
def delete_role(ctx: RequestContext) -> Any:
    ctx.require_guild_permissions(ctx.int_arg("guild_id"), "manage_roles")
    ctx.backend.delete_role(ctx.int_arg("guild_id"), ctx.int_arg("role_id"))


@route("GET", "/guilds/{guild_id}/roles")
def get_roles(ctx: RequestContext) -> Any:
    guild = ctx.backend.get_guild(ctx.int_arg("guild_id"))
    return [dict(serializers.role_payload(r)) for r in guild.roles.values()]
