"""Application command registration routes (the sync endpoints)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...backend import errors
from ...backend.ops.commands import COMMAND_FIELDS
from ..router import RequestContext, route

_HTTP_COMMAND_FIELDS = tuple(name for name in COMMAND_FIELDS if name != "nsfw")


def _command_fields(ctx: RequestContext, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise errors.invalid_form_body("Command payloads must be objects")
    unsupported = (
        {"nsfw": "NSFW command visibility is not modeled."} if payload.get("nsfw", False) is not False else {}
    )
    return ctx.fields(*_HTTP_COMMAND_FIELDS, ignore=("nsfw",), reject=unsupported, body=dict(payload))


def _bulk_upsert_commands(ctx: RequestContext, guild_id: int | None) -> list[dict[str, Any]]:
    if not isinstance(ctx.json, list):
        raise errors.invalid_form_body("Bulk command registration requires an array")
    return ctx.backend.register_commands(guild_id, [_command_fields(ctx, payload) for payload in ctx.json])


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


@route("POST", "/applications/{application_id}/guilds/{guild_id}/commands")
def create_guild_command(ctx: RequestContext) -> Any:
    return ctx.backend.upsert_command(ctx.int_arg("guild_id"), _command_fields(ctx, ctx.json))


@route("GET", "/applications/{application_id}/guilds/{guild_id}/commands/{command_id}")
def get_guild_command(ctx: RequestContext) -> Any:
    return ctx.backend.get_command(ctx.int_arg("guild_id"), ctx.int_arg("command_id"))


@route("PATCH", "/applications/{application_id}/guilds/{guild_id}/commands/{command_id}")
def edit_guild_command(ctx: RequestContext) -> Any:
    return ctx.backend.upsert_command(
        ctx.int_arg("guild_id"), _command_fields(ctx, ctx.json), command_id=ctx.int_arg("command_id")
    )


@route("DELETE", "/applications/{application_id}/guilds/{guild_id}/commands/{command_id}")
def delete_guild_command(ctx: RequestContext) -> Any:
    ctx.backend.delete_command(ctx.int_arg("guild_id"), ctx.int_arg("command_id"))
    return None


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
