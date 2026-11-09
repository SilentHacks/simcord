"""Deterministic temporal/safety checks; no Discord lifecycle is simulated."""

import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location(
    "monetization_reference_lifecycle",
    Path(__file__).resolve().parents[2] / "scripts" / "monetization_reference_lifecycle.py",
)
lifecycle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lifecycle)

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)
ROW = {
    "id": "123",
    "application_id": "456",
    "sku_id": "789",
    "type": 4,
    "user_id": "111",
    "deleted": False,
    "starts_at": None,
    "ends_at": NOW.isoformat(),
}
SKU = {"id": "789", "type": 3}


def test_temporal_boundary_is_not_an_interaction_eligibility_claim():
    assert lifecycle.classify_temporal(ROW, NOW - timedelta(microseconds=1)) == "active"
    assert lifecycle.classify_temporal(ROW, NOW) == "ended"
    future = {**ROW, "starts_at": NOW.isoformat(), "ends_at": None}
    assert lifecycle.classify_temporal(future, NOW - timedelta(microseconds=1)) == "future"
    assert lifecycle.classify_temporal(future, NOW) == "active"
    assert lifecycle.classify_temporal({**ROW, "deleted": True}, NOW) == "deleted"


@pytest.mark.parametrize(
    "patch",
    [
        {"ends_at": "not-a-date"},
        {"ends_at": "2026-10-21T12:00:00"},
        {"deleted": None},
        {"starts_at": (NOW + timedelta(days=1)).isoformat()},
    ],
)
def test_malformed_or_contradictory_time_is_unknown(patch):
    assert lifecycle.classify_temporal({**ROW, **patch}, NOW) == "unknown"


def test_missing_fields_are_not_synthesized_as_null():
    missing = {key: value for key, value in ROW.items() if key != "ends_at"}
    assert lifecycle.classify_temporal(missing, NOW) == "unknown"
    assert lifecycle.classify_temporal({**missing, "ends_at": None}, NOW) == "active"


def test_only_unchanged_non_deleted_schedule_proves_temporal_transition():
    before, after = NOW - timedelta(seconds=1), NOW + timedelta(seconds=1)
    assert lifecycle.classify_transition(ROW, dict(ROW), before, after) == "natural_end"
    for patch in (
        {"deleted": True},
        {"ends_at": None},
        {"id": "124"},
        {"type": 1},
        {"ends_at": (NOW - timedelta(seconds=2)).isoformat()},
    ):
        assert lifecycle.classify_transition(ROW, {**ROW, **patch}, before, after) is None
    assert lifecycle.classify_transition(ROW, ROW, after, after) is None
    assert lifecycle.classify_transition(ROW, ROW, after, before) is None
    future = {**ROW, "starts_at": NOW.isoformat(), "ends_at": None}
    assert lifecycle.classify_transition(future, dict(future), before, after) == "natural_start"


def test_discord_can_publish_end_timestamp_only_when_subscription_expires():
    before, after = NOW - timedelta(seconds=1), NOW + timedelta(seconds=1)
    active = {**ROW, "type": 8, "ends_at": None}
    expired = {**active, "ends_at": NOW.isoformat()}
    assert lifecycle.classify_transition(active, expired, before, after) == "natural_end"
    assert lifecycle.classify_transition(active, {**expired, "deleted": True}, before, after) is None
    assert (
        lifecycle.classify_transition(active, {**expired, "ends_at": before.isoformat()}, before, after)
        is None
    )


def test_consume_requires_matching_application_owner_sku_and_type_four():
    validate = lifecycle.validate_fixture
    assert validate(ROW, "456", SKU, "111", "222", consume=True) is None
    for patch in (
        {"application_id": "999"},
        {"user_id": "999"},
        {"guild_id": "999"},
        {"sku_id": "999"},
        {"type": 1},
        {"type": 8},
    ):
        assert validate({**ROW, **patch}, "456", SKU, "111", "222", consume=True)
    assert validate(ROW, "456", {**SKU, "type": 2}, "111", "222", consume=True)
    assert validate(None, "456", SKU, "111", "222", consume=True)
    # A guild grant's purchaser is not its owner; only the authorized guild owns it.
    guild = {**ROW, "guild_id": "222", "user_id": "999"}
    assert validate(guild, "456", SKU, "111", "222", consume=True) is None


def test_no_fixture_run_only_records_blockers_without_operator_prompts():
    state = {"cases": {}, "sections": {}}

    def block(case, reason):
        state["cases"][case] = {"status": "blocked", "reason": reason}

    # No network/probe/create methods exist: any unwanted action fails this check.
    ctx = SimpleNamespace(
        state=state,
        save=lambda: None,
        block=block,
        lifecycle=SimpleNamespace(watch_entitlement=[], consume_history=[]),
        owner_type=2,
        skus=[{"id": "789", "type": 5, "flags": 256}],
        subscriptions=[],
    )
    asyncio.run(lifecycle.capture_guild(ctx))
    asyncio.run(lifecycle.capture_lifecycle(ctx))
    assert state["cases"]["guild.prerequisite"]["status"] == "blocked"
    for case in (
        "natural_expiry",
        "future_start",
        "exact_boundary",
        "consume_ended",
        "consume_deleted",
        "duplicate_after_natural_end",
    ):
        assert state["cases"]["lifecycle." + case]["status"] == "blocked"
    assert state["sections"]["lifecycle"]["done"] is True


def test_cleanup_never_submits_unowned_or_already_deleted_ids():
    removed = []

    async def remove(case, entitlement_id):
        removed.append(entitlement_id)

    ctx = SimpleNamespace(
        state={
            "owned_entitlements": {
                "owned": {"deleted": False},
                "gone": {"deleted": True},
            }
        },
        remove=remove,
    )
    for entitlement_id in ("foreign", "gone", "owned"):
        asyncio.run(lifecycle._cleanup(ctx, "cleanup", entitlement_id))
    assert removed == ["owned"]
    blocked = []
    ctx.block = lambda case, reason: blocked.append(case)
    ctx.state["pending_write"] = {"method": "DELETE", "entitlement_id": "owned"}
    asyncio.run(lifecycle._cleanup(ctx, "cleanup.uncertain", "owned"))
    assert removed == ["owned"]
    assert blocked == ["cleanup.uncertain"]
