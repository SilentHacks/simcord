"""Guild scheduled-event routes: CRUD plus the subscriber list."""

from __future__ import annotations

from typing import Any

from ...backend import errors, serializers
from ...enums import AuditLogAction
from ..router import RequestContext, route

_VOICE_ENTITY_TYPES = (1, 2)  # STAGE_INSTANCE, VOICE


def _validate_entity(body: dict[str, Any]) -> None:
    entity_type = int(body["entity_type"])
    if entity_type in _VOICE_ENTITY_TYPES and not body.get("channel_id"):
        raise errors.invalid_form_body("channel_id is required for stage/voice events")
    if entity_type == 3:  # EXTERNAL
        if not (body.get("entity_metadata") or {}).get("location"):
            raise errors.invalid_form_body("entity_metadata.location is required for external events")
        if not body.get("scheduled_end_time"):
            raise errors.invalid_form_body("scheduled_end_time is required for external events")


@route("GET", "/guilds/{guild_id}/scheduled-events")
def list_scheduled_events(ctx: RequestContext) -> Any:
    guild = ctx.backend.get_guild(ctx.int_arg("guild_id"))
    return [serializers.scheduled_event_payload(ctx.backend, e) for e in guild.scheduled_events.values()]


@route("POST", "/guilds/{guild_id}/scheduled-events")
def create_scheduled_event(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    ctx.require_guild_permissions(guild_id, "manage_events")
    body = ctx.body()
    _validate_entity(body)
    event = backend.create_scheduled_event(
        guild_id,
        name=body["name"],
        entity_type=int(body["entity_type"]),
        scheduled_start_time=body["scheduled_start_time"],
        creator_id=backend.bot_user.id,
        channel_id=int(body["channel_id"]) if body.get("channel_id") else None,
        description=body.get("description"),
        scheduled_end_time=body.get("scheduled_end_time"),
        entity_metadata=body.get("entity_metadata"),
    )
    backend.record_audit_log(
        guild_id, AuditLogAction.SCHEDULED_EVENT_CREATE, target_id=event.id, reason=ctx.reason
    )
    return serializers.scheduled_event_payload(backend, event)


@route("GET", "/guilds/{guild_id}/scheduled-events/{event_id}")
def get_scheduled_event(ctx: RequestContext) -> Any:
    backend = ctx.backend
    event = backend.get_scheduled_event(ctx.int_arg("guild_id"), ctx.int_arg("event_id"))
    return serializers.scheduled_event_payload(backend, event)


@route("PATCH", "/guilds/{guild_id}/scheduled-events/{event_id}")
def edit_scheduled_event(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    event_id = ctx.int_arg("event_id")
    ctx.require_guild_permissions(guild_id, "manage_events")
    backend.get_scheduled_event(guild_id, event_id)
    body = ctx.body()
    field_map = {
        "name": "name",
        "description": "description",
        "scheduled_start_time": "scheduled_start_time",
        "scheduled_end_time": "scheduled_end_time",
        "status": "status",
        "entity_type": "entity_type",
        "privacy_level": "privacy_level",
    }
    changes: dict[str, Any] = {attr: body[key] for key, attr in field_map.items() if key in body}
    if "channel_id" in body:
        changes["channel_id"] = int(body["channel_id"]) if body["channel_id"] else None
    if "entity_metadata" in body:
        changes["entity_metadata"] = body["entity_metadata"]
    event = backend.edit_scheduled_event(guild_id, event_id, changes)
    backend.record_audit_log(
        guild_id, AuditLogAction.SCHEDULED_EVENT_UPDATE, target_id=event_id, reason=ctx.reason
    )
    return serializers.scheduled_event_payload(backend, event)


@route("DELETE", "/guilds/{guild_id}/scheduled-events/{event_id}")
def delete_scheduled_event(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    event_id = ctx.int_arg("event_id")
    ctx.require_guild_permissions(guild_id, "manage_events")
    backend.delete_scheduled_event(guild_id, event_id)
    backend.record_audit_log(
        guild_id, AuditLogAction.SCHEDULED_EVENT_DELETE, target_id=event_id, reason=ctx.reason
    )


@route("GET", "/guilds/{guild_id}/scheduled-events/{event_id}/users")
def get_scheduled_event_users(ctx: RequestContext) -> Any:
    backend = ctx.backend
    guild_id = ctx.int_arg("guild_id")
    event = backend.get_scheduled_event(guild_id, ctx.int_arg("event_id"))
    guild = backend.get_guild(guild_id)
    out = []
    for user_id in event.user_ids:
        if user_id not in backend.users:
            continue
        entry: dict[str, Any] = {
            "guild_scheduled_event_id": str(event.id),
            "user": serializers.user_payload(backend.users[user_id]),
        }
        if user_id in guild.members:
            entry["member"] = serializers.member_payload(backend, guild, guild.members[user_id])
        out.append(entry)
    return out
