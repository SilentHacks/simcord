from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Webhook:
    id: int
    token: str
    channel_id: int
    guild_id: int | None
    name: str
    creator_id: int
    webhook_user_id: int  # the synthetic user webhook messages are authored as
