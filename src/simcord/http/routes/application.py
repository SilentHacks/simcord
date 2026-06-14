"""Application/user-level routes: identity, application info, DM channels."""

from __future__ import annotations

from typing import Any

from ...backend import serializers
from ..router import RequestContext, route


@route("GET", "/users/@me")
def me(ctx: RequestContext) -> Any:
    return dict(serializers.user_payload(ctx.backend.bot_user))


@route("GET", "/users/{user_id}")
def get_user(ctx: RequestContext) -> Any:
    return dict(serializers.user_payload(ctx.backend.get_user(ctx.int_arg("user_id"))))


@route("GET", "/oauth2/applications/@me")
def application_info(ctx: RequestContext) -> Any:
    backend = ctx.backend
    bot = dict(serializers.user_payload(backend.bot_user))
    return {
        "id": str(backend.application_id),
        "name": backend.bot_user.name,
        "icon": None,
        "description": "",
        "summary": "",
        "bot_public": True,
        "bot_require_code_grant": False,
        "verify_key": "0" * 64,
        "owner": bot,
        "flags": 0,
        "team": None,
        "bot": bot,
    }


@route("GET", "/applications/{application_id}/emojis")
def list_application_emojis(ctx: RequestContext) -> Any:
    backend = ctx.backend
    return {
        "items": [serializers.guild_emoji_payload(backend, e) for e in backend.application_emojis.values()]
    }


@route("POST", "/applications/{application_id}/emojis")
def create_application_emoji(ctx: RequestContext) -> Any:
    backend = ctx.backend
    emoji = backend.create_application_emoji(ctx.body()["name"])
    return serializers.guild_emoji_payload(backend, emoji)


@route("GET", "/applications/{application_id}/emojis/{emoji_id}")
def get_application_emoji(ctx: RequestContext) -> Any:
    backend = ctx.backend
    return serializers.guild_emoji_payload(backend, backend.get_application_emoji(ctx.int_arg("emoji_id")))


@route("PATCH", "/applications/{application_id}/emojis/{emoji_id}")
def edit_application_emoji(ctx: RequestContext) -> Any:
    backend = ctx.backend
    emoji = backend.edit_application_emoji(ctx.int_arg("emoji_id"), ctx.fields("name").get("name"))
    return serializers.guild_emoji_payload(backend, emoji)


@route("DELETE", "/applications/{application_id}/emojis/{emoji_id}")
def delete_application_emoji(ctx: RequestContext) -> Any:
    ctx.backend.delete_application_emoji(ctx.int_arg("emoji_id"))


@route("POST", "/users/@me/channels")
def create_dm(ctx: RequestContext) -> Any:
    # Real Discord happily opens the DM channel even for a bot recipient; the
    # 50007 only surfaces when a message is actually sent (see send_message).
    recipient_id = int(ctx.body()["recipient_id"])
    ctx.backend.get_user(recipient_id)
    channel = ctx.backend.get_dm_channel(recipient_id)
    return dict(serializers.channel_payload(ctx.backend, channel))
