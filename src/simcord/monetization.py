"""Fixture authority for application SKUs and entitlements, not a checkout."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord

from ._dpy_internals import get_state
from .backend import serializers
from .backend.errors import SetupError
from .builders import _guard_builder_operation

if TYPE_CHECKING:
    from .env import Env


def _fixture_id(env: Env, value: Any, expected: type | tuple[type, ...], label: str) -> int:
    if isinstance(value, expected):
        if value._env is not env:
            raise SetupError(f"{label} must belong to this Env")
        value = value.id
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**64:
        raise SetupError(f"{label} must be a positive snowflake or a same-Env {label} handle")
    return value


class SKUHandle:
    """An arranged catalog entry. SDK reads are detached snapshots."""

    def __init__(self, env: Env, sku_id: int) -> None:
        self._env = env
        self._id = sku_id

    @property
    def id(self) -> int:
        return self._id

    def snapshot(self) -> discord.SKU:
        """Return a fresh real SDK model, bound to the current bot generation."""
        return discord.SKU(
            state=get_state(self._env.bot),
            data=serializers.sku_payload(self._env.backend.skus[self.id]),
        )


class EntitlementHandle:
    """Arrange live transitions without changing previously returned SDK models."""

    def __init__(self, env: Env, entitlement_id: int) -> None:
        self._env = env
        self._id = entitlement_id

    @property
    def id(self) -> int:
        return self._id

    def snapshot(self) -> discord.Entitlement:
        """Return current authoritative state as a detached real SDK object."""
        return discord.Entitlement(
            state=get_state(self._env.bot),
            data=serializers.entitlement_payload(self._env.backend.get_entitlement(self.id)),
        )

    def _require_live(self) -> None:
        if not self._env._started:
            raise SetupError("Entitlement transitions require a running Env")

    async def end(self) -> None:
        """End access now, retaining the historical entitlement and emitting UPDATE."""
        self._require_live()
        self._env.backend.end_entitlement(self.id)
        self._env._mark_mutation()
        await self._env._settle_internal(dispatch="ENTITLEMENT_UPDATE")

    async def schedule_end(self, at_or_none: datetime | None) -> None:
        """Replace or cancel a future end action; this is not a billing period."""
        self._require_live()
        self._env.backend.schedule_entitlement_end(self.id, at_or_none)
        self._env._mark_mutation()
        await self._env._settle_internal()

    async def revoke(self) -> None:
        """Revoke access with DELETE, without implying a refund."""
        self._require_live()
        self._env.backend.revoke_entitlement(self.id)
        self._env._mark_mutation()
        await self._env._settle_internal(dispatch="ENTITLEMENT_DELETE")

    async def replay_create(self) -> None:
        """Deliver the original CREATE again, without resetting authoritative state."""
        self._require_live()
        self._env.backend.replay_entitlement_create(self.id)
        self._env._mark_mutation()
        await self._env._settle_internal(dispatch="ENTITLEMENT_CREATE replay")


for _operation_name in ("end", "schedule_end", "revoke", "replay_create"):
    setattr(
        EntitlementHandle,
        _operation_name,
        _guard_builder_operation(getattr(EntitlementHandle, _operation_name)),
    )
