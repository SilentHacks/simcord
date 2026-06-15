"""Guild routes: members, moderation (kick/ban/timeout), roles."""

from __future__ import annotations

from typing import Any

from ...backend import errors, serializers
from ...backend.models import Overwrite
from ...enums import AuditLogAction, ChannelType, OverwriteType
from ..router import RequestContext, route


@route("POST", "/guilds")
def create_guild(ctx: RequestContext) -> Any:
    # A bot that creates a guild becomes its owner, exactly as on real Discord.
    # ``icon`` is accepted and discarded: there is no image rendering offline.
    backend = ctx.backend
    body = ctx.fields("name", ignore=("icon",))
    guild = backend.create_guild(body.get("name") or "Guild", owner_id=backend.bot_user.id)
    return dict(serializers.guild_create_payload(backend, guild))


@route("GET", "/guilds/{guild_id}")
def get_guild(ctx: RequestContext) -> Any:
    return dict(serializers.guild_create_payload(ctx.backend, ctx.backend.get_guild(ctx.int_arg("guild_id"))))


# Guild fields a bot can edit (`Guild.edit`), mapped 1:1 onto the backend model.
# Nullable fields may be set to None to clear them; channel ids are coerced to int.
_GUILD_EDITABLE = (
    "name",
    "description",
    "preferred_locale",
    "afk_timeout",
    "verification_level",
    "default_message_notifications",
    "explicit_content_filter",
    "afk_channel_id",
    "system_channel_id",
    "rules_channel_id",
    "public_updates_channel_id",
)
_GUILD_CHANNEL_FIELDS = frozenset(
    {"afk_channel_id", "system_channel_id", "rules_channel_id", "public_updates_channel_id"}
)


@route("PATCH", "/guilds/{guild_id}")
def edit_guild(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, "manage_guild")
    body = ctx.fields(*_GUILD_EDITABLE)
    changes: dict[str, Any] = {}
    for key in _GUILD_EDITABLE:
        if key not in body:
            continue
        value = body[key]
        if key in _GUILD_CHANNEL_FIELDS and value is not None:
            value = int(value)
        changes[key] = value
    old = {key: getattr(backend.get_guild(guild_id), key) for key in changes}
    guild = backend.edit_guild(guild_id, changes)
    audit_changes = [
        {"key": key, "old_value": old[key], "new_value": getattr(guild, key)}
        for key in changes
        if old[key] != getattr(guild, key)
    ]
    if audit_changes:
        backend.record_audit_log(
            guild_id,
            AuditLogAction.GUILD_UPDATE,
            target_id=guild_id,
            changes=audit_changes,
            reason=ctx.reason,
        )
    return dict(serializers.guild_create_payload(backend, guild))


@route("GET", "/guilds/{guild_id}/threads/active")
def active_guild_threads(ctx: RequestContext) -> Any:
    backend = ctx.backend
    threads = backend.active_threads(ctx.int_arg("guild_id"))
    return serializers.thread_list_payload(backend, threads)


@route("POST", "/guilds/{guild_id}/channels")
def create_guild_channel(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, "manage_channels")
    # Scalar fields mapped 1:1 onto the Channel model; overwrites are applied
    # separately. Anything else discord.py can send on create (position,
    # video_quality_mode, default_* forum settings, ...) is unmodelled and so
    # fails loudly rather than being silently dropped.
    body = ctx.fields(
        "name",
        "type",
        "topic",
        "nsfw",
        "rate_limit_per_user",
        "bitrate",
        "user_limit",
        "rtc_region",
        "parent_id",
        ignore=("permission_overwrites",),
    )
    overwrites = [
        Overwrite(
            target_id=int(o["id"]),
            type=OverwriteType(int(o["type"])),
            allow=int(o.get("allow", 0)),
            deny=int(o.get("deny", 0)),
        )
        for o in ctx.body().get("permission_overwrites") or []
    ]
    fields: dict[str, Any] = {}
    for key in ("topic", "nsfw", "rate_limit_per_user", "bitrate", "user_limit", "rtc_region"):
        if body.get(key) is not None:
            fields[key] = body[key]
    if body.get("parent_id") is not None:
        fields["parent_id"] = int(body["parent_id"])
    channel = backend.create_channel(
        guild_id,
        body.get("name"),
        type=int(body.get("type", ChannelType.TEXT)),
        overwrites=overwrites,
        **fields,
    )
    backend.record_audit_log(
        guild_id,
        AuditLogAction.CHANNEL_CREATE,
        target_id=channel.id,
        changes=[{"key": "name", "new_value": channel.name}],
        reason=ctx.reason,
    )
    return dict(serializers.channel_payload(backend, channel))


@route("GET", "/guilds/{guild_id}/channels")
def get_guild_channels(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild = backend.get_guild(ctx.int_arg("guild_id"))
    return [dict(serializers.channel_payload(backend, backend.channels[cid])) for cid in guild.channel_ids]


@route("GET", "/guilds/{guild_id}/members")
def list_members(ctx: RequestContext) -> Any:
    # The list-members endpoint discord.py's Guild.fetch_members pages through;
    # member *search* by name is the gateway's REQUEST_GUILD_MEMBERS path
    # (Guild.query_members), already served by the fake websocket.
    backend = ctx.backend
    guild = backend.get_guild(ctx.int_arg("guild_id"))
    limit = int(ctx.params.get("limit", 1))
    after = int(ctx.params.get("after", 0))
    members = [m for m in sorted(guild.members.values(), key=lambda m: m.user_id) if m.user_id > after]
    return [dict(serializers.member_payload(backend, guild, m)) for m in members[:limit]]


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
    body = ctx.fields("nick", "roles", "communication_disabled_until", "mute", "deaf", "channel_id")
    bot_id = backend.bot_user.id

    # Validate every change before applying any of them, then hand the whole set
    # to the backend so the write and its GUILD_MEMBER_UPDATE stay atomic — a
    # partial edit that 403s halfway would desync the bot's cache.
    changes: dict[str, Any] = {}
    log: list[tuple[int, dict[str, Any], dict[str, Any]]] = []  # (action_type, changes, options)
    old_nick, old_timeout, old_roles = member.nick, member.timed_out_until, list(member.role_ids)
    old_mute, old_deaf = member.mute, member.deaf
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
            ctx.require_guild_permissions(guild_id, "moderate_members" if key == "deaf" else "mute_members")
            changes[key] = body[key]

    member = backend.edit_member(guild_id, user_id, changes)

    # Voice channel move/disconnect (server-side); reflect server mute/deaf too.
    if "channel_id" in body:
        target_channel = body["channel_id"]
        if user_id not in guild.voice_states:
            raise errors.target_not_connected_to_voice()
        if target_channel is None:
            backend.set_voice_state(guild_id, user_id, None)
            log.append((AuditLogAction.MEMBER_DISCONNECT, {}, {}))
        else:
            backend.set_voice_state(
                guild_id, user_id, int(target_channel), mute=member.mute, deaf=member.deaf
            )
            log.append((AuditLogAction.MEMBER_MOVE, {}, {"channel_id": str(target_channel), "count": "1"}))
    elif user_id in guild.voice_states and ("mute" in body or "deaf" in body):
        backend.set_voice_state(
            guild_id, user_id, guild.voice_states[user_id].channel_id, mute=member.mute, deaf=member.deaf
        )

    member_changes: list[dict[str, Any]] = []
    if "nick" in changes and old_nick != member.nick:
        member_changes.append({"key": "nick", "old_value": old_nick, "new_value": member.nick})
    if "timed_out_until" in changes and old_timeout != member.timed_out_until:
        member_changes.append(
            {
                "key": "communication_disabled_until",
                "old_value": old_timeout,
                "new_value": member.timed_out_until,
            }
        )
    if "mute" in changes and old_mute != member.mute:
        member_changes.append({"key": "mute", "old_value": old_mute, "new_value": member.mute})
    if "deaf" in changes and old_deaf != member.deaf:
        member_changes.append({"key": "deaf", "old_value": old_deaf, "new_value": member.deaf})
    if member_changes:
        log.append((AuditLogAction.MEMBER_UPDATE, {"_changes": member_changes}, {}))
    if "role_ids" in changes:
        added = [r for r in member.role_ids if r not in old_roles]
        removed = [r for r in old_roles if r not in member.role_ids]
        options: dict[str, Any] = {}
        if added:
            options["$add"] = [{"id": str(r), "name": backend.get_role(guild_id, r).name} for r in added]
        if removed:
            options["$remove"] = [{"id": str(r), "name": backend.get_role(guild_id, r).name} for r in removed]
        if options:
            log.append((AuditLogAction.MEMBER_ROLE_UPDATE, {}, options))

    for action_type, extra, options in log:
        backend.record_audit_log(
            guild_id,
            action_type,
            target_id=user_id,
            changes=extra.get("_changes", []),
            options=options,
            reason=ctx.reason,
        )
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
    backend.record_audit_log(
        guild_id,
        AuditLogAction.MEMBER_ROLE_UPDATE,
        target_id=user_id,
        options={"$add": [{"id": str(role.id), "name": role.name}]},
        reason=ctx.reason,
    )


@route("DELETE", "/guilds/{guild_id}/members/{user_id}/roles/{role_id}")
def remove_member_role(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    user_id = ctx.int_arg("user_id")
    role_id = ctx.int_arg("role_id")
    ctx.require_guild_permissions(guild_id, "manage_roles")
    backend.get_member(guild_id, user_id)
    backend.require_role_assignable(guild_id, backend.bot_user.id, role_id)
    role = backend.get_role(guild_id, role_id)
    backend.remove_member_role(guild_id, user_id, role_id)
    backend.record_audit_log(
        guild_id,
        AuditLogAction.MEMBER_ROLE_UPDATE,
        target_id=user_id,
        options={"$remove": [{"id": str(role.id), "name": role.name}]},
        reason=ctx.reason,
    )


@route("DELETE", "/guilds/{guild_id}/members/{user_id}")
def kick(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    user_id = ctx.int_arg("user_id")
    ctx.require_guild_permissions(guild_id, "kick_members")
    backend.require_hierarchy(guild_id, backend.bot_user.id, user_id)
    backend.remove_member(guild_id, user_id)
    backend.record_audit_log(guild_id, AuditLogAction.MEMBER_KICK, target_id=user_id, reason=ctx.reason)


@route("PUT", "/guilds/{guild_id}/bans/{user_id}")
def ban(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    user_id = ctx.int_arg("user_id")
    ctx.require_guild_permissions(guild_id, "ban_members")
    backend.require_hierarchy(guild_id, backend.bot_user.id, user_id)
    backend.get_user(user_id)
    backend.apply_ban(guild_id, user_id, ctx.reason)
    backend.record_audit_log(guild_id, AuditLogAction.MEMBER_BAN, target_id=user_id, reason=ctx.reason)


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
    backend.record_audit_log(guild_id, AuditLogAction.MEMBER_UNBAN, target_id=user_id, reason=ctx.reason)


@route("POST", "/guilds/{guild_id}/bulk-ban")
def bulk_ban(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, "ban_members", "manage_guild")
    guild = backend.get_guild(guild_id)
    user_ids = [int(u) for u in ctx.body().get("user_ids") or []]
    if not user_ids:
        # Real Discord 400s a bulk ban with no users; the per-user failed list is
        # how it reports users that simply could not be banned (a 200 response).
        raise errors.invalid_form_body("user_ids: This field is required")
    banned: list[int] = []
    failed: list[int] = []
    for user_id in user_ids:
        if user_id in guild.bans:
            failed.append(user_id)
            continue
        try:
            backend.require_hierarchy(guild_id, backend.bot_user.id, user_id)
        except errors.BackendError:
            failed.append(user_id)
            continue
        backend.apply_ban(guild_id, user_id, ctx.reason)
        backend.record_audit_log(guild_id, AuditLogAction.MEMBER_BAN, target_id=user_id, reason=ctx.reason)
        banned.append(user_id)
    return {
        "banned_users": [str(u) for u in banned],
        "failed_users": [str(u) for u in failed],
    }


def _prunable(backend: Any, guild: Any) -> list[int]:
    """Members eligible for prune: no roles beyond @everyone, never the bot or owner.

    SimCord has no presence/last-activity history, so "inactive" is modelled as
    "roleless" — documented in the moderation guide as a deliberate simplification.
    """
    return [
        uid
        for uid, member in guild.members.items()
        if uid != backend.bot_user.id and uid != guild.owner_id and not member.role_ids
    ]


@route("GET", "/guilds/{guild_id}/prune")
def estimate_prune(ctx: RequestContext) -> Any:
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, "kick_members")
    return {"pruned": len(_prunable(ctx.backend, ctx.backend.get_guild(guild_id)))}


@route("POST", "/guilds/{guild_id}/prune")
def prune_members(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, "kick_members")
    guild = backend.get_guild(guild_id)
    body = ctx.body()
    targets = _prunable(backend, guild)
    for user_id in targets:
        backend.remove_member(guild_id, user_id)
    backend.record_audit_log(
        guild_id,
        AuditLogAction.MEMBER_PRUNE,
        target_id=guild_id,
        options={"delete_member_days": str(body.get("days", 7)), "members_removed": str(len(targets))},
        reason=ctx.reason,
    )
    return {"pruned": len(targets) if body.get("compute_prune_count", True) else None}


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


def _edit_voice_state(ctx: RequestContext, guild_id: int, user_id: int) -> None:
    backend = ctx.backend
    state = backend.get_guild(guild_id).voice_states.get(user_id)
    if state is None:
        raise errors.target_not_connected_to_voice()
    body = ctx.body()
    flags: dict[str, Any] = {}
    if "suppress" in body:
        flags["suppress"] = bool(body["suppress"])
    if "request_to_speak_timestamp" in body:
        flags["request_to_speak_timestamp"] = body["request_to_speak_timestamp"]
    backend.set_voice_state(guild_id, user_id, state.channel_id, **flags)


@route("PATCH", "/guilds/{guild_id}/voice-states/@me")
def edit_my_voice_state(ctx: RequestContext) -> Any:
    # The bot's own stage voice state (request-to-speak / un-suppress).
    _edit_voice_state(ctx, ctx.int_arg("guild_id"), ctx.backend.bot_user.id)


@route("PATCH", "/guilds/{guild_id}/voice-states/{user_id}")
def edit_voice_state(ctx: RequestContext) -> Any:
    # Inviting another member to speak / suppressing them needs mute_members.
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, "mute_members")
    _edit_voice_state(ctx, guild_id, ctx.int_arg("user_id"))


@route("POST", "/guilds/{guild_id}/roles")
def create_role(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    bot_id = backend.bot_user.id
    ctx.require_guild_permissions(guild_id, "manage_roles")
    body = ctx.fields("name", "permissions", "hoist", "mentionable", "color", "colors")
    new_permissions = int(body.get("permissions") or 0)
    backend.require_can_grant(guild_id, bot_id, new_permissions)
    # Newer discord.py sends the gradient-colour object; we model a single colour,
    # so honour its primary (mirrors edit_role).
    color = int(body.get("color") or 0)
    if body.get("colors") is not None and body["colors"].get("primary_color") is not None:
        color = int(body["colors"]["primary_color"])
    role = backend.create_role(
        guild_id,
        body.get("name") or "new role",
        permissions=new_permissions,
        hoist=bool(body.get("hoist", False)),
        mentionable=bool(body.get("mentionable", False)),
        color=color,
    )
    backend.record_audit_log(
        guild_id,
        AuditLogAction.ROLE_CREATE,
        target_id=role.id,
        changes=[{"key": "name", "new_value": role.name}],
        reason=ctx.reason,
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
    body = ctx.fields("name", "hoist", "mentionable", "color", "colors", "permissions")

    # Validate before mutating: a role above the bot can't be edited, and the
    # bot can't grant permissions it lacks.
    backend.require_role_assignable(guild_id, bot_id, role_id)
    if "permissions" in body and body["permissions"] is not None:
        backend.require_can_grant(guild_id, bot_id, int(body["permissions"]))

    changes: dict[str, Any] = {
        key: body[key] for key in ("name", "hoist", "mentionable", "color") if body.get(key) is not None
    }
    # Newer discord.py sends the gradient-colour object; we model a single colour,
    # so honour its primary and let the (unmodelled) gradient parts ride along.
    if body.get("colors") is not None and body["colors"].get("primary_color") is not None:
        changes["color"] = body["colors"]["primary_color"]
    if "permissions" in body and body["permissions"] is not None:
        changes["permissions"] = int(body["permissions"])
    old = {key: getattr(backend.get_role(guild_id, role_id), key) for key in changes}
    role = backend.edit_role(guild_id, role_id, changes)
    audit_changes = [
        {"key": key, "old_value": old[key], "new_value": getattr(role, key)}
        for key in changes
        if old[key] != getattr(role, key)
    ]
    if audit_changes:
        backend.record_audit_log(
            guild_id, AuditLogAction.ROLE_UPDATE, target_id=role_id, changes=audit_changes, reason=ctx.reason
        )
    return dict(serializers.role_payload(role))


@route("DELETE", "/guilds/{guild_id}/roles/{role_id}")
def delete_role(ctx: RequestContext) -> Any:
    guild_id = ctx.int_arg("guild_id")
    role_id = ctx.int_arg("role_id")
    ctx.require_guild_permissions(guild_id, "manage_roles")
    name = ctx.backend.get_role(guild_id, role_id).name
    ctx.backend.delete_role(guild_id, role_id)
    ctx.backend.record_audit_log(
        guild_id,
        AuditLogAction.ROLE_DELETE,
        target_id=role_id,
        changes=[{"key": "name", "old_value": name}],
        reason=ctx.reason,
    )


@route("GET", "/guilds/{guild_id}/roles")
def get_roles(ctx: RequestContext) -> Any:
    guild = ctx.backend.get_guild(ctx.int_arg("guild_id"))
    return [dict(serializers.role_payload(r)) for r in guild.roles.values()]
