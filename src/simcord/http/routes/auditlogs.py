"""Audit-log route: GET the guild's recorded moderation/management actions."""

from __future__ import annotations

from typing import Any

from ...backend import serializers
from ..router import RequestContext, route


@route("GET", "/guilds/{guild_id}/audit-logs")
def get_audit_logs(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, "view_audit_log")
    guild = backend.get_guild(guild_id)
    entries = list(guild.audit_log_entries)
    if "user_id" in ctx.params:
        user_id = int(ctx.params["user_id"])
        entries = [e for e in entries if e.user_id == user_id]
    if "action_type" in ctx.params:
        action_type = int(ctx.params["action_type"])
        entries = [e for e in entries if e.action_type == action_type]
    if "before" in ctx.params:
        before = int(ctx.params["before"])
        entries = [e for e in entries if e.id < before]
    if "after" in ctx.params:
        after = int(ctx.params["after"])
        entries = [e for e in entries if e.id > after]
    limit = min(int(ctx.params.get("limit", 50)), 100)
    # Newest first, then keep at most `limit`.
    entries = sorted(entries, key=lambda e: e.id)[-limit:]
    return serializers.audit_log_payload(backend, entries)
