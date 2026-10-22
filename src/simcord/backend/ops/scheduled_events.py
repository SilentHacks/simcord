"""Guild scheduled events."""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any

from .. import errors, serializers
from ..models import ScheduledEvent
from .base import BackendBase


class ScheduledEventMixin(BackendBase):
    def create_scheduled_event(
        self,
        guild_id: int,
        *,
        name: str,
        entity_type: int,
        scheduled_start_time: str,
        creator_id: int | None = None,
        channel_id: int | None = None,
        description: str | None = None,
        scheduled_end_time: str | None = None,
        entity_metadata: dict[str, str] | None = None,
    ) -> ScheduledEvent:
        guild = self.get_guild(guild_id)
        event = ScheduledEvent(
            id=self.snowflake(),
            guild_id=guild_id,
            name=name,
            creator_id=creator_id if creator_id is not None else self.bot_user.id,
            scheduled_start_time=scheduled_start_time,
            entity_type=entity_type,
            channel_id=channel_id,
            description=description,
            scheduled_end_time=scheduled_end_time,
            entity_metadata=entity_metadata,
        )
        guild.scheduled_events[event.id] = event
        self.emit("GUILD_SCHEDULED_EVENT_CREATE", serializers.scheduled_event_payload(self, event))
        return event

    def get_scheduled_event(self, guild_id: int, event_id: int) -> ScheduledEvent:
        event = self.get_guild(guild_id).scheduled_events.get(event_id)
        if event is None:
            raise errors.unknown_scheduled_event()
        return event

    def edit_scheduled_event(
        self, guild_id: int, event_id: int, changes: Mapping[str, Any]
    ) -> ScheduledEvent:
        event = self.get_scheduled_event(guild_id, event_id)
        for attr, value in changes.items():
            setattr(event, attr, value)
        self.emit("GUILD_SCHEDULED_EVENT_UPDATE", serializers.scheduled_event_payload(self, event))
        return event

    def delete_scheduled_event(self, guild_id: int, event_id: int) -> None:
        guild = self.get_guild(guild_id)
        event = self.get_scheduled_event(guild_id, event_id)
        del guild.scheduled_events[event_id]
        self.emit("GUILD_SCHEDULED_EVENT_DELETE", serializers.scheduled_event_payload(self, event))

    def next_scheduled_event_deadline(self) -> datetime.datetime | None:
        moments = []
        for guild in self.guilds.values():
            for event in guild.scheduled_events.values():
                value = (
                    event.scheduled_start_time
                    if event.status == 1
                    else event.scheduled_end_time
                    if event.status == 2
                    else None
                )
                if value is not None:
                    moment = datetime.datetime.fromisoformat(value)
                    moments.append(
                        moment if moment.tzinfo is not None else moment.replace(tzinfo=datetime.UTC)
                    )
        return min(moments, default=None)

    def activate_due_scheduled_events(self) -> None:
        """Auto-transition scheduled events whose start/end times have passed.

        1 (scheduled) -> 2 (active) at the start time, then 2 -> 3 (completed)
        at the end time (when one is set) — Discord's automatic lifecycle,
        driven by the virtual clock so ``advance_time`` carries events forward
        like real time. Manual status edits via ``PATCH`` still work too.
        """
        now = datetime.datetime.fromisoformat(self.now_iso())
        for guild in list(self.guilds.values()):
            for event in list(guild.scheduled_events.values()):
                if event.status == 1 and self._event_time_passed(event.scheduled_start_time, now):
                    self.edit_scheduled_event(guild.id, event.id, {"status": 2})
                if (
                    event.status == 2
                    and event.scheduled_end_time is not None
                    and self._event_time_passed(event.scheduled_end_time, now)
                ):
                    self.edit_scheduled_event(guild.id, event.id, {"status": 3})

    @staticmethod
    def _event_time_passed(iso: str, now: datetime.datetime) -> bool:
        moment = datetime.datetime.fromisoformat(iso)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.UTC)
        return moment <= now

    def set_scheduled_event_subscription(
        self, guild_id: int, event_id: int, user_id: int, subscribed: bool
    ) -> None:
        event = self.get_scheduled_event(guild_id, event_id)
        if subscribed:
            event.user_ids.add(user_id)
        else:
            event.user_ids.discard(user_id)
        self.emit(
            "GUILD_SCHEDULED_EVENT_USER_ADD" if subscribed else "GUILD_SCHEDULED_EVENT_USER_REMOVE",
            {
                "guild_scheduled_event_id": str(event_id),
                "user_id": str(user_id),
                "guild_id": str(guild_id),
            },
        )
