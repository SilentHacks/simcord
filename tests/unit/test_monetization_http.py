from datetime import timedelta

import pytest

from simcord.backend import Backend
from simcord.backend.errors import BackendError
from simcord.http import router
from simcord.http import routes as _routes  # noqa: F401


def _path(backend):
    return f"/applications/{backend.application_id}"


def test_test_entitlement_create_list_get_delete_provenance_and_omissions():
    backend = Backend()
    sku = backend.create_sku("Recurring")
    path = _path(backend)
    catalog = router.dispatch(backend, "GET", f"{path}/skus")
    assert {item["type"] for item in catalog} == {5, 6}
    created = router.dispatch(
        backend,
        "POST",
        f"{path}/entitlements",
        json={"sku_id": str(sku.id), "owner_id": "123", "owner_type": 1},
    )
    entitlement_id = int(created["id"])
    assert created["guild_id"] == "123" and "user_id" not in created
    assert not {"starts_at", "ends_at", "subscription_id"} & created.keys()
    assert backend.entitlements[entitlement_id].created_via_test_api
    listed = router.dispatch(backend, "GET", f"{path}/entitlements")
    assert listed == [created]
    assert router.dispatch(backend, "GET", f"{path}/entitlements/{entitlement_id}") == created
    assert router.dispatch(backend, "DELETE", f"{path}/entitlements/{entitlement_id}") is None
    assert router.dispatch(backend, "GET", f"{path}/entitlements") == []
    deleted = router.dispatch(backend, "GET", f"{path}/entitlements", params={"exclude_deleted": "false"})
    assert deleted[0]["deleted"] is True
    assert backend.transcript[-3][1] == "ENTITLEMENT_DELETE"


def test_non_sandbox_wire_type_does_not_authorize_test_delete():
    backend = Backend()
    sku = backend.create_sku("Recurring")
    entitlement = backend.seed_entitlement(sku.id, user_id=123, type=4)
    with pytest.raises(BackendError, match="40019"):
        router.dispatch(backend, "DELETE", f"{_path(backend)}/entitlements/{entitlement.id}")
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
    after = 0
    ascending = []
    while True:
        page = router.dispatch(backend, "GET", path, params={"after": str(after)})
        if not page:
            break
        ascending.extend(int(row["id"]) for row in page)
        after = int(page[-1]["id"])
    assert ascending == expected
    before = expected[-1] + 1
    pages = []
    while True:
        page = router.dispatch(backend, "GET", path, params={"before": str(before)})
        if not page:
            break
        pages.insert(0, [int(row["id"]) for row in page])
        before = int(page[0]["id"])
    assert [item for page in pages for item in page] == expected


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
    sku = backend.create_sku("Recurring")
    path = f"{_path(backend)}/entitlements"
    body = {"sku_id": str(sku.id), "owner_id": "123", "owner_type": 2}
    created = router.dispatch(backend, "POST", path, json=body)
    assert created["user_id"] == "123" and "guild_id" not in created
    with pytest.raises(BackendError) as duplicate:
        router.dispatch(backend, "POST", path, json=body)
    assert (duplicate.value.status, duplicate.value.code) == (400, 40074)
    individual = f"{path}/{created['id']}"
    router.dispatch(backend, "DELETE", individual)
    assert router.dispatch(backend, "DELETE", individual) is None
    assert router.dispatch(backend, "GET", individual)["deleted"] is True
    assert router.dispatch(backend, "POST", path, json=body)["deleted"] is False
    consumable = backend.create_sku("Potion", type=3)
    grant = backend.grant_entitlement(consumable.id, user_id=123)
    router.dispatch(backend, "POST", f"{path}/{grant.id}/consume")
    assert router.dispatch(backend, "POST", f"{path}/{grant.id}/consume") is None
    assert sum(event == "ENTITLEMENT_UPDATE" for _, event, _ in backend.transcript) == 1


def test_wrong_kind_errors_are_real_http_backend_errors():
    backend = Backend()
    sku = backend.create_sku("Lifetime", type=2)
    grant = backend.seed_entitlement(sku.id, user_id=123)
    with pytest.raises(BackendError) as wrong_kind:
        router.dispatch(backend, "POST", f"{_path(backend)}/entitlements/{grant.id}/consume")
    assert (wrong_kind.value.status, wrong_kind.value.code) == (400, 40018)
