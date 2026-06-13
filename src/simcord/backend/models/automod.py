from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AutoModRule:
    """A guild auto-moderation rule.

    SimCord evaluates keyword triggers (``trigger_type == 1``) against messages
    on send; other trigger types are stored and served faithfully but not
    evaluated.
    """

    id: int
    guild_id: int
    name: str
    creator_id: int
    event_type: int  # 1 MESSAGE_SEND
    trigger_type: int  # 1 KEYWORD, 3 SPAM, 4 KEYWORD_PRESET, 5 MENTION_SPAM
    trigger_metadata: dict[str, Any] = field(default_factory=dict)
    actions: list[dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    exempt_roles: list[int] = field(default_factory=list)
    exempt_channels: list[int] = field(default_factory=list)
