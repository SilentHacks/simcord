"""Reaction routes."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

from ...backend import serializers
from ..router import RequestContext, route


def _emoji(ctx: RequestContext) -> str:
    return unquote(ctx.args["emoji"])


@route("PUT", "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me")
def add_own_reaction(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "add_reactions")
    backend.add_reaction(channel.id, ctx.int_arg("message_id"), _emoji(ctx), backend.bot_user.id)


@route("DELETE", "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me")
def remove_own_reaction(ctx: RequestContext) -> Any:
    backend = ctx.backend
    backend.remove_reaction(
        ctx.int_arg("channel_id"), ctx.int_arg("message_id"), _emoji(ctx), backend.bot_user.id
    )


@route("DELETE", "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/{user_id}")
def remove_user_reaction(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_messages")
    backend.remove_reaction(channel.id, ctx.int_arg("message_id"), _emoji(ctx), ctx.int_arg("user_id"))


@route("GET", "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}")
def get_reaction_users(ctx: RequestContext) -> Any:
    backend = ctx.backend
    message = backend.get_message(ctx.int_arg("channel_id"), ctx.int_arg("message_id"))
    reaction = message.reaction_for(_emoji(ctx))
    if reaction is None:
        return []
    return [dict(serializers.user_payload(backend.get_user(uid))) for uid in reaction.user_ids]
