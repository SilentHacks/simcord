from __future__ import annotations

from dataclasses import dataclass


@dataclass
class User:
    id: int
    name: str
    bot: bool = False
