"""Message routes: send, fetch, history, edit, delete, pins."""

from __future__ import annotations

from typing import Any

from ...backend import errors, serializers
from .._helpers import bot_message, message_response
from ..router import RequestContext, route


def _send_permissions(ctx: RequestContext, channel_id: int) -> None:
    channel = ctx.backend.get_channel(channel_id)
    if channel.type == 1:  # DM channel
        # You cannot DM another bot; real Discord rejects this on send (50007).
        if any(ctx.backend.users[uid].bot for uid in channel.recipient_ids):
            raise errors.cannot_dm_bot()
        return
    perm = "send_messages_in_threads" if channel.is_thread else "send_messages"
    ctx.require_channel_permissions(channel_id, perm)


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
    # Ephemeral messages are never returned by the channel-messages endpoint,
    # just as they are invisible in a real client's channel scrollback.
    visible = [m for m in backend.messages[channel_id].values() if not m.is_ephemeral]
    if "around" in ctx.params:
        around = int(ctx.params["around"])
        ascending = sorted(visible, key=lambda m: m.id)
        pivot = min(range(len(ascending)), key=lambda i: abs(ascending[i].id - around), default=None)
        if pivot is None:
            return []
        half = limit // 2
        window = ascending[max(0, pivot - half) : pivot + half + 1]
        return [dict(serializers.message_payload(backend, m)) for m in reversed(window[:limit])]
    messages = sorted(visible, key=lambda m: m.id, reverse=True)
    if "before" in ctx.params:
        messages = [m for m in messages if m.id < int(ctx.params["before"])]
    if "after" in ctx.params:
        messages = [m for m in messages if m.id > int(ctx.params["after"])]
    return [dict(serializers.message_payload(backend, m)) for m in messages[:limit]]


@route("PATCH", "/channels/{channel_id}/messages/{message_id}")
def edit_message(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    message = backend.get_message(channel_id, ctx.int_arg("message_id"))
    # Real Discord only lets you edit your own messages (50005); the bot editing
    # someone else's message is exactly the kind of bug this framework surfaces.
    if message.author_id != backend.bot_user.id:
        raise errors.cannot_edit_other_user()
    message = backend.edit_message(channel_id, message.id, ctx.body())
    return message_response(ctx, message)


@route("DELETE", "/channels/{channel_id}/messages/{message_id}")
def delete_message(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    message = backend.get_message(channel_id, ctx.int_arg("message_id"))
    channel = backend.get_channel(channel_id)
    if message.author_id != backend.bot_user.id and channel.guild_id is not None:
        ctx.require_channel_permissions(channel_id, "manage_messages")
    backend.delete_message(channel_id, message.id)


# Pins use Discord's current paginated endpoints (under /messages/pins).


@route("PUT", "/channels/{channel_id}/messages/pins/{message_id}")
def pin_message(ctx: RequestContext) -> Any:
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_messages")
    ctx.backend.set_pinned(channel.id, ctx.int_arg("message_id"), True)


@route("DELETE", "/channels/{channel_id}/messages/pins/{message_id}")
def unpin_message(ctx: RequestContext) -> Any:
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_messages")
    ctx.backend.set_pinned(channel.id, ctx.int_arg("message_id"), False)


@route("GET", "/channels/{channel_id}/messages/pins")
def get_pins(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    backend.get_channel(channel_id)
    items = [
        {"message": dict(serializers.message_payload(backend, m)), "pinned_at": backend.now_iso()}
        for m in backend.messages[channel_id].values()
        if m.pinned and not m.is_ephemeral
    ]
    return {"items": items, "has_more": False}
