"""Immediate operator-only upgrades; real purchases never become cleanup ownership."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from capture_monetization_reference import select_user_subscription
from monetization_reference_checkout import (
    LIVE_TYPES,
    _baseline,
    _manual,
    _note,
    _rows,
    _stage,
    _subscription_reports,
    cancellation_verified,
    classify_upgrade,
    validate_purchase,
)
from monetization_reference_lifecycle import classify_temporal

PREFIX = "checkout.live_upgrade"


def _certain(ctx):
    if ctx.state.get("pending_write") or ctx.state.get("uncertain_writes"):
        raise RuntimeError(
            "Unresolved HTTP mutation: preserve its journal; no checkout or mutation may continue."
        )


def _manage(ctx, stage, row, sku):
    validate_purchase(row, ctx.app["id"], ctx.args.user, sku["id"], LIVE_TYPES)
    eid = str(row["id"])
    if eid in ctx.state.get("owned_entitlements", {}):
        raise RuntimeError("An API-owned test grant cannot be adopted as a real subscription.")
    stage.setdefault("managed_entitlements", {})[eid] = {"sku_id": str(sku["id"])}
    registry = ctx.state.setdefault("manual_live_entitlement_ids", [])
    if eid not in registry:
        registry.append(eid)
    stage["cancellation_required"] = True
    stage["cancellation_verified"] = False
    ctx.save()


def _linked(ctx, row, sku, reports):
    return [
        report
        for report in reports
        if report.get("user_id") == str(ctx.args.user)
        and str(sku["id"]) in report.get("sku_ids", [])
        and str(row["id"]) in report.get("entitlement_ids", [])
        and type(report.get("status")) is int
        and report["status"] in (0, 1, 2)
    ]


def _active_rows(ctx, rows, sku):
    active = []
    for row in rows:
        state = classify_temporal(row, datetime.now(UTC))
        if state in ("ended", "deleted"):
            continue
        validate_purchase(row, ctx.app["id"], ctx.args.user, sku["id"], LIVE_TYPES)
        if state != "active":
            raise RuntimeError(
                "An active/unknown A/B entitlement cannot be positively classified; no checkout authorized."
            )
        if str(row["id"]) in ctx.state.get("owned_entitlements", {}):
            raise RuntimeError("An existing sandbox grant conflicts with the real subscription capture.")
        active.append(row)
    return active


def _active(ctx, rows, sku):
    active = _active_rows(ctx, rows, sku)
    if len(active) > 1:
        raise RuntimeError("Multiple active entitlements prevent choosing a subscription safely.")
    return active[0] if active else None


async def _read(ctx, case, a, b):
    result = {}
    for key, sku in (("a", a), ("b", b)):
        result[key] = await _rows(ctx, case + f".{key}.entitlements", sku)
        result[key + "_subscriptions"] = await _subscription_reports(ctx, case + f".{key}.subscriptions", sku)
    return result


def _recover(ctx, stage, view, a, b):
    for key, sku, prompted in (("a", a, "prepare_a_prompted"), ("b", b, "upgrade_prompted")):
        if not stage.get(prompted):
            continue
        for row in view[key]:
            eid = str(row.get("id", ""))
            if eid in stage.get(key + "_baseline", []) or eid in ctx.state.get("owned_entitlements", {}):
                continue
            # A manual action authorizes tracking every matching new live ID, not deleting it.
            if (
                row.get("application_id") == str(ctx.app["id"])
                and row.get("user_id") == str(ctx.args.user)
                and row.get("guild_id") is None
                and row.get("sku_id") == str(sku["id"])
                and type(row.get("type")) is int
                and row["type"] in LIVE_TYPES
                and row.get("deleted") is False
            ):
                _manage(ctx, stage, row, sku)


async def _snapshot(ctx, case, stage, a, b):
    skus = {str(sku["id"]): sku for sku in (a, b)}
    for eid, item in stage.get("managed_entitlements", {}).items():
        await ctx.inspect(
            case + f".entitlement.{eid}", eid, skus[item["sku_id"]], owner_id=ctx.args.user, owner_type=2
        )
    await ctx.observe(case + ".events")
    _note(ctx, case, status="observed")


async def _interaction(ctx, stage, key):
    _certain(ctx)
    case = PREFIX + "." + key + ".interaction"
    complete = key + "_interaction_complete"
    if stage.get(complete):
        return
    if ctx.state["cases"].get(case, {}).get("status") != "observed":
        print(
            "PROBE ONLY: invoke the capture command. Do NOT repeat any checkout, resume, upgrade, or downgrade action.",
            flush=True,
        )
        await ctx.probe(case)
    stage[complete] = True
    ctx.save()


async def _settle(ctx, case, stage, a, b, select):
    # Bound read-only propagation checks; never repeat a UI action or HTTP mutation.
    seconds = max(0.0, getattr(ctx.args, "settle_seconds", 120.0))
    deadline = time.monotonic() + seconds
    print(
        f"READ-ONLY SETTLING: up to {seconds:g}s for entitlement/subscription linkage; no checkout or action retries.",
        flush=True,
    )
    attempt = 0
    while True:
        view = await _read(ctx, case + f".settle.{attempt}", a, b)
        _recover(ctx, stage, view, a, b)
        selected = select(view)
        remaining = deadline - time.monotonic()
        if selected is not None or remaining <= 0:
            return selected, view
        await asyncio.sleep(min(2.0, remaining))
        attempt += 1


def _ready_a(ctx, view, a, b):
    if _active(ctx, view["b"], b):
        raise RuntimeError("An active B conflicts with preparing A; no second subscription authorized.")
    ready = []
    for row in _active_rows(ctx, view["a"], a):
        reports = _linked(ctx, row, a, view["a_subscriptions"])
        if any(report["status"] == 0 and not report.get("canceled_at") for report in reports):
            ready.append(row)
        elif not any(cancellation_verified(report, ctx.args.user, a["id"], row["id"]) for report in reports):
            return None
    if len(ready) > 1:
        raise RuntimeError("Multiple renewing A subscriptions prevent choosing an upgrade safely.")
    return ready[0] if ready else None


def _replacement(ctx, stage, view, b):
    replacement = _active(ctx, view["b"], b)
    if (
        replacement is not None
        and str(replacement["id"]) not in stage["b_baseline"]
        and _linked(ctx, replacement, b, view["b_subscriptions"])
    ):
        return replacement
    return None


def _downgrade(ctx, stage, view, a, b):
    current = next((row for row in view["b"] if str(row.get("id")) == stage["b"]), None)
    if current is None:
        return None
    validate_purchase(current, ctx.app["id"], ctx.args.user, b["id"], LIVE_TYPES)
    if classify_temporal(current, datetime.now(UTC)) != "active":
        return None
    return next(
        (
            report
            for report in _linked(ctx, current, b, view["b_subscriptions"])
            if report.get("sku_ids") == [str(b["id"])]
            and report.get("renewal_sku_ids") == [str(a["id"])]
            and report["status"] == 0
            and not report.get("canceled_at")
        ),
        None,
    )


async def _cross_tier(ctx, stage, a, b):
    case = PREFIX + ".cross_tier_duplicate"
    probe = stage.setdefault("cross_tier_probe", {})
    if probe.get("done"):
        return
    _certain(ctx)
    receipt = ctx.state["cases"].get(case + ".create", {})
    if not receipt.get("mutation_complete"):
        if probe.get("attempted"):
            ctx.block(case, "The prior create attempt has no completed receipt; it will not be repeated.")
            return
        view = await _read(ctx, case + ".before", a, b)
        if _replacement(ctx, stage, view, b) is None:
            raise RuntimeError(
                "B is no longer positively active after the upgrade; no sandbox probe authorized."
            )
        probe["attempted"] = True
        ctx.save()
        await ctx.create(case + ".create", a, owner_id=ctx.args.user, owner_type=2)
        receipt = ctx.state["cases"].get(case + ".create", {})
    probe["http_status"] = receipt.get("http_status")
    eid = receipt.get("created_id")
    if eid:
        eid = str(eid)
        owned = ctx.state.get("owned_entitlements", {}).get(eid)
        if (
            not owned
            or not owned.get("create_ref")
            or owned.get("sku_id") != a["id"]
            or owned.get("owner_id") != str(ctx.args.user)
            or owned.get("owner_type") != 2
            or eid in stage.get("managed_entitlements", {})
            or eid in ctx.state.get("manual_live_entitlement_ids", [])
            or eid in stage["a_baseline"] + stage["b_baseline"]
        ):
            raise RuntimeError(
                "Sandbox response lacks exclusive positive create provenance; no deletion authorized."
            )
        probe["created_id"] = eid
        ctx.save()
        if not owned.get("deleted"):
            try:
                await ctx.inspect(case + ".accepted", eid, a, owner_id=ctx.args.user, owner_type=2)
                await ctx.observe(case + ".events")
            finally:
                _certain(ctx)
                status, _ = await ctx.remove(case + ".cleanup", eid)
                if status != 204 and not owned.get("deleted"):
                    raise RuntimeError("The API-created sandbox probe needs cleanup before continuing.")
        probe["outcome"] = "accepted"
    else:
        status = receipt.get("http_status")
        if not receipt.get("mutation_complete") or not isinstance(status, int) or 200 <= status < 300:
            raise RuntimeError("Sandbox probe has no verifiable completed outcome; it will not be repeated.")
        probe["outcome"] = "rejected"
        await ctx.observe(case + ".events")
    probe["done"] = True
    _note(
        ctx,
        case,
        status="observed",
        **probe,
        cross_tier_after_upgrade=True,
        isolated_natural_end_policy=False,
    )


def _canceled(ctx, stage, view, a, b):
    resolved = []
    for key, sku in (("a", a), ("b", b)):
        for eid, item in stage.get("managed_entitlements", {}).items():
            if item["sku_id"] != str(sku["id"]):
                continue
            row = next((row for row in view[key] if str(row.get("id")) == eid), None)
            ended = False
            try:
                validate_purchase(row, ctx.app["id"], ctx.args.user, sku["id"], LIVE_TYPES)
                ended = classify_temporal(row, datetime.now(UTC)) == "ended"
            except RuntimeError:
                pass
            canceled = any(
                cancellation_verified(report, ctx.args.user, sku["id"], eid)
                for report in view[key + "_subscriptions"]
            )
            resolved.append(ended or canceled)
    return True if resolved and all(resolved) else None


async def _cancel(ctx, stage, a, b):
    if not stage.get("cancellation_required") or stage.get("cancellation_verified"):
        return
    _certain(ctx)
    stage["cancellation_started"] = True
    ctx.save()
    print(
        "5. CANCELLATION REQUIRED: in Discord User Settings -> Subscriptions cancel EVERY real A/B subscription "
        "reused, resumed, or started in this capture, including replaced A and pending renewal to A. "
        "Do not delete entitlements or buy again. Then invoke the capture command.",
        flush=True,
    )
    await ctx.probe(PREFIX + ".cancellation.interaction")
    await ctx.observe(PREFIX + ".cancellation.events")
    verified, _ = await _settle(
        ctx, PREFIX + ".cancellation", stage, a, b, lambda value: _canceled(ctx, stage, value, a, b)
    )
    skus = {str(sku["id"]): sku for sku in (a, b)}
    for eid, item in stage.get("managed_entitlements", {}).items():
        await ctx.inspect(
            PREFIX + f".cancellation.entitlement.{eid}",
            eid,
            skus[item["sku_id"]],
            owner_id=ctx.args.user,
            owner_type=2,
        )
    stage["cancellation_verified"] = verified is True
    ctx.save()
    if stage["cancellation_verified"]:
        _note(
            ctx,
            PREFIX + ".cancellation",
            status="observed",
            cancellation_verified=True,
            managed_entitlement_ids=list(stage["managed_entitlements"]),
            remains_entitled_until_period_end=True,
        )
    else:
        ctx.block(
            PREFIX + ".cancellation",
            "UNRESOLVED: not every managed real subscription is positively ended/canceled. Resume cancellation only; never buy again.",
        )


async def capture_upgrade(ctx):
    """Capture A preparation, immediate upgrade, sandbox overlap, downgrade, and cancellation."""
    _certain(ctx)
    if not getattr(ctx.args, "team_discount_confirmed", False):
        ctx.block(PREFIX, "Explicit --team-discount-confirmed is required; no checkout authorized.")
        return
    a = select_user_subscription(ctx.skus, ctx.args.user_sku_a)
    b = select_user_subscription(ctx.skus, ctx.args.user_sku_b)
    if a["id"] == b["id"]:
        raise RuntimeError("Upgrade capture needs distinct user subscription A/B SKUs.")
    stage = _stage(ctx, "live_upgrade", [a, b])
    stage.setdefault("upgrade_observed", False)
    stage.setdefault("downgrade_observed", False)
    stage.setdefault("managed_entitlements", {})
    if stage.get("done") and stage.get("cancellation_verified"):
        return
    if stage.get("cancellation_started") or stage.get("cancellation_verified"):
        await _cancel(ctx, stage, a, b)
        stage["done"] = bool(stage.get("actions_complete") and stage.get("cancellation_verified"))
        ctx.save()
        return
    try:
        await _baseline(ctx, PREFIX, stage, a, "a_baseline")
        await _baseline(ctx, PREFIX, stage, b, "b_baseline")
        view = await _read(ctx, PREFIX + ".before", a, b)
        _recover(ctx, stage, view, a, b)
        for key in ("prepare_a_prompted", "upgrade_prompted", "downgrade_prompted"):
            if stage.get(key):
                await _interaction(ctx, stage, key)
        if not stage.get("upgrade_prompted") and not stage.get("upgrade_action_captured"):
            current = (
                next((row for row in view["a"] if str(row.get("id")) == stage.get("a")), None)
                if stage.get("prepare_a_prompted")
                else _active(ctx, view["a"], a)
            )
            if _active(ctx, view["b"], b):
                raise RuntimeError(
                    "A preexisting active B prevents choosing an upgrade safely; no checkout authorized."
                )
            if current:
                if not _linked(ctx, current, a, view["a_subscriptions"]):
                    raise RuntimeError(
                        "Active A has no authoritative subscription binding; no checkout authorized."
                    )
                _manage(ctx, stage, current, a)
                stage["a"] = str(current["id"])
                ctx.save()
            if not stage.get("baseline_interaction_complete"):
                await _snapshot(ctx, PREFIX + ".baseline", stage, a, b)
                print(
                    "1. BEFORE ANY CHECKOUT: keep Application Test Mode DISABLED and invoke the capture command. Leave guild SKUs unpublished.",
                    flush=True,
                )
                await _interaction(ctx, stage, "baseline")
            ready = _ready_a(ctx, view, a, b)
            if ready is None:
                if not stage.get("prepare_a_prompted"):
                    stage["cancellation_required"] = True
                    stage["cancellation_verified"] = False
                    ctx.save()
                    await _manual(
                        ctx,
                        PREFIX,
                        stage,
                        "prepare_a_prompted",
                        "2. PREPARE A NOW: Application Test Mode stays DISABLED. "
                        f"In Discord resume the existing {a['name']!r} subscription if available; otherwise start ONE real {a['name']!r} subscription. "
                        "A new/restart checkout is authorized ONLY with 100% team discount AND final total 0.00; STOP otherwise, never pay. "
                        "Type RESUMED only when no checkout occurred, or 0.00 only after the free checkout. Do not subscribe to B yet. "
                        "This is real and MUST be canceled after capture.",
                        zero=True,
                        allow_no_checkout=current is not None,
                    )
                    await _interaction(ctx, stage, "prepare_a_prompted")
                ready, view = await _settle(
                    ctx, PREFIX + ".a.prepare", stage, a, b, lambda value: _ready_a(ctx, value, a, b)
                )
                if ready is None:
                    raise RuntimeError(
                        "A did not settle to one active renewing linked subscription; do not resume or purchase again."
                    )
            _manage(ctx, stage, ready, a)
            stage["a"] = str(ready["id"])
            stage["a_initial"] = ready
            ctx.save()
            await _snapshot(ctx, PREFIX + ".a.before_upgrade", stage, a, b)
        if not stage.get("upgrade_action_captured"):
            if not stage.get("upgrade_prompted"):
                stage["cancellation_required"] = True
                stage["cancellation_verified"] = False
                ctx.save()
                await _manual(
                    ctx,
                    PREFIX,
                    stage,
                    "upgrade_prompted",
                    f"3. UPGRADE NOW: with Application Test Mode DISABLED, upgrade the existing {a['name']!r} subscription to higher-priced {b['name']!r}. "
                    "Do NOT start an unrelated second subscription or downgrade. Complete ONLY if THIS checkout independently shows "
                    "100% team discount AND final total 0.00. STOP otherwise; never pay. After completion type 0.00, then invoke the capture command. "
                    "Cancellation is required after capture.",
                    zero=True,
                )
                await _interaction(ctx, stage, "upgrade_prompted")
            replacement, view = await _settle(
                ctx, PREFIX + ".upgrade", stage, a, b, lambda value: _replacement(ctx, stage, value, b)
            )
            if replacement is None:
                raise RuntimeError(
                    "A new active subscription-bound B was not verified; no upgrade or natural-expiry claim."
                )
            _manage(ctx, stage, replacement, b)
            stage["b"] = str(replacement["id"])
            stage["b_initial"] = replacement
            previous = next((row for row in view["a"] if str(row.get("id")) == stage["a"]), None)
            classification = (
                classify_upgrade(stage["a_initial"], previous, replacement, datetime.now(UTC))
                if isinstance(previous, dict)
                else None
            )
            stage["a_after_upgrade"] = previous
            stage["upgrade_observed"] = classification is not None
            ctx.save()
            await _snapshot(ctx, PREFIX + ".after_upgrade", stage, a, b)
            stage["upgrade_action_captured"] = True
            _note(
                ctx,
                PREFIX,
                status="observed",
                classification=classification or "unmatched_upgrade_outcome",
                previous_entitlement_id=stage["a"],
                replacement_entitlement_id=stage["b"],
                natural_expiry=False,
                future_start=False,
                atomic_boundary=False,
                operator_zero_total_attestation=bool(stage.get("upgrade_prompted_zero_confirmed")),
                charge_independently_verified=False,
            )
        await _cross_tier(ctx, stage, a, b)
        if not stage["downgrade_observed"]:
            if not stage.get("downgrade_prompted"):
                _certain(ctx)
                view = await _read(ctx, PREFIX + ".scheduled_downgrade.before", a, b)
                if _replacement(ctx, stage, view, b) is None:
                    raise RuntimeError(
                        "B is not positively active and subscription-bound; no downgrade action authorized."
                    )
                await _manual(
                    ctx,
                    PREFIX,
                    stage,
                    "downgrade_prompted",
                    f"4. SCHEDULE DOWNGRADE NOW: change the current {b['name']!r} subscription to renew as {a['name']!r} at period end. "
                    "Do NOT make an immediate unrelated purchase or force renewal. STOP if any nonzero payment is requested; never pay. "
                    "If unavailable, leave it unchanged. Invoke the capture command after scheduling or stopping; cancellation follows.",
                )
                await _interaction(ctx, stage, "downgrade_prompted")
            report, view = await _settle(
                ctx,
                PREFIX + ".scheduled_downgrade",
                stage,
                a,
                b,
                lambda value: _downgrade(ctx, stage, value, a, b),
            )
            await _snapshot(ctx, PREFIX + ".scheduled_downgrade.after", stage, a, b)
            stage["downgrade_observed"] = report is not None
            ctx.save()
            if report:
                _note(
                    ctx,
                    PREFIX + ".scheduled_downgrade",
                    status="observed",
                    classification="scheduled_downgrade_only",
                    subscription_id=report["id"],
                    current_entitlement_id=stage["b"],
                    renewal_sku_ids=[str(a["id"])],
                    activation_observed=False,
                    future_start=False,
                    natural_expiry=False,
                )
            else:
                ctx.block(
                    PREFIX + ".scheduled_downgrade",
                    "No verified pending renewal to A with B still active; activation remains unobserved. Cancel instead of forcing a purchase.",
                )
        stage["actions_complete"] = True
        ctx.save()
    except RuntimeError as exc:
        _certain(ctx)
        if any(
            ctx.state["cases"].get(PREFIX + "." + key + ".interaction", {}).get("status") == "capture_failed"
            for key in ("prepare_a_prompted", "upgrade_prompted", "downgrade_prompted")
        ):
            raise
        ctx.block(PREFIX, str(exc))
        stage["actions_complete"] = True
        ctx.save()
    # Interruptions preserve the ledger; a begun cancellation resumes cancellation only.
    await _cancel(ctx, stage, a, b)
    stage["done"] = bool(stage.get("actions_complete") and stage.get("cancellation_verified"))
    ctx.save()
