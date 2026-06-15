"""Channel routes: fetch/edit/delete, typing, overwrites, threads, webhooks."""

from __future__ import annotations

from typing import Any

from ...backend import errors, serializers
from ...backend.models import Overwrite, Webhook
from ...enums import AuditLogAction, ChannelType, OverwriteType
from .._helpers import bot_message
from ..router import RequestContext, route


@route("GET", "/channels/{channel_id}")
def get_channel(ctx: RequestContext) -> Any:
    return dict(serializers.channel_payload(ctx.backend, ctx.backend.get_channel(ctx.int_arg("channel_id"))))


@route("PATCH", "/channels/{channel_id}")
def edit_channel(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_channels")
    body = ctx.body()
    # Scalar fields mapped 1:1 onto the Channel model. Voice fields only reach
    # this handler for voice/stage channels (discord.py omits them otherwise).
    # parent_id arrives only when position is absent: discord.py routes any edit
    # carrying a position through the bulk-move endpoint instead (an honest
    # route-level gap), so position never reaches this per-channel handler.
    editable = (
        "name",
        "topic",
        "nsfw",
        "rate_limit_per_user",
        "available_tags",
        "bitrate",
        "user_limit",
        "rtc_region",
        "parent_id",
    )
    # Keys this handler honours but applies itself (not via the scalar `changes`).
    ignore = ("permission_overwrites",)
    if channel.is_thread:
        ignore += ("archived", "locked", "auto_archive_duration", "invitable", "applied_tags")
    changes = ctx.fields(*editable, ignore=ignore)
    if changes.get("parent_id") is not None:
        changes["parent_id"] = int(changes["parent_id"])
    thread_metadata: dict[str, Any] | None = None
    if channel.is_thread:
        thread_metadata = {
            key: body[key]
            for key in ("archived", "locked", "auto_archive_duration", "invitable")
            if key in body
        }
        if "applied_tags" in body:
            changes["applied_tags"] = [int(t) for t in body["applied_tags"]]
    if "available_tags" in changes:
        # Discord assigns an id to each newly created forum tag. discord.py's
        # ForumTag.to_dict omits ``id`` for unsaved tags, so a missing/falsy id
        # means "new" — mint a snowflake. Ids are normalised to str like every
        # other snowflake in the payloads.
        changes["available_tags"] = [
            {**tag, "id": str(tag.get("id") or backend.snowflake())} for tag in changes["available_tags"]
        ]
    overwrites = None
    if "permission_overwrites" in body:
        overwrites = [
            Overwrite(
                target_id=int(o["id"]),
                type=OverwriteType(int(o["type"])),
                allow=int(o.get("allow", 0)),
                deny=int(o.get("deny", 0)),
            )
            for o in body["permission_overwrites"]
        ]
    old = {key: getattr(channel, key) for key in changes}
    channel = backend.edit_channel(
        channel.id, changes, overwrites=overwrites, thread_metadata=thread_metadata
    )
    if channel.guild_id is not None:
        audit_changes = [
            {"key": key, "old_value": old[key], "new_value": getattr(channel, key)}
            for key in changes
            if old[key] != getattr(channel, key)
        ]
        if audit_changes:
            backend.record_audit_log(
                channel.guild_id,
                AuditLogAction.CHANNEL_UPDATE,
                target_id=channel.id,
                changes=audit_changes,
                reason=ctx.reason,
            )
    return dict(serializers.channel_payload(backend, channel))


@route("DELETE", "/channels/{channel_id}")
def delete_channel(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_channels")
    payload = dict(serializers.channel_payload(backend, channel))
    guild_id, channel_id, name = channel.guild_id, channel.id, channel.name
    backend.delete_channel(channel.id)
    if guild_id is not None:
        backend.record_audit_log(
            guild_id,
            AuditLogAction.CHANNEL_DELETE,
            target_id=channel_id,
            changes=[{"key": "name", "old_value": name}],
            reason=ctx.reason,
        )
    return payload


@route("PUT", "/channels/{channel_id}/permissions/{target_id}")
def edit_overwrite(ctx: RequestContext) -> Any:
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_roles")
    body = ctx.body()
    ctx.backend.set_overwrite(
        channel.id,
        Overwrite(
            target_id=ctx.int_arg("target_id"),
            type=OverwriteType(int(body["type"])),
            allow=int(body.get("allow", 0)),
            deny=int(body.get("deny", 0)),
        ),
    )


@route("DELETE", "/channels/{channel_id}/permissions/{target_id}")
def delete_overwrite(ctx: RequestContext) -> Any:
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_roles")
    ctx.backend.delete_overwrite(channel.id, ctx.int_arg("target_id"))


@route("POST", "/channels/{channel_id}/typing")
def typing(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel = backend.get_channel(ctx.int_arg("channel_id"))
    payload: dict[str, Any] = {
        "channel_id": str(channel.id),
        "user_id": str(backend.bot_user.id),
        "timestamp": 0,
    }
    if channel.guild_id is not None:
        payload["guild_id"] = str(channel.guild_id)
    backend.emit("TYPING_START", payload)


@route("POST", "/channels/{channel_id}/threads")
def create_thread(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel = backend.get_channel(ctx.int_arg("channel_id"))
    if channel.type == ChannelType.FORUM:
        # A forum post is a message posted into the forum, so it is gated on
        # send_messages there, not create_public_threads. Thread slowmode
        # (rate_limit_per_user) is accepted and discarded — unmodelled offline.
        ctx.require_channel_permissions(channel.id, "send_messages")
        # A forum post is always a public thread, so ``type`` is accepted and
        # discarded; thread slowmode (rate_limit_per_user) is unmodelled.
        body = ctx.fields(
            "name",
            "message",
            "applied_tags",
            "auto_archive_duration",
            ignore=("type", "rate_limit_per_user"),
        )
        return _create_forum_post(ctx, channel.id, body)
    ctx.require_channel_permissions(channel.id, "create_public_threads")
    # invitable (private-thread joinability) and rate_limit_per_user are accepted
    # and discarded: neither is modelled for offline threads.
    body = ctx.fields("name", "type", "auto_archive_duration", ignore=("invitable", "rate_limit_per_user"))
    thread = backend.create_thread(
        channel.id,
        body["name"],
        backend.bot_user.id,
        type=int(body.get("type", 11)),
        auto_archive_duration=int(body.get("auto_archive_duration", 1440)),
    )
    return dict(serializers.thread_payload(backend, thread))


def _create_forum_post(ctx: RequestContext, forum_id: int, body: dict[str, Any]) -> dict[str, Any]:
    """A forum post is a public thread plus its mandatory starter message.

    discord.py's ``ForumChannel.create_thread`` posts here with the message
    nested under ``message`` and the chosen tags under ``applied_tags``; the
    response carries the thread fields with the starter ``message`` embedded.
    """
    backend = ctx.backend
    forum = backend.get_channel(forum_id)
    applied_tags = [int(t) for t in body.get("applied_tags") or []]
    available = {int(tag["id"]) for tag in forum.available_tags if tag.get("id") is not None}
    unknown = [t for t in applied_tags if t not in available]
    if unknown:
        raise errors.invalid_form_body(f"applied_tags: Unknown tag(s) {unknown}")
    thread = backend.create_thread(
        forum_id,
        body["name"],
        backend.bot_user.id,
        type=ChannelType.PUBLIC_THREAD,
        auto_archive_duration=int(body.get("auto_archive_duration") or 1440),
        applied_tags=applied_tags,
    )
    message = bot_message(ctx, thread.id, body=body.get("message") or {})
    payload = dict(serializers.thread_payload(backend, thread))
    payload["message"] = serializers.message_payload(backend, message)
    return payload


@route("POST", "/channels/{channel_id}/messages/{message_id}/threads")
def create_thread_from_message(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "create_public_threads")
    message = backend.get_message(channel.id, ctx.int_arg("message_id"))
    # rate_limit_per_user (thread slowmode) is accepted and discarded.
    body = ctx.fields("name", "auto_archive_duration", ignore=("rate_limit_per_user",))
    thread = backend.create_thread(
        channel.id,
        body["name"],
        backend.bot_user.id,
        message_id=message.id,
        auto_archive_duration=int(body.get("auto_archive_duration", 1440)),
    )
    return dict(serializers.thread_payload(backend, thread))


@route("PUT", "/channels/{channel_id}/thread-members/@me")
def join_thread(ctx: RequestContext) -> Any:
    backend = ctx.backend
    backend.add_thread_member(ctx.int_arg("channel_id"), backend.bot_user.id)


@route("DELETE", "/channels/{channel_id}/thread-members/@me")
def leave_thread(ctx: RequestContext) -> Any:
    backend = ctx.backend
    backend.remove_thread_member(ctx.int_arg("channel_id"), backend.bot_user.id)


@route("PUT", "/channels/{channel_id}/thread-members/{user_id}")
def add_thread_member(ctx: RequestContext) -> Any:
    ctx.backend.add_thread_member(ctx.int_arg("channel_id"), ctx.int_arg("user_id"))


@route("DELETE", "/channels/{channel_id}/thread-members/{user_id}")
def remove_thread_member(ctx: RequestContext) -> Any:
    ctx.backend.remove_thread_member(ctx.int_arg("channel_id"), ctx.int_arg("user_id"))


@route("GET", "/channels/{channel_id}/thread-members")
def list_thread_members(ctx: RequestContext) -> Any:
    thread = ctx.backend.get_thread(ctx.int_arg("channel_id"))
    return [serializers.thread_member_payload(thread, uid) for uid in thread.thread_members]


@route("GET", "/channels/{channel_id}/thread-members/{user_id}")
def get_thread_member(ctx: RequestContext) -> Any:
    thread = ctx.backend.get_thread(ctx.int_arg("channel_id"))
    user_id = ctx.int_arg("user_id")
    if user_id not in thread.thread_members:
        raise errors.unknown_member()
    return serializers.thread_member_payload(thread, user_id)


def _archived_threads(ctx: RequestContext, *, private: bool, joined_only: bool = False) -> dict[str, Any]:
    backend = ctx.backend
    threads = backend.archived_threads(ctx.int_arg("channel_id"), private=private)
    if joined_only:
        threads = [t for t in threads if backend.bot_user.id in t.thread_members]
    return serializers.thread_list_payload(backend, threads, has_more=False)


@route("GET", "/channels/{channel_id}/threads/archived/public")
def archived_public_threads(ctx: RequestContext) -> Any:
    return _archived_threads(ctx, private=False)


@route("GET", "/channels/{channel_id}/threads/archived/private")
def archived_private_threads(ctx: RequestContext) -> Any:
    return _archived_threads(ctx, private=True)


@route("GET", "/channels/{channel_id}/users/@me/threads/archived/private")
def joined_archived_private_threads(ctx: RequestContext) -> Any:
    # The `@me` variant is scoped to the private archived threads the bot joined.
    return _archived_threads(ctx, private=True, joined_only=True)


@route("POST", "/channels/{channel_id}/webhooks")
def create_webhook(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_webhooks")
    # avatar (CDN image) is accepted and discarded.
    body = ctx.fields("name", ignore=("avatar",))
    webhook = backend.create_webhook(channel.id, body.get("name") or "Webhook", backend.bot_user.id)
    return serializers.webhook_payload(backend, webhook)


@route("GET", "/channels/{channel_id}/webhooks")
def get_channel_webhooks(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    backend.get_channel(channel_id)
    return [
        serializers.webhook_payload(backend, w)
        for w in backend.webhooks.values()
        if w.channel_id == channel_id
    ]


@route("GET", "/guilds/{guild_id}/webhooks")
def get_guild_webhooks(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    backend.get_guild(guild_id)
    ctx.require_guild_permissions(guild_id, "manage_webhooks")
    return [
        serializers.webhook_payload(backend, w) for w in backend.webhooks.values() if w.guild_id == guild_id
    ]


def _require_token(webhook: Webhook, token: str) -> None:
    """Token-scoped webhook ops authenticate by token, not bot permissions."""
    if webhook.token != token:
        raise errors.unknown_webhook()


@route("GET", "/webhooks/{webhook_id}")
def get_webhook(ctx: RequestContext) -> Any:
    backend = ctx.backend
    webhook = backend.get_webhook(ctx.int_arg("webhook_id"))
    ctx.require_channel_permissions(webhook.channel_id, "manage_webhooks")
    return serializers.webhook_payload(backend, webhook)


@route("GET", "/webhooks/{webhook_id}/{token}")
def get_webhook_with_token(ctx: RequestContext) -> Any:
    backend = ctx.backend
    webhook = backend.get_webhook(ctx.int_arg("webhook_id"))
    _require_token(webhook, ctx.args["token"])
    return serializers.webhook_payload(backend, webhook, include_token=False)


@route("PATCH", "/webhooks/{webhook_id}")
def edit_webhook(ctx: RequestContext) -> Any:
    backend = ctx.backend
    webhook = backend.get_webhook(ctx.int_arg("webhook_id"))
    ctx.require_channel_permissions(webhook.channel_id, "manage_webhooks")
    changes = ctx.fields("name", "channel_id")
    return serializers.webhook_payload(backend, backend.edit_webhook(webhook.id, changes))


@route("PATCH", "/webhooks/{webhook_id}/{token}")
def edit_webhook_with_token(ctx: RequestContext) -> Any:
    backend = ctx.backend
    webhook = backend.get_webhook(ctx.int_arg("webhook_id"))
    _require_token(webhook, ctx.args["token"])
    # A token cannot move the webhook between channels, so channel_id is accepted
    # and ignored rather than honoured.
    changes = ctx.fields("name", ignore=("channel_id",))
    return serializers.webhook_payload(
        backend, backend.edit_webhook(webhook.id, changes), include_token=False
    )


@route("DELETE", "/webhooks/{webhook_id}")
def delete_webhook(ctx: RequestContext) -> Any:
    backend = ctx.backend
    webhook = backend.get_webhook(ctx.int_arg("webhook_id"))
    ctx.require_channel_permissions(webhook.channel_id, "manage_webhooks")
    backend.delete_webhook(webhook.id)


@route("DELETE", "/webhooks/{webhook_id}/{token}")
def delete_webhook_with_token(ctx: RequestContext) -> Any:
    backend = ctx.backend
    webhook = backend.get_webhook(ctx.int_arg("webhook_id"))
    _require_token(webhook, ctx.args["token"])
    backend.delete_webhook(webhook.id)
