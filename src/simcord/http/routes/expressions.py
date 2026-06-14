"""Guild expression routes: custom emojis and stickers (CRUD)."""

from __future__ import annotations

from typing import Any

from ...backend import serializers
from ..router import RequestContext, route

_EXPRESSION_PERM = "manage_expressions"


@route("GET", "/guilds/{guild_id}/emojis")
def list_emojis(ctx: RequestContext) -> Any:
    guild = ctx.backend.get_guild(ctx.int_arg("guild_id"))
    return [serializers.guild_emoji_payload(ctx.backend, e) for e in guild.emojis.values()]


@route("POST", "/guilds/{guild_id}/emojis")
def create_emoji(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, _EXPRESSION_PERM)
    body = ctx.body()
    emoji = backend.create_emoji(
        guild_id,
        body["name"],
        backend.bot_user.id,
        role_ids=[int(r) for r in body.get("roles") or []],
    )
    return serializers.guild_emoji_payload(backend, emoji)


@route("GET", "/guilds/{guild_id}/emojis/{emoji_id}")
def get_emoji(ctx: RequestContext) -> Any:
    backend = ctx.backend
    return serializers.guild_emoji_payload(
        backend, backend.get_emoji(ctx.int_arg("guild_id"), ctx.int_arg("emoji_id"))
    )


@route("PATCH", "/guilds/{guild_id}/emojis/{emoji_id}")
def edit_emoji(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, _EXPRESSION_PERM)
    body = ctx.fields("name", "roles")
    changes: dict[str, Any] = {}
    if "name" in body:
        changes["name"] = body["name"]
    if "roles" in body:
        changes["role_ids"] = [int(r) for r in body["roles"] or []]
    emoji = backend.edit_emoji(guild_id, ctx.int_arg("emoji_id"), changes)
    return serializers.guild_emoji_payload(backend, emoji)


@route("DELETE", "/guilds/{guild_id}/emojis/{emoji_id}")
def delete_emoji(ctx: RequestContext) -> Any:
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, _EXPRESSION_PERM)
    ctx.backend.delete_emoji(guild_id, ctx.int_arg("emoji_id"))


@route("GET", "/guilds/{guild_id}/stickers")
def list_stickers(ctx: RequestContext) -> Any:
    guild = ctx.backend.get_guild(ctx.int_arg("guild_id"))
    return [serializers.sticker_payload(ctx.backend, s) for s in guild.stickers.values()]


@route("POST", "/guilds/{guild_id}/stickers")
def create_sticker(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, _EXPRESSION_PERM)
    body = ctx.body()
    sticker = backend.create_sticker(
        guild_id,
        body["name"],
        backend.bot_user.id,
        description=body.get("description"),
        tags=body.get("tags") or "",
    )
    return serializers.sticker_payload(backend, sticker)


@route("GET", "/guilds/{guild_id}/stickers/{sticker_id}")
def get_sticker(ctx: RequestContext) -> Any:
    backend = ctx.backend
    return serializers.sticker_payload(
        backend, backend.get_sticker(ctx.int_arg("guild_id"), ctx.int_arg("sticker_id"))
    )


@route("PATCH", "/guilds/{guild_id}/stickers/{sticker_id}")
def edit_sticker(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, _EXPRESSION_PERM)
    changes = ctx.fields("name", "description", "tags")
    sticker = backend.edit_sticker(guild_id, ctx.int_arg("sticker_id"), changes)
    return serializers.sticker_payload(backend, sticker)


@route("DELETE", "/guilds/{guild_id}/stickers/{sticker_id}")
def delete_sticker(ctx: RequestContext) -> Any:
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, _EXPRESSION_PERM)
    ctx.backend.delete_sticker(guild_id, ctx.int_arg("sticker_id"))
