from copy import deepcopy
from datetime import timedelta

import pytest

from simcord.backend import Backend
from simcord.backend.errors import BackendError
from simcord.http import router
from simcord.http import routes as _routes  # noqa: F401


def _path(backend):
    return f"/applications/{backend.application_id}"


def test_test_entitlement_create_list_get_delete_provenance_and_fields():
    backend = Backend()
    sku = backend.create_sku("Recurring", flags=128)
    path = _path(backend)
    catalog = router.dispatch(backend, "GET", f"{path}/skus")
    assert [item["type"] for item in catalog] == [5]
    created = router.dispatch(
        backend,
        "POST",
        f"{path}/entitlements",
        json={"sku_id": str(sku.id), "owner_id": "123", "owner_type": 1},
    )
    entitlement_id = int(created["id"])
    # S REST 7/8/9 and G17: API dates are null; guild purchaser is the bot.
    assert created["guild_id"] == "123" and created["user_id"] == str(backend.bot_user.id)
    assert backend.bot_user.id != backend.application_id
    assert created["starts_at"] is None and created["ends_at"] is None
    assert created["consumed"] is False and created["type"] == 8
    assert created["fulfilled_at"] == backend.clock.now().isoformat()
    assert created["fulfillment_status"] == 3
    assert created["gift_code_flags"] == 0 and created["promotion_id"] is None
    assert "subscription_id" not in created and "source_type" not in created
    assert backend.entitlements[entitlement_id].created_via_test_api
    listed = router.dispatch(backend, "GET", f"{path}/entitlements")
    assert listed == [created]
    assert router.dispatch(backend, "GET", f"{path}/entitlements/{entitlement_id}") == created
    assert router.dispatch(backend, "DELETE", f"{path}/entitlements/{entitlement_id}") is None
    assert router.dispatch(backend, "GET", f"{path}/entitlements") == []
    deleted = router.dispatch(backend, "GET", f"{path}/entitlements", params={"exclude_deleted": "false"})
    assert deleted[0]["deleted"] is True
    assert backend.transcript[-3][1] == "ENTITLEMENT_DELETE"


def test_live_wire_type_does_not_authorize_test_delete():
    backend = Backend()
    sku = backend.create_sku("Recurring")
    entitlement = backend.seed_entitlement(sku.id, user_id=123, type=1)
    _reject(
        backend,
        "DELETE",
        f"{_path(backend)}/entitlements/{entitlement.id}",
        status=400,
        code=40019,
        message="You can only delete sandbox entitlements.",
    )
    assert not entitlement.deleted


def test_consumption_returns_no_content_preserves_history_and_replay():
    backend = Backend()
    sku = backend.create_sku("Potion", type=3)
    entitlement = backend.grant_entitlement(sku.id, user_id=123)
    path = f"{_path(backend)}/entitlements"
    assert router.dispatch(backend, "POST", f"{path}/{entitlement.id}/consume") is None
    assert router.dispatch(backend, "GET", path)[0]["consumed"] is True
    backend.replay_entitlement_create(entitlement.id)
    assert backend.transcript[-1][2]["consumed"] is False
    assert router.dispatch(backend, "GET", f"{path}/{entitlement.id}")["consumed"] is True


def test_all_write_routes_reject_unknown_fields_before_mutation():
    backend = Backend()
    sku = backend.create_sku("Recurring")
    path = f"{_path(backend)}/entitlements"
    with pytest.raises(router.UnsupportedField):
        router.dispatch(
            backend,
            "POST",
            path,
            json={
                "sku_id": str(sku.id),
                "owner_id": "123",
                "owner_type": 2,
                "unknown": True,
            },
        )
    assert not backend.entitlements
    entitlement = backend.seed_entitlement(sku.id, user_id=123)
    for method, suffix in (("DELETE", ""), ("POST", "/consume")):
        with pytest.raises(router.UnsupportedField):
            router.dispatch(backend, method, f"{path}/{entitlement.id}{suffix}", json={"x": 1})
    assert not entitlement.deleted and not entitlement.consumed


def test_list_filter_categories_before_limit_and_consumed_not_silently_excluded():
    backend = Backend()
    wanted = backend.create_sku("Wanted", type=3)
    other = backend.create_sku("Other", type=3)
    backend.seed_entitlement(other.id, user_id=123, guild_id=456)
    backend.seed_entitlement(wanted.id, user_id=999, guild_id=456)
    backend.seed_entitlement(wanted.id, user_id=123, guild_id=999)
    expected = backend.seed_entitlement(wanted.id, user_id=123, guild_id=456, consumed=True)
    backend.seed_entitlement(wanted.id, user_id=123, guild_id=456, deleted=True)
    backend.seed_entitlement(wanted.id, user_id=123, guild_id=456, ends_at=backend.clock.now())
    rows = router.dispatch(
        backend,
        "GET",
        f"{_path(backend)}/entitlements",
        params={
            "user_id": "123",
            "guild_id": "456",
            "sku_ids": str(wanted.id),
            "limit": "1",
            "exclude_ended": "true",
        },
    )
    assert [int(row["id"]) for row in rows] == [expected.id]
    assert rows[0]["consumed"] is True
    rows = router.dispatch(
        backend,
        "GET",
        f"{_path(backend)}/entitlements",
        params={
            "sku_ids": f"{wanted.id},{other.id}",
            "exclude_deleted": "0",
        },
    )
    assert len(rows) == 6


def test_after_and_before_pagination_exceed_one_hundred_without_skips():
    backend = Backend()
    sku = backend.create_sku("Recurring")
    expected = [backend.seed_entitlement(sku.id, user_id=123).id for _ in range(237)]
    path = f"{_path(backend)}/entitlements"
    ascending = []
    after = 0
    for _ in range(4):
        page = router.dispatch(backend, "GET", path, params={"after": str(after), "limit": 100})
        ascending.extend(int(row["id"]) for row in page)
        if len(page) < 100:
            break
        after = int(page[-1]["id"])
    assert ascending == expected
    descending = []
    before = expected[-1] + 1
    for _ in range(4):
        page = router.dispatch(backend, "GET", path, params={"before": str(before), "limit": 100})
        descending.extend(int(row["id"]) for row in page)
        if len(page) < 100:
            break
        before = int(page[-1]["id"])
    assert descending == expected[::-1]


def test_received_exclude_deleted_controls_request_not_sdk_intent():
    backend = Backend()
    sku = backend.create_sku("Recurring")
    backend.seed_entitlement(sku.id, user_id=123, deleted=True)
    path = f"{_path(backend)}/entitlements"
    assert router.dispatch(backend, "GET", path, params={"after": "0"}) == []
    assert len(router.dispatch(backend, "GET", path, params={"exclude_deleted": False})) == 1


def test_list_ended_default_and_future_start_are_history():
    backend = Backend()
    sku = backend.create_sku("Recurring")
    ended = backend.seed_entitlement(sku.id, user_id=123, ends_at=backend.clock.now())
    future = backend.seed_entitlement(sku.id, user_id=123, starts_at=backend.clock.now() + timedelta(days=1))
    path = f"{_path(backend)}/entitlements"
    assert len(router.dispatch(backend, "GET", path)) == 2
    assert [
        int(row["id"]) for row in router.dispatch(backend, "GET", path, params={"exclude_ended": True})
    ] == [future.id]
    assert ended.id != future.id


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": "x"},
        {"before": "1", "after": "2"},
        {"exclude_deleted": "maybe"},
    ],
)
def test_invalid_query_is_discord_form_error(params):
    backend = Backend()
    with pytest.raises(BackendError) as failure:
        router.dispatch(backend, "GET", f"{_path(backend)}/entitlements", params=params)
    assert failure.value.code == 50035


def test_missing_resources_have_known_codes():
    backend = Backend()
    with pytest.raises(BackendError) as missing_sku:
        backend.get_sku(123)
    assert missing_sku.value.code == 10027
    with pytest.raises(BackendError) as missing_entitlement:
        router.dispatch(backend, "GET", f"{_path(backend)}/entitlements/123")
    assert missing_entitlement.value.code == 10029


def test_explicit_repeated_mutation_and_duplicate_error_policies():
    backend = Backend()
    sku = backend.create_sku("Recurring", flags=256)
    path = f"{_path(backend)}/entitlements"
    body = {"sku_id": str(sku.id), "owner_id": "123", "owner_type": 2}
    created = router.dispatch(backend, "POST", path, json=body)
    assert created["user_id"] == "123" and "guild_id" not in created
    with pytest.raises(BackendError) as duplicate:
        router.dispatch(backend, "POST", path, json=body)
    assert (duplicate.value.status, duplicate.value.code) == (400, 40074)
    individual = f"{path}/{created['id']}"
    router.dispatch(backend, "DELETE", individual)
    with pytest.raises(BackendError, match="10029"):
        router.dispatch(backend, "DELETE", individual)
    assert router.dispatch(backend, "GET", individual)["deleted"] is True
    assert router.dispatch(backend, "POST", path, json=body)["deleted"] is False
    consumable = backend.create_sku("Potion", type=3)
    grant = backend.grant_entitlement(consumable.id, user_id=123)
    router.dispatch(backend, "POST", f"{path}/{grant.id}/consume")
    with pytest.raises(BackendError, match="40022"):
        router.dispatch(backend, "POST", f"{path}/{grant.id}/consume")
    assert sum(event == "ENTITLEMENT_UPDATE" for _, event, _ in backend.transcript) == 1


def test_wrong_kind_errors_are_real_http_backend_errors():
    backend = Backend()
    sku = backend.create_sku("Lifetime", type=2)
    grant = backend.seed_entitlement(sku.id, user_id=123)
    with pytest.raises(BackendError) as wrong_kind:
        router.dispatch(backend, "POST", f"{_path(backend)}/entitlements/{grant.id}/consume")
    assert wrong_kind.value.status == 400
    assert wrong_kind.value.to_json() == {"code": 40018, "message": "Only consumable SKUs can be consumed"}


def _reject(backend, method, path, *, status, code, message, json=None, errors=None):
    events = sum(kind == "GATEWAY" for kind, _, _ in backend.transcript)
    rows = deepcopy(backend.entitlements)
    with pytest.raises(BackendError) as failure:
        router.dispatch(backend, method, path, json=json)
    assert failure.value.status == status
    expected = {"code": code, "message": message}
    if errors is not None:
        expected["errors"] = errors
    assert failure.value.to_json() == expected
    assert sum(kind == "GATEWAY" for kind, _, _ in backend.transcript) == events
    assert backend.entitlements == rows


@pytest.mark.parametrize(
    "field,value,coercion,description",
    [
        ("owner_type", 0, "ENUM_TYPE_COERCE", "a valid enum value"),
        ("owner_type", True, "ENUM_TYPE_COERCE", "a valid enum value"),
        ("owner_type", 1.0, "ENUM_TYPE_COERCE", "a valid enum value"),
        ("owner_id", "not-a-snowflake", "NUMBER_TYPE_COERCE", "snowflake"),
        ("owner_id", 0, "NUMBER_TYPE_COERCE", "snowflake"),
        ("owner_id", 1 << 64, "NUMBER_TYPE_COERCE", "snowflake"),
        ("owner_id", True, "NUMBER_TYPE_COERCE", "snowflake"),
        ("owner_id", 123.0, "NUMBER_TYPE_COERCE", "snowflake"),
    ],
)
def test_owner_validation_precedes_unknown_sku_with_exact_field_errors(field, value, coercion, description):
    backend = Backend()
    body = {"sku_id": "1", "owner_id": "123", "owner_type": 1, field: value}
    # R REST 9-12: original bad owner survives coercion and beats unknown SKU.
    _reject(
        backend,
        "POST",
        f"{_path(backend)}/entitlements",
        json=body,
        status=400,
        code=50035,
        message="Invalid Form Body",
        errors={
            field: {"_errors": [{"code": coercion, "message": f'Value "{value}" is not {description}.'}]}
        },
    )
    assert not backend.entitlements


@pytest.mark.parametrize(
    "sku_type,flags,owner_type",
    [
        (2, 128, 1),
        (3, 256, 2),
        (6, 128, 1),
        (5, 128, 2),
        (5, 256, 1),
        (5, 0, 1),
        (5, 384, 2),
    ],
)
def test_invalid_sku_and_scope_fail_without_insertion(sku_type, flags, owner_type):
    backend = Backend()
    sku = backend.create_sku("Ineligible", type=sku_type, flags=flags)
    # G73/74 calibrate wrong scope; zero/both bits are fail-fast simulator policy.
    _reject(
        backend,
        "POST",
        f"{_path(backend)}/entitlements",
        json={"sku_id": str(sku.id), "owner_id": "123", "owner_type": owner_type},
        status=400,
        code=50057,
        message="Invalid SKU",
    )
    assert not backend.entitlements


def test_unknown_sku_create_status_and_unrelated_error_bodies_are_unchanged():
    from simcord.backend import errors

    backend = Backend()
    _reject(
        backend,
        "POST",
        f"{_path(backend)}/entitlements",
        json={"sku_id": "1", "owner_id": "123", "owner_type": 2},
        status=400,
        code=10027,
        message="Unknown SKU",
    )
    with pytest.raises(BackendError) as failure:
        backend.get_sku(1)
    assert failure.value.status == 404
    assert failure.value.to_json() == {"code": 10027, "message": "Unknown SKU"}
    assert errors.invalid_form_body("legacy").to_json() == {
        "code": 50035,
        "message": "Invalid Form Body: legacy",
    }
    assert BackendError(403, 50013, "Missing Permissions").to_json() == {
        "code": 50013,
        "message": "Missing Permissions",
    }


@pytest.mark.parametrize("owner_type", [1, 2])
@pytest.mark.parametrize("existing_type", [1, 4, 8])
@pytest.mark.parametrize(
    "state,blocks",
    [
        ("active", True),
        ("future", True),
        ("ended", False),
        ("deleted", False),
    ],
)
def test_subscription_conflicts_cross_tier_and_origin(owner_type, existing_type, state, blocks):
    backend = Backend()
    first = backend.create_sku("Tier B", flags=128 if owner_type == 1 else 256)
    second = backend.create_sku("Tier A", flags=first.flags)
    fields = {"guild_id": 123, "user_id": 999} if owner_type == 1 else {"user_id": 123}
    if state == "ended":
        fields["ends_at"] = backend.clock.now()
    elif state == "future":
        fields["starts_at"] = backend.clock.now() + timedelta(days=1)
    elif state == "deleted":
        fields["deleted"] = True
    backend.seed_entitlement(first.id, type=existing_type, **fields)
    path = f"{_path(backend)}/entitlements"
    body = {"sku_id": str(second.id), "owner_id": "123", "owner_type": owner_type}
    # R50-64/G32-46; U53: live type1 tier B blocks sandbox type8 tier A.
    # Future/end boundary behavior remains simulator policy.
    if blocks:
        _reject(
            backend,
            "POST",
            path,
            json=body,
            status=400,
            code=40074,
            message="An entitlement has already been granted for this resource",
        )
        assert len(backend.entitlements) == 1
    else:
        assert router.dispatch(backend, "POST", path, json=body)["type"] == 8


def test_one_time_rows_do_not_block_and_guild_purchaser_is_not_personal_owner():
    backend = Backend()
    for kind in (2, 3):
        sku = backend.create_sku("One time", type=kind)
        backend.seed_entitlement(sku.id, user_id=123)
        backend.seed_entitlement(sku.id, guild_id=456, user_id=123)
    guild = backend.create_sku("Guild", flags=128)
    personal = backend.create_sku("Personal", flags=256)
    path = f"{_path(backend)}/entitlements"
    for sku, owner, owner_type in (
        (guild, 456, 1),
        (personal, 123, 2),
        (personal, backend.bot_user.id, 2),
        (guild, 123, 1),
    ):
        created = router.dispatch(
            backend,
            "POST",
            path,
            json={
                "sku_id": str(sku.id),
                "owner_id": str(owner),
                "owner_type": owner_type,
            },
        )
        assert created["deleted"] is False


@pytest.mark.parametrize("sku_type", [2, 3])
def test_type_four_delete_and_deleted_mutation_precedence_preserve_history(sku_type):
    backend = Backend()
    sku = backend.create_sku("Test purchase", type=sku_type)
    row = backend.grant_entitlement(sku.id, user_id=123, type=4)
    path = f"{_path(backend)}/entitlements/{row.id}"
    assert router.dispatch(backend, "DELETE", path) is None
    # R29/40 and G375/381/387: DELETE succeeds; later consume is unknown, not wrong-kind/consumed.
    for method, suffix in (("DELETE", ""), ("POST", "/consume")):
        _reject(backend, method, path + suffix, status=404, code=10029, message="Unknown Entitlement")
    history = router.dispatch(backend, "GET", path)
    assert history["deleted"] is True and history["fulfillment_status"] == 6
    assert not row.consumed


def test_repeat_consume_has_exact_envelope_and_no_second_update():
    backend = Backend()
    sku = backend.create_sku("Potion", type=3)
    row = backend.grant_entitlement(sku.id, user_id=123, type=4)
    path = f"{_path(backend)}/entitlements/{row.id}/consume"
    router.dispatch(backend, "POST", path)
    # P12/P16/P25: first 204, repeated 40022, durable 40018.
    _reject(backend, "POST", path, status=400, code=40022, message="Entitlement already consumed")
    assert row.consumed
    backend.revoke_entitlement(row.id)
    _reject(backend, "POST", path, status=404, code=10029, message="Unknown Entitlement")


def test_numeric_direction_strict_nearest_cursors_and_full_raw_deleted_history():
    backend = Backend()
    sku = backend.create_sku("Recurring")
    for entitlement_id in (9, 101, 10, 99, 100):
        backend.seed_entitlement(sku.id, user_id=123, id=entitlement_id)
    path = f"{_path(backend)}/entitlements"

    def ids(**params):
        return [int(row["id"]) for row in router.dispatch(backend, "GET", path, params=params)]

    assert ids(limit=3) == [101, 100, 99]
    assert ids(before=100, limit=2) == [99, 10]
    assert ids(after=10, limit=2) == [99, 100]
    for row in backend.entitlements.values():
        backend.revoke_entitlement(row.id)
    for entitlement_id in range(200, 405):
        backend.seed_entitlement(sku.id, user_id=123, id=entitlement_id, deleted=True)
    expected = sorted(backend.entitlements, reverse=True)
    assert ids(limit=100) == []
    actual = []
    cursor = None
    # R/G raw history evidence: oldest returned cursor, bounded traversal, no SDK workaround.
    for _ in range(4):
        params = {"limit": 100, "exclude_deleted": False}
        if cursor is not None:
            params["before"] = cursor
        page = ids(**params)
        actual.extend(page)
        if len(page) < 100:
            break
        cursor = page[-1]
    assert actual == expected
