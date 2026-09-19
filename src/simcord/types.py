"""Public observability value types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class HttpLogEntry:
    """A transport-level REST request made by the bot."""

    method: str
    path: str
    params: dict[str, Any]
    json: Any | None
    reason: str | None
