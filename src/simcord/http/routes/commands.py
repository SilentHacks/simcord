"""Application command registration routes (the sync endpoints)."""

from __future__ import annotations

from typing import Any

from ..router import RequestContext, route


@route("PUT", "/applications/{application_id}/commands")
def bulk_upsert_global_commands(ctx: RequestContext) -> Any:
    return ctx.backend.register_commands(None, ctx.json or [])


@route("PUT", "/applications/{application_id}/guilds/{guild_id}/commands")
def bulk_upsert_guild_commands(ctx: RequestContext) -> Any:
    return ctx.backend.register_commands(ctx.int_arg("guild_id"), ctx.json or [])


@route("GET", "/applications/{application_id}/commands")
def get_global_commands(ctx: RequestContext) -> Any:
    return list(ctx.backend.commands.get(None, {}).values())


@route("GET", "/applications/{application_id}/guilds/{guild_id}/commands")
def get_guild_commands(ctx: RequestContext) -> Any:
    return list(ctx.backend.commands.get(ctx.int_arg("guild_id"), {}).values())
