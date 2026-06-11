"""Message routes: send, fetch, history, edit, delete, pins."""

from __future__ import annotations

from typing import Any

from ...backend import serializers
from .._helpers import bot_message, message_response
from ..router import RequestContext, route


def _send_permissions(ctx: RequestContext, channel_id: int) -> None:
    backend = ctx.backend
    channel = backend.get_channel(channel_id)
    perm = "send_messages_in_threads" if channel.is_thread else "send_messages"
    backend.require_permissions(channel.guild_id, backend.bot_user.id, channel_id, perm)


@route("POST", "/channels/{channel_id}/messages")
def send_message(ctx: RequestContext) -> Any:
    channel_id = ctx.int_arg("channel_id")
    _send_permissions(ctx, channel_id)
    return message_response(ctx, bot_message(ctx, channel_id))


@route("GET", "/channels/{channel_id}/messages/{message_id}")
def get_message(ctx: RequestContext) -> Any:
    backend = ctx.backend
    message = backend.get_message(ctx.int_arg("channel_id"), ctx.int_arg("message_id"))
    return dict(serializers.message_payload(backend, message, for_user=backend.bot_user.id))


@route("GET", "/channels/{channel_id}/messages")
def get_history(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    backend.get_channel(channel_id)
    limit = int(ctx.params.get("limit", 50))
    messages = sorted(backend.messages[channel_id].values(), key=lambda m: m.id, reverse=True)
    if "before" in ctx.params:
        messages = [m for m in messages if m.id < int(ctx.params["before"])]
    if "after" in ctx.params:
        messages = [m for m in messages if m.id > int(ctx.params["after"])]
    return [dict(serializers.message_payload(backend, m)) for m in messages[:limit]]


@route("PATCH", "/channels/{channel_id}/messages/{message_id}")
def edit_message(ctx: RequestContext) -> Any:
    message = ctx.backend.edit_message(ctx.int_arg("channel_id"), ctx.int_arg("message_id"), ctx.body())
    return message_response(ctx, message)


@route("DELETE", "/channels/{channel_id}/messages/{message_id}")
def delete_message(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    message = backend.get_message(channel_id, ctx.int_arg("message_id"))
    channel = backend.get_channel(channel_id)
    if message.author_id != backend.bot_user.id and channel.guild_id is not None:
        backend.require_permissions(channel.guild_id, backend.bot_user.id, channel_id, "manage_messages")
    backend.delete_message(channel_id, message.id)


# Pins use Discord's current paginated endpoints (under /messages/pins).


@route("PUT", "/channels/{channel_id}/messages/pins/{message_id}")
def pin_message(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    channel = backend.get_channel(channel_id)
    backend.require_permissions(channel.guild_id, backend.bot_user.id, channel_id, "manage_messages")
    backend.set_pinned(channel_id, ctx.int_arg("message_id"), True)


@route("DELETE", "/channels/{channel_id}/messages/pins/{message_id}")
def unpin_message(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    channel = backend.get_channel(channel_id)
    backend.require_permissions(channel.guild_id, backend.bot_user.id, channel_id, "manage_messages")
    backend.set_pinned(channel_id, ctx.int_arg("message_id"), False)


@route("GET", "/channels/{channel_id}/messages/pins")
def get_pins(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    backend.get_channel(channel_id)
    items = [
        {"message": dict(serializers.message_payload(backend, m)), "pinned_at": backend.now_iso()}
        for m in backend.messages[channel_id].values()
        if m.pinned
    ]
    return {"items": items, "has_more": False}
