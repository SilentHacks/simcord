"""Auto-moderation routes: rule CRUD. (Execution happens on message send.)"""

from __future__ import annotations

from typing import Any

from ...backend import serializers
from ..router import RequestContext, route

_AUTOMOD_PERM = "manage_guild"


@route("GET", "/guilds/{guild_id}/auto-moderation/rules")
def list_rules(ctx: RequestContext) -> Any:
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, _AUTOMOD_PERM)
    guild = ctx.backend.get_guild(guild_id)
    return [serializers.auto_mod_rule_payload(ctx.backend, r) for r in guild.auto_mod_rules.values()]


@route("POST", "/guilds/{guild_id}/auto-moderation/rules")
def create_rule(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, _AUTOMOD_PERM)
    rule = backend.create_auto_mod_rule(guild_id, backend.bot_user.id, ctx.body())
    return serializers.auto_mod_rule_payload(backend, rule)


@route("GET", "/guilds/{guild_id}/auto-moderation/rules/{rule_id}")
def get_rule(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, _AUTOMOD_PERM)
    return serializers.auto_mod_rule_payload(
        backend, backend.get_auto_mod_rule(guild_id, ctx.int_arg("rule_id"))
    )


@route("PATCH", "/guilds/{guild_id}/auto-moderation/rules/{rule_id}")
def edit_rule(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, _AUTOMOD_PERM)
    body = ctx.body()
    field_map = {
        "name": "name",
        "event_type": "event_type",
        "trigger_type": "trigger_type",
        "trigger_metadata": "trigger_metadata",
        "actions": "actions",
        "enabled": "enabled",
    }
    changes: dict[str, Any] = {attr: body[key] for key, attr in field_map.items() if key in body}
    if "exempt_roles" in body:
        changes["exempt_roles"] = [int(r) for r in body["exempt_roles"] or []]
    if "exempt_channels" in body:
        changes["exempt_channels"] = [int(c) for c in body["exempt_channels"] or []]
    rule = backend.edit_auto_mod_rule(guild_id, ctx.int_arg("rule_id"), changes)
    return serializers.auto_mod_rule_payload(backend, rule)


@route("DELETE", "/guilds/{guild_id}/auto-moderation/rules/{rule_id}")
def delete_rule(ctx: RequestContext) -> Any:
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, _AUTOMOD_PERM)
    ctx.backend.delete_auto_mod_rule(guild_id, ctx.int_arg("rule_id"))
