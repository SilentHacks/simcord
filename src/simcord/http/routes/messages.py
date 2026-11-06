"""Message routes: send, fetch, history, edit, delete, pins."""

from __future__ import annotations

from typing import Any

from ...backend import errors, permissions, serializers
from ...backend.access import channel_accessible
from ...enums import AuditLogAction, ChannelType
from .._access import require_text_access
from .._helpers import commit_bot_message, edit_bot_message, message_response, prepare_bot_message
from ..router import RequestContext, route


def _read_response(ctx: RequestContext, message: Any) -> Any:
    payload = message_response(ctx, message, for_user=ctx.backend.bot_user.id)
    if (
        ctx.backend.users[message.author_id].bot
        and message.interaction_metadata is None
        and message.webhook_id is None
    ):
        payload["tts"] = False
    return payload


def _send_permissions(ctx: RequestContext, channel_id: int) -> None:
    channel = require_text_access(ctx, channel_id)
    if channel.type == ChannelType.DM:
        # You cannot DM another bot; real Discord rejects this on send (50007).
        if any(ctx.backend.users[uid].bot for uid in channel.recipient_ids):
            raise errors.cannot_dm_bot()
        return
    perm = "send_messages_in_threads" if channel.is_thread else "send_messages"
    ctx.require_channel_permissions(channel_id, perm, denied_code=50001 if channel.is_thread else 50013)
    ctx.backend.require_thread_mutable(channel_id, ctx.backend.bot_user.id, sending=True)
    if channel.type in (ChannelType.CATEGORY, ChannelType.FORUM):
        raise errors.BackendError(400, 50008, "Cannot send messages in a non-text channel")


@route("POST", "/channels/{channel_id}/messages")
def send_message(ctx: RequestContext) -> Any:
    channel_id = ctx.int_arg("channel_id")
    _send_permissions(ctx, channel_id)
    prepared = prepare_bot_message(ctx, channel_id, ordinary_send=True)
    ctx.backend.unarchive_thread_for_send(channel_id, ctx.backend.bot_user.id)
    return message_response(ctx, commit_bot_message(ctx, channel_id, prepared))


@route("GET", "/channels/{channel_id}/messages/{message_id}")
def get_message(ctx: RequestContext) -> Any:
    require_text_access(ctx, ctx.int_arg("channel_id"))
    message = ctx.require_message(
        ctx.int_arg("channel_id"), ctx.int_arg("message_id"), "read_message_history", denied_code=50001
    )
    return _read_response(ctx, message)


@route("GET", "/channels/{channel_id}/messages")
def get_history(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    channel = require_text_access(ctx, channel_id)
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
        return [_read_response(ctx, message) for message in reversed(window[:limit])]
    messages = backend.select_messages(
        channel_id,
        newest_first=True,
        limit=limit,
        before=int(ctx.params["before"]) if "before" in ctx.params else None,
        after=int(ctx.params["after"]) if "after" in ctx.params else None,
    )
    return [_read_response(ctx, message) for message in messages]


@route("PATCH", "/channels/{channel_id}/messages/{message_id}")
def edit_message(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    require_text_access(ctx, channel_id)
    backend.require_thread_mutable(channel_id, backend.bot_user.id)
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
    require_text_access(ctx, ctx.int_arg("channel_id"))
    backend.require_thread_mutable(ctx.int_arg("channel_id"), backend.bot_user.id)
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
    require_text_access(ctx, channel_id)
    channel = ctx.require_channel_permissions(channel_id, "send_messages", denied_code=50001)
    ctx.require_channel_permissions(channel_id, "read_message_history", denied_code=50001)
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
    require_text_access(ctx, channel_id)
    backend.require_thread_mutable(channel_id, backend.bot_user.id)
    message = ctx.require_message(channel_id, ctx.int_arg("message_id"))
    channel = backend.get_channel(channel_id)
    if message.author_id != backend.bot_user.id and channel.guild_id is not None:
        ctx.require_channel_permissions(channel_id, "manage_messages")
    backend.delete_message(channel_id, message.id)


# Pins use Discord's current paginated endpoints (under /messages/pins).


@route("PUT", "/channels/{channel_id}/messages/pins/{message_id}")
def pin_message(ctx: RequestContext) -> Any:
    channel = _pin_access(ctx)
    ctx.require_message(channel.id, ctx.int_arg("message_id"))
    ctx.backend.set_pinned(channel.id, ctx.int_arg("message_id"), True)


@route("DELETE", "/channels/{channel_id}/messages/pins/{message_id}")
def unpin_message(ctx: RequestContext) -> Any:
    channel = _pin_access(ctx)
    ctx.require_message(channel.id, ctx.int_arg("message_id"))
    ctx.backend.set_pinned(channel.id, ctx.int_arg("message_id"), False)


def _pin_access(ctx: RequestContext) -> Any:
    channel = require_text_access(ctx, ctx.int_arg("channel_id"))
    if channel.type in (ChannelType.VOICE, ChannelType.STAGE_VOICE):
        raise errors.BackendError(400, 50019, "A message can only be pinned to the channel it was sent in")
    ctx.backend.require_thread_mutable(channel.id, ctx.backend.bot_user.id)
    ctx.require_channel_permissions(channel.id, "read_message_history", denied_code=50001)
    ctx.require_channel_permissions(channel.id, "pin_messages")
    return channel


@route("POST", "/channels/{channel_id}/polls/{message_id}/expire")
def expire_poll(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    require_text_access(ctx, channel_id)
    backend.require_thread_mutable(channel_id, backend.bot_user.id)
    message = ctx.require_message(channel_id, ctx.int_arg("message_id"))
    if message.author_id != backend.bot_user.id:
        raise errors.cannot_edit_other_user()
    message = backend.expire_poll(channel_id, message.id)
    return message_response(ctx, message)


@route("GET", "/channels/{channel_id}/polls/{message_id}/answers/{answer_id}")
def get_poll_answer_voters(ctx: RequestContext) -> Any:
    backend = ctx.backend
    require_text_access(ctx, ctx.int_arg("channel_id"))
    message = ctx.require_message(
        ctx.int_arg("channel_id"), ctx.int_arg("message_id"), "read_message_history", denied_code=50001
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
    channel = require_text_access(ctx, channel_id)
    if channel.guild_id is not None and not backend.compute_permissions(
        channel.guild_id, backend.bot_user.id, channel_id
    ) & permissions.flag("read_message_history"):
        return {"items": [], "has_more": False}
    items = [
        {"message": _read_response(ctx, m), "pinned_at": backend.now_iso()}
        for m in sorted(
            backend.iter_messages(channel_id, pinned=True), key=lambda m: backend._message_order[m.id]
        )
    ]
    return {"items": items, "has_more": False}
