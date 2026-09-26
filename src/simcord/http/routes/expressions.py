"""Guild expression routes: custom emojis and stickers (CRUD)."""

import json
from typing import Any

from ...backend import errors, serializers
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
    # image (CDN bytes) is accepted and discarded — no image storage offline.
    body = ctx.fields("name", "roles", ignore=("image",))
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
    body = ctx.fields("name", "description", "tags")
    if len(ctx.files) != 1:
        raise errors.invalid_form_body("sticker creation requires exactly one uploaded file")
    file = ctx.files[0]
    data = file.fp.read()
    if not data or len(data) > 512 * 1024:
        raise errors.invalid_form_body("sticker upload must be between 1 byte and 512 KiB")
    filename = str(file.filename)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        format_type = 1
        offset = 8
        while offset + 12 <= len(data):
            size = int.from_bytes(data[offset : offset + 4], "big")
            if data[offset + 4 : offset + 8] == b"acTL":
                format_type = 2
                break
            offset += size + 12
            if offset > len(data):
                break
        content_type = "image/png"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        format_type, content_type = 4, "image/gif"
    elif filename.casefold().endswith(".json"):
        try:
            lottie = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise errors.invalid_form_body("Lottie sticker upload must be valid JSON") from exc
        if not isinstance(lottie, dict) or not isinstance(lottie.get("layers"), list):
            raise errors.invalid_form_body("Lottie sticker upload must contain a layers array")
        format_type, content_type = 3, "application/json"
    else:
        raise errors.invalid_form_body("sticker upload must be PNG, APNG, GIF, or Lottie JSON")
    sticker = backend.create_sticker(
        guild_id,
        body["name"],
        backend.bot_user.id,
        description=body.get("description"),
        tags=body.get("tags") or "",
        format_type=format_type,
        file_data=data,
        filename=filename,
        content_type=content_type,
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
