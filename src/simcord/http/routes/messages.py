"""Message routes: send, fetch, history, edit, delete, pins."""

from __future__ import annotations

from typing import Any

from ...backend import errors, serializers
from ...enums import AuditLogAction, ChannelType
from .._helpers import bot_message, message_edit_changes, message_response
from ..router import RequestContext, route


def _send_permissions(ctx: RequestContext, channel_id: int) -> None:
    channel = ctx.backend.get_channel(channel_id)
    if channel.type == ChannelType.DM:
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
    message = backend.edit_message(channel_id, message.id, message_edit_changes(ctx))
    return message_response(ctx, message)


@route("POST", "/channels/{channel_id}/messages/bulk-delete")
def bulk_delete_messages(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_messages")
    ids = [int(m) for m in ctx.fields("messages").get("messages") or []]
    # Discord's bulk-delete endpoint only accepts 2-100 messages; discord.py
    # uses single delete below that, so a route hit outside this range is a
    # malformed call.
    if not 2 <= len(ids) <= 100:
        raise errors.invalid_form_body("messages: Must be between 2 and 100 in length")
    deleted = backend.bulk_delete_messages(channel.id, ids)
    if channel.guild_id is not None and deleted:
        backend.record_audit_log(
            channel.guild_id,
            AuditLogAction.MESSAGE_BULK_DELETE,
            target_id=channel.id,
            options={"count": str(len(deleted))},
            reason=ctx.reason,
        )


@route("POST", "/channels/{channel_id}/messages/{message_id}/crosspost")
def crosspost_message(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    channel = ctx.require_channel_permissions(channel_id, "send_messages")
    if channel.type != ChannelType.NEWS:
        # Only announcement (news) channels can crosspost; real Discord 50024s.
        raise errors.cannot_execute_on_channel_type()
    message = backend.get_message(channel_id, ctx.int_arg("message_id"))
    return message_response(ctx, backend.crosspost_message(channel_id, message.id))


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


@route("POST", "/channels/{channel_id}/polls/{message_id}/expire")
def expire_poll(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    message = backend.get_message(channel_id, ctx.int_arg("message_id"))
    if message.author_id != backend.bot_user.id:
        raise errors.cannot_edit_other_user()
    message = backend.expire_poll(channel_id, message.id)
    return dict(serializers.message_payload(backend, message))


@route("GET", "/channels/{channel_id}/polls/{message_id}/answers/{answer_id}")
def get_poll_answer_voters(ctx: RequestContext) -> Any:
    backend = ctx.backend
    message = backend.get_message(ctx.int_arg("channel_id"), ctx.int_arg("message_id"))
    answer_id = ctx.int_arg("answer_id")
    voters = message.poll.votes.get(answer_id, set()) if message.poll else set()
    limit = int(ctx.params.get("limit", 25))
    users = [serializers.user_payload(backend.users[uid]) for uid in voters if uid in backend.users]
    return {"users": users[:limit]}


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
