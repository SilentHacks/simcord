from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...enums import ChannelType, OverwriteType

THREAD_TYPES = (ChannelType.PUBLIC_THREAD, ChannelType.PRIVATE_THREAD)


@dataclass
class Overwrite:
    target_id: int
    type: OverwriteType  # ROLE or MEMBER
    allow: int = 0
    deny: int = 0


@dataclass
class ThreadMetadata:
    archived: bool = False
    auto_archive_duration: int = 1440
    archive_timestamp: str = ""
    locked: bool = False
    create_timestamp: str = ""


@dataclass
class Channel:
    id: int
    type: int
    name: str | None = None
    guild_id: int | None = None
    position: int = 0
    overwrites: list[Overwrite] = field(default_factory=list)
    topic: str | None = None
    parent_id: int | None = None
    nsfw: bool = False
    rate_limit_per_user: int = 0
    last_message_id: int | None = None
    recipient_ids: list[int] = field(default_factory=list)  # DM channels
    # Voice/stage-only fields
    bitrate: int = 64000
    user_limit: int = 0
    rtc_region: str | None = None
    # Forum-only fields
    available_tags: list[dict[str, Any]] = field(default_factory=list)
    default_reaction_emoji: dict[str, Any] | None = None
    # Thread-only fields
    owner_id: int | None = None
    thread_metadata: ThreadMetadata | None = None
    message_count: int = 0
    applied_tags: list[int] = field(default_factory=list)  # forum-post threads

    @property
    def is_thread(self) -> bool:
        return self.type in THREAD_TYPES

    def permission_channel_id(self) -> int:
        """Threads inherit permissions from their parent channel."""
        return self.parent_id if self.is_thread and self.parent_id else self.id
