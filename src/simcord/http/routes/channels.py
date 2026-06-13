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
    editable = ("name", "topic", "nsfw", "rate_limit_per_user", "available_tags")
    changes = {key: body[key] for key in editable if key in body}
    if "available_tags" in changes:
        # Discord assigns an id to each newly created forum tag.
        changes["available_tags"] = [
            {**tag, "id": tag.get("id") or str(backend.snowflake())} for tag in changes["available_tags"]
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
    channel = backend.edit_channel(channel.id, changes, overwrites=overwrites)
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
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "create_public_threads")
    body = ctx.body()
    if channel.type == ChannelType.FORUM:
        return _create_forum_post(ctx, channel.id, body)
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
    thread = backend.create_thread(
        forum_id,
        body["name"],
        backend.bot_user.id,
        type=ChannelType.PUBLIC_THREAD,
        auto_archive_duration=int(body.get("auto_archive_duration") or 1440),
        applied_tags=[int(t) for t in body.get("applied_tags") or []],
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
    body = ctx.body()
    thread = backend.create_thread(
        channel.id,
        body["name"],
        backend.bot_user.id,
        message_id=message.id,
        auto_archive_duration=int(body.get("auto_archive_duration", 1440)),
    )
    return dict(serializers.thread_payload(backend, thread))


@route("POST", "/channels/{channel_id}/webhooks")
def create_webhook(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_webhooks")
    webhook = backend.create_webhook(channel.id, ctx.body().get("name") or "Webhook", backend.bot_user.id)
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
    return serializers.webhook_payload(backend, backend.edit_webhook(webhook.id, ctx.body()))


@route("PATCH", "/webhooks/{webhook_id}/{token}")
def edit_webhook_with_token(ctx: RequestContext) -> Any:
    backend = ctx.backend
    webhook = backend.get_webhook(ctx.int_arg("webhook_id"))
    _require_token(webhook, ctx.args["token"])
    # A token cannot move the webhook between channels.
    changes = {key: value for key, value in ctx.body().items() if key != "channel_id"}
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
