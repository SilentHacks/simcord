"""Application-scoped SKU catalog and entitlement lifecycle."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .. import errors, serializers
from ..models import SKU, Entitlement
from .base import BackendBase

if TYPE_CHECKING:
    from discord.types.sku import Entitlement as EntitlementPayload


def _snowflake(value: int | None, name: str) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 1 << 64
    ):
        raise errors.SetupError(f"{name} must be a positive 64-bit snowflake")


def _time(value: datetime | None, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise errors.SetupError(f"{name} must be an aware datetime")
    return value.astimezone(UTC)


class MonetizationMixin(BackendBase):
    def create_sku(
        self,
        name: str,
        *,
        type: int = 5,
        flags: int = 0,
        id: int | None = None,
        slug: str | None = None,
    ) -> SKU:
        if isinstance(type, bool) or not isinstance(type, int) or type not in (2, 3, 5):
            raise errors.SetupError("SKU type must be durable (2), consumable (3), or subscription (5)")
        if not isinstance(name, str) or not name.strip():
            raise errors.SetupError("SKU name must be a nonempty string")
        if isinstance(flags, bool) or not isinstance(flags, int) or flags < 0:
            raise errors.SetupError("SKU flags must be a nonnegative integer")
        if slug is not None and (not isinstance(slug, str) or not slug):
            raise errors.SetupError("SKU slug must be a nonempty string")
        _snowflake(id, "id")
        slug = slug if slug is not None else re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "sku"
        sku_id = self.snowflake() if id is None else self.reserve_id(id)
        sku = SKU(sku_id, self.application_id, type, name, slug, flags)
        # Allocate the entire pair before publishing either catalog row.
        group = SKU(self.snowflake(), self.application_id, 6, name, slug, flags) if type == 5 else None
        self.skus[sku.id] = sku
        if group is not None:
            self.skus[group.id] = group
        return sku

    def get_sku(self, sku_id: int) -> SKU:
        sku = self.skus.get(sku_id)
        if sku is None:
            raise errors.unknown_sku()
        return sku

    def get_entitlement(self, entitlement_id: int) -> Entitlement:
        entitlement = self.entitlements.get(entitlement_id)
        if entitlement is None:
            raise errors.unknown_entitlement()
        return entitlement

    def _insert_entitlement(
        self,
        sku_id: int,
        *,
        user_id: int | None = None,
        guild_id: int | None = None,
        type: int | None = None,
        id: int | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        deleted: bool = False,
        consumed: bool = False,
        scheduled_end_at: datetime | None = None,
        created_via_test_api: bool = False,
    ) -> Entitlement:
        sku = self.get_sku(sku_id)
        if sku.type == 6:
            raise errors.SetupError("subscription groups cannot be granted")
        _snowflake(id, "id")
        _snowflake(user_id, "user_id")
        _snowflake(guild_id, "guild_id")
        if user_id is None and guild_id is None:
            raise errors.SetupError("an entitlement needs a user or guild owner")
        if type is None:
            type = 8 if sku.type == 5 else 1
        if isinstance(type, bool) or not isinstance(type, int) or type not in range(1, 9):
            raise errors.SetupError("entitlement type must be a Discord entitlement type (1-8)")
        if not isinstance(deleted, bool) or not isinstance(consumed, bool):
            raise errors.SetupError("deleted and consumed must be booleans")
        starts_at = _time(starts_at, "starts_at")
        ends_at = _time(ends_at, "ends_at")
        scheduled_end_at = _time(scheduled_end_at, "scheduled_end_at")
        if starts_at is not None and ends_at is not None and ends_at < starts_at:
            raise errors.SetupError("ends_at cannot precede starts_at")
        if consumed and sku.type != 3:
            raise errors.SetupError("only consumable entitlements can be consumed")
        if scheduled_end_at is not None:
            self._validate_entitlement_schedule(
                sku, starts_at, ends_at, deleted, consumed, created_via_test_api, scheduled_end_at
            )
        entitlement_id = self.snowflake() if id is None else self.reserve_id(id)
        entitlement = Entitlement(
            id=entitlement_id,
            application_id=self.application_id,
            sku_id=sku_id,
            type=type,
            user_id=user_id,
            guild_id=guild_id,
            starts_at=starts_at,
            ends_at=ends_at,
            deleted=deleted,
            consumed=consumed,
            created_via_test_api=created_via_test_api,
            scheduled_end_at=scheduled_end_at,
        )
        self.entitlements[entitlement.id] = entitlement
        return entitlement

    def seed_entitlement(
        self,
        sku_id: int,
        *,
        user_id: int | None = None,
        guild_id: int | None = None,
        type: int | None = None,
        id: int | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        deleted: bool = False,
        consumed: bool = False,
        scheduled_end_at: datetime | None = None,
    ) -> Entitlement:
        return self._insert_entitlement(
            sku_id,
            user_id=user_id,
            guild_id=guild_id,
            type=type,
            id=id,
            starts_at=starts_at,
            ends_at=ends_at,
            deleted=deleted,
            consumed=consumed,
            scheduled_end_at=scheduled_end_at,
        )

    def grant_entitlement(
        self,
        sku_id: int,
        *,
        user_id: int | None = None,
        guild_id: int | None = None,
        type: int | None = None,
        id: int | None = None,
        starts_at: datetime | None = None,
        scheduled_end_at: datetime | None = None,
    ) -> Entitlement:
        starts_at = _time(starts_at, "starts_at")
        if starts_at is not None and starts_at < self.clock.now():
            raise errors.SetupError("historical grants belong in seed_entitlement")
        entitlement = self._insert_entitlement(
            sku_id,
            user_id=user_id,
            guild_id=guild_id,
            type=type,
            id=id,
            starts_at=starts_at,
            scheduled_end_at=scheduled_end_at,
        )
        self._emit_entitlement_create(entitlement)
        return entitlement

    def _emit_entitlement_create(self, entitlement: Entitlement) -> None:
        payload = serializers.entitlement_payload(entitlement)
        entitlement.original_create_payload = deepcopy(payload)
        self.emit("ENTITLEMENT_CREATE", payload)

    def _validate_entitlement_schedule(
        self,
        sku: SKU,
        starts_at: datetime | None,
        ends_at: datetime | None,
        deleted: bool,
        consumed: bool,
        test_api: bool,
        at: datetime,
    ) -> None:
        if sku.type != 5 or test_api or deleted or consumed or ends_at is not None:
            raise errors.SetupError("only unended, non-test subscription grants can have a scheduled end")
        if at <= self.clock.now() or (starts_at is not None and at <= starts_at):
            raise errors.SetupError("scheduled_end_at must follow now and starts_at")

    def schedule_entitlement_end(self, entitlement_id: int, at: datetime | None) -> None:
        entitlement = self.get_entitlement(entitlement_id)
        at = _time(at, "scheduled_end_at")
        if at is not None:
            self._validate_entitlement_schedule(
                self.get_sku(entitlement.sku_id),
                entitlement.starts_at,
                entitlement.ends_at,
                entitlement.deleted,
                entitlement.consumed,
                entitlement.created_via_test_api,
                at,
            )
        entitlement.scheduled_end_at = at

    def end_entitlement(self, entitlement_id: int) -> None:
        entitlement = self.get_entitlement(entitlement_id)
        if self.get_sku(entitlement.sku_id).type != 5 or entitlement.created_via_test_api:
            raise errors.SetupError("only non-test subscription grants can end; revoke other grants")
        now = self.clock.now()
        if entitlement.deleted or (entitlement.starts_at is not None and entitlement.starts_at > now):
            raise errors.SetupError("only started, nondeleted subscription grants can end")
        entitlement.scheduled_end_at = None
        if entitlement.ends_at is not None and entitlement.ends_at <= now:
            return
        entitlement.ends_at = now
        self.emit("ENTITLEMENT_UPDATE", serializers.entitlement_payload(entitlement))

    def revoke_entitlement(self, entitlement_id: int) -> None:
        entitlement = self.get_entitlement(entitlement_id)
        entitlement.scheduled_end_at = None
        if entitlement.deleted:
            return
        entitlement.deleted = True
        self.emit("ENTITLEMENT_DELETE", serializers.entitlement_payload(entitlement))

    def replay_entitlement_create(self, entitlement_id: int) -> None:
        original = self.get_entitlement(entitlement_id).original_create_payload
        if original is None:
            raise errors.SetupError("seeded entitlements have no original CREATE event to replay")
        self.emit("ENTITLEMENT_CREATE", deepcopy(original))

    def next_entitlement_deadline(self) -> datetime | None:
        return min(
            (e.scheduled_end_at for e in self.entitlements.values() if e.scheduled_end_at is not None),
            default=None,
        )

    def process_entitlement_deadlines(self) -> None:
        now = self.clock.now()
        for entitlement in list(self.entitlements.values()):
            if entitlement.scheduled_end_at is not None and entitlement.scheduled_end_at <= now:
                self.end_entitlement(entitlement.id)

    def interaction_entitlements(
        self,
        user_id: int,
        guild_id: int | None = None,
    ) -> list[EntitlementPayload]:
        """Access in this invocation, not purchaser history or target-user access."""
        # Access-state filtering is an explicit simulator policy; official docs
        # define the owner selection but do not enumerate these temporal edges.
        now = self.clock.now()
        return [
            serializers.entitlement_payload(e)
            for e in self.entitlements.values()
            if (e.guild_id == guild_id if e.guild_id is not None else e.user_id == user_id)
            and not e.deleted
            and not e.consumed
            and (e.starts_at is None or e.starts_at <= now)
            and (e.ends_at is None or e.ends_at > now)
        ]

    def create_test_entitlement(self, sku_id: int, owner_id: int, owner_type: int) -> Entitlement:
        sku = self.get_sku(sku_id)
        if sku.type != 5:
            raise errors.invalid_form_body("API test entitlements require a subscription SKU")
        if owner_type not in (1, 2) or isinstance(owner_type, bool):
            raise errors.invalid_form_body("owner_type must be 1 (guild) or 2 (user)")
        if isinstance(owner_id, bool) or not isinstance(owner_id, int) or not 0 < owner_id < 1 << 64:
            raise errors.invalid_form_body("owner_id must be a positive 64-bit snowflake")
        if any(
            e.sku_id == sku_id
            and not e.deleted
            and (e.ends_at is None or e.ends_at > self.clock.now())
            and (e.guild_id == owner_id if owner_type == 1 else e.guild_id is None and e.user_id == owner_id)
            for e in self.entitlements.values()
        ):
            raise errors.entitlement_already_granted()
        entitlement = self._insert_entitlement(
            sku_id,
            guild_id=owner_id if owner_type == 1 else None,
            user_id=owner_id if owner_type == 2 else None,
            created_via_test_api=True,
        )
        self._emit_entitlement_create(entitlement)
        return entitlement

    def delete_test_entitlement(self, entitlement_id: int) -> None:
        entitlement = self.get_entitlement(entitlement_id)
        if not entitlement.created_via_test_api:
            raise errors.not_sandbox_entitlement()
        self.revoke_entitlement(entitlement_id)

    def consume_entitlement(self, entitlement_id: int) -> None:
        entitlement = self.get_entitlement(entitlement_id)
        if self.get_sku(entitlement.sku_id).type != 3:
            raise errors.not_consumable()
        if not entitlement.consumed:
            entitlement.consumed = True
            self.emit("ENTITLEMENT_UPDATE", serializers.entitlement_payload(entitlement))
