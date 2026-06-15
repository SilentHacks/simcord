"""Invite routes: create/list on a channel, list on a guild, fetch/delete by code."""

from __future__ import annotations

from typing import Any

from ...backend import serializers
from ..router import RequestContext, route


@route("POST", "/channels/{channel_id}/invites")
def create_invite(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "create_instant_invite")
    # discord.py always sends ``unique``; we always mint a unique code, so it is
    # accepted and discarded. target_type/target_*/flags are unmodelled and so
    # fail loudly rather than being silently dropped.
    body = ctx.fields("max_uses", "max_age", "temporary", ignore=("unique",))
    invite = backend.create_invite(
        channel.id,
        backend.bot_user.id,
        max_uses=int(body.get("max_uses", 0)),
        max_age=int(body.get("max_age", 86400)),
        temporary=bool(body.get("temporary", False)),
    )
    return serializers.invite_payload(backend, invite)


@route("GET", "/channels/{channel_id}/invites")
def channel_invites(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel = ctx.require_channel_permissions(ctx.int_arg("channel_id"), "manage_channels")
    return [
        serializers.invite_payload(backend, inv)
        for inv in backend.invites.values()
        if inv.channel_id == channel.id
    ]


@route("GET", "/guilds/{guild_id}/invites")
def guild_invites(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, "manage_guild")
    return [
        serializers.invite_payload(backend, inv)
        for inv in backend.invites.values()
        if inv.guild_id == guild_id
    ]


@route("GET", "/invites/{code}")
def get_invite(ctx: RequestContext) -> Any:
    return serializers.invite_payload(ctx.backend, ctx.backend.get_invite(ctx.args["code"]))


@route("DELETE", "/invites/{code}")
def delete_invite(ctx: RequestContext) -> Any:
    backend = ctx.backend
    invite = backend.get_invite(ctx.args["code"])
    ctx.require_channel_permissions(invite.channel_id, "manage_channels")
    backend.delete_invite(invite.code)
    return serializers.invite_payload(backend, invite, with_inviter=False)
