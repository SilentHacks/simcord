"""Reaction routes."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

from ...backend import serializers
from ...backend.access import reaction_permissions
from ..router import RequestContext, route


def _emoji(ctx: RequestContext) -> str:
    return unquote(ctx.args["emoji"])


@route("PUT", "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me")
def add_own_reaction(ctx: RequestContext) -> Any:
    backend = ctx.backend
    message = ctx.require_message(
        ctx.int_arg("channel_id"), ctx.int_arg("message_id"), "read_message_history"
    )
    emoji = _emoji(ctx)
    ctx.require_channel_permissions(message.channel_id, *reaction_permissions(message, emoji))
    backend.add_reaction(message.channel_id, message.id, emoji, backend.bot_user.id)


@route("DELETE", "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me")
def remove_own_reaction(ctx: RequestContext) -> Any:
    backend = ctx.backend
    ctx.require_message(ctx.int_arg("channel_id"), ctx.int_arg("message_id"), "read_message_history")
    backend.remove_reaction(
        ctx.int_arg("channel_id"), ctx.int_arg("message_id"), _emoji(ctx), backend.bot_user.id
    )


@route("DELETE", "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/{user_id}")
def remove_user_reaction(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    if ctx.int_arg("user_id") == backend.bot_user.id:
        ctx.require_message(channel_id, ctx.int_arg("message_id"), "read_message_history")
    else:
        ctx.require_message(channel_id, ctx.int_arg("message_id"), "manage_messages")
    backend.remove_reaction(channel_id, ctx.int_arg("message_id"), _emoji(ctx), ctx.int_arg("user_id"))


@route("DELETE", "/channels/{channel_id}/messages/{message_id}/reactions")
def clear_reactions(ctx: RequestContext) -> Any:
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_messages")
    ctx.require_message(channel.id, ctx.int_arg("message_id"))
    ctx.backend.clear_reactions(channel.id, ctx.int_arg("message_id"))


@route("DELETE", "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}")
def clear_single_reaction(ctx: RequestContext) -> Any:
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_messages")
    ctx.require_message(channel.id, ctx.int_arg("message_id"))
    ctx.backend.clear_reaction(channel.id, ctx.int_arg("message_id"), _emoji(ctx))


@route("GET", "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}")
def get_reaction_users(ctx: RequestContext) -> Any:
    backend = ctx.backend
    message = ctx.require_message(
        ctx.int_arg("channel_id"), ctx.int_arg("message_id"), "read_message_history"
    )
    reaction = message.reaction_for(_emoji(ctx))
    if reaction is None:
        return []
    return [dict(serializers.user_payload(backend.get_user(uid))) for uid in reaction.user_ids]
