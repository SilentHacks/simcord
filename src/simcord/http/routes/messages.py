"""Message routes: send, fetch, history, edit, delete, pins."""

from __future__ import annotations

from typing import Any

from ...backend import errors, permissions, serializers
from ...backend.access import channel_accessible
from ...enums import AuditLogAction, ChannelType
from .._helpers import bot_message, edit_bot_message, message_response
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
    message = ctx.require_message(
        ctx.int_arg("channel_id"), ctx.int_arg("message_id"), "read_message_history"
    )
    return message_response(ctx, message, for_user=backend.bot_user.id)


@route("GET", "/channels/{channel_id}/messages")
def get_history(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    channel = backend.get_channel(channel_id)
    perms = (
        backend.compute_permissions(channel.guild_id, backend.bot_user.id, channel_id)
        if channel.guild_id is not None
        else 0
    )
    if not channel_accessible(backend, channel_id, backend.bot_user.id, permissions_value=perms):
        raise errors.missing_access()
    if channel.guild_id is not None and not perms & permissions.flag("read_message_history"):
        return []
    limit = int(ctx.params.get("limit", 50))
    # Channel scrollback never includes ephemeral interaction responses.
    if "around" in ctx.params:
        around = int(ctx.params["around"])
        lower = backend.select_messages(channel_id, before=around + 1, newest_first=True, limit=1)
        upper = backend.select_messages(channel_id, after=around - 1, limit=1)
        candidates = lower + upper
        if not candidates:
            return []
        pivot = min(candidates, key=lambda message: (abs(message.id - around), message.id))
        half = limit // 2
        older = backend.select_messages(channel_id, before=pivot.id, newest_first=True, limit=half)
        newer = backend.select_messages(channel_id, after=pivot.id, limit=half)
        window = [*reversed(older), pivot, *newer]
        return [message_response(ctx, message) for message in reversed(window[:limit])]
    messages = backend.select_messages(
        channel_id,
        newest_first=True,
        limit=limit,
        before=int(ctx.params["before"]) if "before" in ctx.params else None,
        after=int(ctx.params["after"]) if "after" in ctx.params else None,
    )
    return [message_response(ctx, message) for message in messages]


@route("PATCH", "/channels/{channel_id}/messages/{message_id}")
def edit_message(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    message = ctx.require_message(channel_id, ctx.int_arg("message_id"))
    # Real Discord only lets you edit your own messages (50005); the bot editing
    # someone else's message is exactly the kind of bug this framework surfaces.
    if message.author_id != backend.bot_user.id:
        raise errors.cannot_edit_other_user()
    message = edit_bot_message(ctx, message)
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
    message = ctx.require_message(channel_id, ctx.int_arg("message_id"))
    if message.author_id != backend.bot_user.id:
        ctx.require_channel_permissions(channel_id, "manage_messages")
    return message_response(ctx, backend.crosspost_message(channel_id, message.id))


@route("DELETE", "/channels/{channel_id}/messages/{message_id}")
def delete_message(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    message = ctx.require_message(channel_id, ctx.int_arg("message_id"))
    channel = backend.get_channel(channel_id)
    if message.author_id != backend.bot_user.id and channel.guild_id is not None:
        ctx.require_channel_permissions(channel_id, "manage_messages")
    backend.delete_message(channel_id, message.id)


# Pins use Discord's current paginated endpoints (under /messages/pins).


@route("PUT", "/channels/{channel_id}/messages/pins/{message_id}")
def pin_message(ctx: RequestContext) -> Any:
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_messages")
    ctx.require_message(channel.id, ctx.int_arg("message_id"))
    ctx.backend.set_pinned(channel.id, ctx.int_arg("message_id"), True)


@route("DELETE", "/channels/{channel_id}/messages/pins/{message_id}")
def unpin_message(ctx: RequestContext) -> Any:
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_messages")
    ctx.require_message(channel.id, ctx.int_arg("message_id"))
    ctx.backend.set_pinned(channel.id, ctx.int_arg("message_id"), False)


@route("POST", "/channels/{channel_id}/polls/{message_id}/expire")
def expire_poll(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    message = ctx.require_message(channel_id, ctx.int_arg("message_id"))
    if message.author_id != backend.bot_user.id:
        raise errors.cannot_edit_other_user()
    message = backend.expire_poll(channel_id, message.id)
    return message_response(ctx, message)


@route("GET", "/channels/{channel_id}/polls/{message_id}/answers/{answer_id}")
def get_poll_answer_voters(ctx: RequestContext) -> Any:
    backend = ctx.backend
    message = ctx.require_message(
        ctx.int_arg("channel_id"), ctx.int_arg("message_id"), "read_message_history"
    )
    answer_id = ctx.int_arg("answer_id")
    voters = message.poll.votes.get(answer_id, set()) if message.poll else set()
    limit = int(ctx.params.get("limit", 25))
    users = [serializers.user_payload(backend.users[uid]) for uid in voters if uid in backend.users]
    return {"users": users[:limit]}


@route("GET", "/channels/{channel_id}/messages/pins")
def get_pins(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    ctx.require_channel_permissions(channel_id, "read_message_history")
    items = [
        {"message": message_response(ctx, m), "pinned_at": backend.now_iso()}
        for m in sorted(
            backend.iter_messages(channel_id, pinned=True), key=lambda m: backend._message_order[m.id]
        )
    ]
    return {"items": items, "has_more": False}
