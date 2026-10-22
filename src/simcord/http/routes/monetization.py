"""Application SKU and entitlement REST projections."""

from __future__ import annotations

from typing import Any

from ...backend import errors, serializers
from ..router import RequestContext, route


def _application(ctx: RequestContext) -> None:
    if ctx.int_arg("application_id") != ctx.backend.application_id:
        raise errors.BackendError(404, 10002, "Unknown Application")


def _integer(value: Any, name: str) -> int:
    try:
        if isinstance(value, bool) or isinstance(value, float):
            raise ValueError
        return int(value)
    except (ValueError, TypeError):
        raise errors.invalid_form_body(f"{name} must be an integer") from None


def _boolean(value: Any, name: str) -> bool:
    if value in (True, 1, "true", "1"):
        return True
    if value in (False, 0, "false", "0"):
        return False
    raise errors.invalid_form_body(f"{name} must be a boolean")


@route("GET", "/applications/{application_id}/skus")
def list_skus(ctx: RequestContext) -> Any:
    _application(ctx)
    return [serializers.sku_payload(sku) for sku in ctx.backend.skus.values()]


@route("GET", "/applications/{application_id}/entitlements")
def list_entitlements(ctx: RequestContext) -> Any:
    _application(ctx)
    params = ctx.params
    limit = _integer(params.get("limit", 100), "limit")
    if not 1 <= limit <= 100:
        raise errors.invalid_form_body("limit must be between 1 and 100")
    ids = {
        key: _integer(params[key], key) for key in ("user_id", "guild_id", "before", "after") if key in params
    }
    if "before" in ids and "after" in ids:
        raise errors.invalid_form_body("before and after are mutually exclusive")
    sku_ids = None
    if "sku_ids" in params:
        sku_ids = {_integer(value, "sku_ids") for value in str(params["sku_ids"]).split(",")}
    exclude_ended = _boolean(params.get("exclude_ended", False), "exclude_ended")
    exclude_deleted = _boolean(params.get("exclude_deleted", True), "exclude_deleted")
    now = ctx.backend.clock.now()
    rows = [
        entitlement
        for entitlement in ctx.backend.entitlements.values()
        if ("user_id" not in ids or entitlement.user_id == ids["user_id"])
        and ("guild_id" not in ids or entitlement.guild_id == ids["guild_id"])
        and (sku_ids is None or entitlement.sku_id in sku_ids)
        and ("before" not in ids or entitlement.id < ids["before"])
        and ("after" not in ids or entitlement.id > ids["after"])
        and (not exclude_deleted or not entitlement.deleted)
        and (not exclude_ended or entitlement.ends_at is None or entitlement.ends_at > now)
    ]
    rows.sort(key=lambda entitlement: entitlement.id, reverse="after" not in ids)
    rows = rows[:limit]
    return [serializers.entitlement_payload(entitlement) for entitlement in rows]


@route("GET", "/applications/{application_id}/entitlements/{entitlement_id}")
def get_entitlement(ctx: RequestContext) -> Any:
    _application(ctx)
    return serializers.entitlement_payload(ctx.backend.get_entitlement(ctx.int_arg("entitlement_id")))


@route("POST", "/applications/{application_id}/entitlements")
def create_test_entitlement(ctx: RequestContext) -> Any:
    _application(ctx)
    body = ctx.fields("sku_id", "owner_id", "owner_type")
    missing = {"sku_id", "owner_id", "owner_type"} - body.keys()
    if missing:
        raise errors.invalid_form_body(f"required fields: {', '.join(sorted(missing))}")
    entitlement = ctx.backend.create_test_entitlement(
        _integer(body["sku_id"], "sku_id"),
        body["owner_id"],
        body["owner_type"],
    )
    return serializers.entitlement_payload(entitlement)


@route("DELETE", "/applications/{application_id}/entitlements/{entitlement_id}")
def delete_test_entitlement(ctx: RequestContext) -> None:
    _application(ctx)
    ctx.fields()
    ctx.backend.delete_test_entitlement(ctx.int_arg("entitlement_id"))


@route("POST", "/applications/{application_id}/entitlements/{entitlement_id}/consume")
def consume_entitlement(ctx: RequestContext) -> None:
    _application(ctx)
    ctx.fields()
    ctx.backend.consume_entitlement(ctx.int_arg("entitlement_id"))
