from datetime import UTC, datetime, timedelta

import discord
import pytest

from simcord.backend import Backend, serializers
from simcord.backend.errors import BackendError, SetupError


def test_catalog_groups_and_real_sdk_snapshots():
    backend = Backend()
    subscription = backend.create_sku("Guild Premium", flags=128)
    durable = backend.create_sku("Lifetime", type=2)
    consumable = backend.create_sku("Boost", type=3)
    assert [sku.type for sku in backend.skus.values()] == [5, 6, 2, 3]
    snapshot = discord.SKU(state=None, data=serializers.sku_payload(subscription))
    assert snapshot.id == subscription.id
    assert snapshot.flags.guild_subscription
    assert durable.slug == "lifetime" and consumable.slug == "boost"
    previous = dict(backend.skus)
    with pytest.raises(SetupError):
        backend.create_sku("Invalid", type=6)
    assert backend.skus == previous


def test_seed_validation_precedes_insert_and_ids_are_reserved():
    backend = Backend()
    sku = backend.create_sku("Recurring")
    with pytest.raises(SetupError):
        backend.seed_entitlement(sku.id, user_id=123, starts_at=datetime(2026, 1, 1))
    with pytest.raises(SetupError):
        backend.seed_entitlement(sku.id)
    with pytest.raises(SetupError):
        backend.seed_entitlement(sku.id, user_id=True)
    assert not backend.entitlements
    seed = backend.seed_entitlement(sku.id, guild_id=456, user_id=123, id=789)
    assert seed.guild_id == 456 and seed.user_id == 123
    with pytest.raises(SetupError):
        backend.seed_entitlement(sku.id, user_id=123, id=789)
    assert list(backend.entitlements) == [789]
    assert not backend.transcript
    with pytest.raises(SetupError, match="no original"):
        backend.replay_entitlement_create(seed.id)


def test_original_create_is_backend_owned_and_replay_does_not_reset_state():
    backend = Backend()
    sku = backend.create_sku("Potion", type=3)
    entitlement = backend.grant_entitlement(sku.id, user_id=123, type=4)
    backend.transcript[-1][2]["consumed"] = True
    backend.consume_entitlement(entitlement.id)
    backend.revoke_entitlement(entitlement.id)
    backend.replay_entitlement_create(entitlement.id)
    replay = backend.transcript[-1][2]
    assert replay["consumed"] is False and replay["deleted"] is False
    replay["consumed"] = True
    backend.replay_entitlement_create(entitlement.id)
    assert backend.transcript[-1][2]["consumed"] is False
    assert entitlement.consumed and entitlement.deleted
    assert not entitlement.created_via_test_api


def test_scheduled_end_replace_cancel_and_history():
    backend = Backend()
    sku = backend.create_sku("Recurring")
    now = backend.clock.now()
    entitlement = backend.seed_entitlement(sku.id, guild_id=456, scheduled_end_at=now + timedelta(seconds=10))
    assert entitlement.ends_at is None
    backend.schedule_entitlement_end(entitlement.id, now + timedelta(seconds=20))
    backend.advance_clock(10)
    backend.process_entitlement_deadlines()
    assert entitlement.ends_at is None
    backend.schedule_entitlement_end(entitlement.id, None)
    assert backend.next_entitlement_deadline() is None
    backend.advance_clock(10)
    backend.process_entitlement_deadlines()
    assert entitlement.ends_at is None
    backend.schedule_entitlement_end(entitlement.id, backend.clock.now() + timedelta(seconds=5))
    backend.advance_clock(5)
    backend.process_entitlement_deadlines()
    assert entitlement.ends_at == now + timedelta(seconds=25)
    assert entitlement.scheduled_end_at is None
    assert backend.transcript[-1][1] == "ENTITLEMENT_UPDATE"
    assert not entitlement.deleted
    historical = backend.seed_entitlement(sku.id, user_id=123, ends_at=now)
    backend.process_entitlement_deadlines()
    assert historical.ends_at == now
    assert len(backend.transcript) == 1


def test_invalid_schedule_never_mutates_or_revives():
    backend = Backend()
    sku = backend.create_sku("Recurring")
    now = backend.clock.now()
    for fields in (
        {"scheduled_end_at": now},
        {"scheduled_end_at": now + timedelta(seconds=1), "deleted": True},
        {"scheduled_end_at": now + timedelta(seconds=1), "ends_at": now},
    ):
        with pytest.raises(SetupError):
            backend.seed_entitlement(sku.id, user_id=123, **fields)
    assert not backend.entitlements
    entitlement = backend.grant_entitlement(sku.id, user_id=123)
    at = now + timedelta(seconds=10)
    backend.schedule_entitlement_end(entitlement.id, at)
    with pytest.raises(SetupError):
        backend.schedule_entitlement_end(entitlement.id, now)
    assert entitlement.scheduled_end_at == at
    backend.revoke_entitlement(entitlement.id)
    assert backend.next_entitlement_deadline() is None


def test_interactions_use_invoker_and_invoking_guild_not_purchaser():
    backend = Backend()
    subscription = backend.create_sku("Guild Premium", flags=128)
    personal = backend.create_sku("Personal", flags=256)
    guild_entitlement = backend.seed_entitlement(subscription.id, user_id=123, guild_id=456)
    user_entitlement = backend.seed_entitlement(personal.id, user_id=123)
    assert {int(e["id"]) for e in backend.interaction_entitlements(999, 456)} == {guild_entitlement.id}
    assert {int(e["id"]) for e in backend.interaction_entitlements(123, 789)} == {user_entitlement.id}
    assert {int(e["id"]) for e in backend.interaction_entitlements(123)} == {user_entitlement.id}
    assert backend.interaction_entitlements(999, 789) == []
    # Owner IDs intentionally need not exist in guild/user caches.
    assert not backend.guilds and 123 not in backend.users


def test_interaction_access_projection_separate_from_rest_history():
    backend = Backend()
    sku = backend.create_sku("Potion", type=3)
    now = backend.clock.now()
    active = backend.seed_entitlement(sku.id, user_id=123)
    backend.seed_entitlement(sku.id, user_id=123, consumed=True)
    backend.seed_entitlement(sku.id, user_id=123, deleted=True)
    backend.seed_entitlement(sku.id, user_id=123, starts_at=now + timedelta(seconds=1))
    backend.seed_entitlement(sku.id, user_id=123, ends_at=now)
    assert [int(e["id"]) for e in backend.interaction_entitlements(123)] == [active.id]
    assert len(backend.entitlements) == 5


def test_times_normalize_utc_and_durable_cannot_end_or_consume():
    backend = Backend()
    sku = backend.create_sku("Lifetime", type=2)
    grant = backend.seed_entitlement(sku.id, user_id=123, starts_at=datetime(2025, 1, 1, tzinfo=UTC))
    with pytest.raises(SetupError):
        backend.end_entitlement(grant.id)
    with pytest.raises(BackendError, match="40018"):
        backend.consume_entitlement(grant.id)
    assert not grant.consumed and grant.ends_at is None
