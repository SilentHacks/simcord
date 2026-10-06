"""Application command registration routes (the sync endpoints)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...backend import errors
from ...backend.ops.commands import COMMAND_FIELDS
from ..router import RequestContext, route


def _bulk_upsert_commands(ctx: RequestContext, guild_id: int | None) -> list[dict[str, Any]]:
    payloads = []
    for payload in ctx.json or []:
        if not isinstance(payload, Mapping):
            raise ValueError("Command payloads must be objects")
        normalized_payload = dict(payload)
        if normalized_payload.get("nsfw") is False:
            normalized_payload.pop("nsfw")
        payloads.append(ctx.fields(*COMMAND_FIELDS, body=normalized_payload))
    return ctx.backend.register_commands(guild_id, payloads)


@route("PUT", "/applications/{application_id}/commands")
def bulk_upsert_global_commands(ctx: RequestContext) -> Any:
    return _bulk_upsert_commands(ctx, None)


@route("PUT", "/applications/{application_id}/guilds/{guild_id}/commands")
def bulk_upsert_guild_commands(ctx: RequestContext) -> Any:
    return _bulk_upsert_commands(ctx, ctx.int_arg("guild_id"))


@route("GET", "/applications/{application_id}/commands")
def get_global_commands(ctx: RequestContext) -> Any:
    return list(ctx.backend.commands.get(None, {}).values())


@route("GET", "/applications/{application_id}/guilds/{guild_id}/commands")
def get_guild_commands(ctx: RequestContext) -> Any:
    return list(ctx.backend.commands.get(ctx.int_arg("guild_id"), {}).values())


@route("GET", "/applications/{application_id}/guilds/{guild_id}/commands/{command_id}/permissions")
def get_command_permissions(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    command_id = ctx.int_arg("command_id")
    permissions = backend.get_command_permissions(guild_id, command_id)
    # Discord 404s when a command's permissions are unchanged from the guild
    # default (i.e. never customised) — discord.py surfaces that as NotFound.
    if permissions is None:
        raise errors.unknown_command_permissions()
    return {
        "id": str(command_id),
        "application_id": ctx.args["application_id"],
        "guild_id": str(guild_id),
        "permissions": list(permissions),
    }
