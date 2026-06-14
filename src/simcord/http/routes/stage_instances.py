"""Stage instance routes: open/fetch/edit/close a live stage."""

from __future__ import annotations

from typing import Any

from ...backend import serializers
from ..router import RequestContext, route


@route("POST", "/stage-instances")
def create_stage_instance(ctx: RequestContext) -> Any:
    backend = ctx.backend
    body = ctx.body()
    channel_id = int(body["channel_id"])
    ctx.require_channel_permissions(channel_id, "manage_channels")
    instance = backend.create_stage_instance(
        channel_id, body.get("topic") or "", privacy_level=int(body.get("privacy_level", 2))
    )
    return serializers.stage_instance_payload(instance)


@route("GET", "/stage-instances/{channel_id}")
def get_stage_instance(ctx: RequestContext) -> Any:
    return serializers.stage_instance_payload(ctx.backend.get_stage_instance(ctx.int_arg("channel_id")))


@route("PATCH", "/stage-instances/{channel_id}")
def edit_stage_instance(ctx: RequestContext) -> Any:
    backend = ctx.backend
    channel_id = ctx.int_arg("channel_id")
    ctx.require_channel_permissions(channel_id, "manage_channels")
    body = ctx.fields("topic", "privacy_level")
    changes: dict[str, Any] = {}
    if "topic" in body:
        changes["topic"] = body["topic"]
    if "privacy_level" in body:
        changes["privacy_level"] = int(body["privacy_level"])
    return serializers.stage_instance_payload(backend.edit_stage_instance(channel_id, changes))


@route("DELETE", "/stage-instances/{channel_id}")
def delete_stage_instance(ctx: RequestContext) -> Any:
    channel_id = ctx.int_arg("channel_id")
    ctx.require_channel_permissions(channel_id, "manage_channels")
    ctx.backend.delete_stage_instance(channel_id)
