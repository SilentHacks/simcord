from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GuildEmoji:
    """A custom guild emoji."""

    id: int
    name: str
    user_id: int
    animated: bool = False
    managed: bool = False
    available: bool = True
    role_ids: list[int] = field(default_factory=list)


@dataclass
class Sticker:
    """A custom guild sticker."""

    id: int
    name: str
    guild_id: int
    user_id: int
    description: str | None = None
    tags: str = ""
    format_type: int = 1  # PNG
    available: bool = True
