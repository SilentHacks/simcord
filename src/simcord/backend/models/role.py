from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Role:
    id: int
    name: str
    permissions: int = 0
    position: int = 0
    color: int = 0
    hoist: bool = False
    managed: bool = False
    mentionable: bool = False
