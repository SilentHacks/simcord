from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScheduledEvent:
    """A guild scheduled event (stage/voice/external)."""

    id: int
    guild_id: int
    name: str
    creator_id: int
    scheduled_start_time: str
    entity_type: int  # 1 stage_instance, 2 voice, 3 external
    channel_id: int | None = None
    description: str | None = None
    scheduled_end_time: str | None = None
    privacy_level: int = 2  # GUILD_ONLY
    status: int = 1  # 1 scheduled, 2 active, 3 completed, 4 canceled
    entity_metadata: dict[str, str] | None = None  # {"location": ...} for external
    user_ids: set[int] = field(default_factory=set)
