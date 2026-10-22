from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discord.types.sku import Entitlement as EntitlementPayload


@dataclass
class SKU:
    id: int
    application_id: int
    type: int
    name: str
    slug: str
    flags: int = 0


@dataclass
class Entitlement:
    id: int
    application_id: int
    sku_id: int
    type: int
    user_id: int | None = None
    guild_id: int | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    deleted: bool = False
    consumed: bool | None = None
    created_via_test_api: bool = field(default=False, repr=False)
    original_create_payload: EntitlementPayload | None = field(default=None, repr=False)
    scheduled_end_at: datetime | None = field(default=None, repr=False)
    fulfilled_at: datetime | None = None
    fulfillment_status: int = 3
    source_type: int | None = None
