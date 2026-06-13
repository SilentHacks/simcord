from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Invite:
    """A guild invite to a channel."""

    code: str
    guild_id: int
    channel_id: int
    inviter_id: int
    created_at: str
    uses: int = 0
    max_uses: int = 0
    max_age: int = 0  # seconds; 0 = never
    temporary: bool = False
    expires_at: str | None = None
