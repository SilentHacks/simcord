from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AuditLogEntry:
    """A single recorded moderation/management action.

    Mirrors Discord's audit-log entry: ``changes`` is a list of
    ``{"key", "old_value", "new_value"}`` dicts and ``options`` carries the
    action's extra context (e.g. ``$add``/``$remove`` for role updates).
    """

    id: int
    action_type: int
    user_id: int  # the executor
    target_id: int | None = None
    reason: str | None = None
    changes: list[dict[str, Any]] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
