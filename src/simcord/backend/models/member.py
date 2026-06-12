from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Member:
    user_id: int
    role_ids: list[int] = field(default_factory=list)
    nick: str | None = None
    joined_at: str = ""
    timed_out_until: str | None = None  # ISO timestamp while timed out
    deaf: bool = False
    mute: bool = False
    pending: bool = False
