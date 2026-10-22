"""Operator-only optional purchase captures; never publish or initiate checkout.

Manual IDs are evidence fixtures, never API-create cleanup ownership. Live
subscriptions renew until canceled; the cancellation obligation is journaled
before showing any checkout instruction.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import discord
from capture_monetization_reference import (
    select_new_test_purchase,
    select_purchase_sku,
    select_user_subscription,
)
from monetization_reference_lifecycle import classify_temporal

LIVE_TYPES = (discord.EntitlementType.purchase.value, discord.EntitlementType.application_subscription.value)


def validate_purchase(row, application_id, user_id, sku_id, kind, *, deleted=False):
    """Fail closed on identity, raw state, and unsupported entitlement kinds."""
    expected_types = kind if isinstance(kind, tuple) else (kind,)
    if (
        not isinstance(row, dict)
        or not str(row.get("id", "")).isdigit()
        or row.get("application_id") != str(application_id)
        or row.get("user_id") != str(user_id)
        or row.get("guild_id") is not None
        or row.get("sku_id") != str(sku_id)
        or type(row.get("type")) is not int
        or row["type"] not in expected_types
        or row.get("deleted") is not deleted
    ):
        raise RuntimeError(
            "Entitlement identity/type/deleted state is not positively verified; no mutation or checkout authorized."
        )
    if kind == 4 and "consumed" not in row:
        raise RuntimeError("Consumable GET omitted consumed; do not infer false.")
    return row


def select_new_purchase(rows, baseline, application_id, user_id, sku_id, kind):
    ids = [str(row.get("id", "")) for row in rows]
    if len(baseline) != len(set(baseline)) or len(ids) != len(set(ids)):
        raise RuntimeError("Duplicate baseline or response IDs prevent safe new-purchase selection.")
    # Callers supply a complete traversal, not a possibly truncated first page.
    new = [row for row in rows if str(row.get("id")) not in baseline]
    if kind == 4:
        row = select_new_test_purchase(new, baseline, {"id": str(sku_id)}, str(application_id), user_id)
    else:
        if len(new) != 1:
            raise RuntimeError("Need exactly one new live entitlement; do not purchase again.")
        row = new[0]
    validate_purchase(row, application_id, user_id, sku_id, kind)
    if kind == 4 and row.get("consumed") is not False:
        raise RuntimeError("Fresh test purchase must explicitly be unconsumed.")
    if kind != 4 and classify_temporal(row, datetime.now(UTC)) != "active":
        raise RuntimeError("New live entitlement is not positively active; do not purchase again.")
    return row


def classify_upgrade(before, ended, replacement, now):
    """Upgrade evidence is deliberately not a natural-expiry classification."""
    identity = ("id", "application_id", "user_id", "guild_id", "sku_id", "type")
    if (
        any(before.get(key) != ended.get(key) for key in identity)
        or type(before.get("type")) is not int
        or type(replacement.get("type")) is not int
        or type(ended.get("type")) is not int
        or before.get("type") not in LIVE_TYPES
        or replacement.get("type") not in LIVE_TYPES
        or replacement.get("application_id") != before.get("application_id")
        or replacement.get("user_id") != before.get("user_id")
        or replacement.get("guild_id") is not None
        or replacement.get("sku_id") == before.get("sku_id")
        or replacement.get("id") == before.get("id")
        or classify_temporal(before, now) != "active"
        or classify_temporal(ended, now) != "ended"
        or classify_temporal(replacement, now) != "active"
    ):
        return None
    return "ended_by_upgrade"


def cancellation_verified(row, user_id, sku_id, entitlement_id):
    return (
        isinstance(row, dict)
        and row.get("user_id") == str(user_id)
        and str(sku_id) in row.get("sku_ids", [])
        and str(entitlement_id) in row.get("entitlement_ids", [])
        and row.get("status") == 2
        and bool(row.get("canceled_at"))
    )


def _note(ctx, case, **fields):
    if fields.get("status") == "observed":
        for stream in ("requests", "rest", "gateway", "health", "interactions"):
            refs = [
                ref
                for name, item in ctx.state.get("cases", {}).items()
                if name.startswith(case + ".")
                for ref in item.get(stream, [])
            ]
            if refs:
                fields[stream] = list(dict.fromkeys(refs))
    item = ctx.state.setdefault("cases", {}).setdefault(case, {})
    if fields.get("status") == "observed":
        item.pop("reason", None)
    item.update(fields)
    ctx.save()


def _stage(ctx, name, skus):
    stage = ctx.state.setdefault("checkout", {}).setdefault(name, {})
    identity = {
        "application_id": str(ctx.app["id"]),
        "user_id": str(ctx.args.user),
        "sku_ids": [str(sku["id"]) for sku in skus],
    }
    if stage.get("identity", identity) != identity:
        raise RuntimeError(
            "Saved checkout app/user/SKUs differ; preserve original IDs and cancellation obligation."
        )
    stage["identity"] = identity
    ctx.save()
    return stage


async def _rows(ctx, case, sku):
    rows = await ctx.walk(
        case,
        query={
            "user_id": str(ctx.args.user),
            "sku_ids": str(sku["id"]),
            "exclude_deleted": "false",
            "exclude_ended": "false",
        },
    )
    if rows is None:
        raise RuntimeError("Purchase baseline/list is incomplete; do not buy or buy again.")
    ids = [str(row.get("id", "")) for row in rows]
    if any(not value.isdigit() for value in ids) or len(ids) != len(set(ids)):
        raise RuntimeError("Purchase baseline/list has missing or duplicate IDs.")
    _note(ctx, case, status="observed")
    return rows


async def _baseline(ctx, prefix, stage, sku, key):
    if key not in stage:
        rows = await _rows(ctx, prefix + "." + key, sku)
        stage[key] = [str(row["id"]) for row in rows]
        stage[key + "_rows"] = rows
        stage[key + "_refs"] = ctx.state["cases"][prefix + "." + key].get("rest", [])
        ctx.save()
    return stage[key]


async def _manual(ctx, prefix, stage, key, instructions, *, zero=False, allow_no_checkout=False):
    if not stage.get(key):
        # Persist before instructions: interrupted checkout must never be reissued.
        stage[key] = True
        ctx.save()
        print(instructions, flush=True)
    else:
        print(
            "RESUME: this checkout was already prompted. DO NOT purchase again; finish identification/cancellation only.",
            flush=True,
        )
    if zero and not (stage.get(key + "_zero_confirmed") or stage.get(key + "_resumed_without_checkout")):
        prompt = "Confirm 100% discount and final 0.00 (type 0.00)"
        if allow_no_checkout:
            prompt += ", or type RESUMED only if you resumed without a checkout"
        answer = (await asyncio.to_thread(input, prompt + "; otherwise STOP: ")).strip()
        if answer == "0.00":
            stage[key + "_zero_confirmed"] = True
        elif allow_no_checkout and answer == "RESUMED":
            stage[key + "_resumed_without_checkout"] = True
        else:
            raise RuntimeError(
                "Operator did not confirm a free checkout or permitted no-checkout resume. Cancel any subscription already started."
            )
        ctx.save()
    await ctx.probe(prefix + "." + key + ".interaction")


async def _select(ctx, prefix, stage, sku, key, baseline_key, kind):
    if key not in stage:
        rows = await _rows(ctx, prefix + "." + key + ".selection", sku)
        row = select_new_purchase(rows, stage[baseline_key], ctx.app["id"], ctx.args.user, sku["id"], kind)
        stage[key] = str(row["id"])
        ctx.save()
    status, row = await ctx.request(prefix + "." + key + ".verify", "GET", f"{ctx.base}/{stage[key]}")
    if status != 200:
        raise RuntimeError("Selected manual entitlement could not be re-read. Do not purchase again.")
    validate_purchase(row, ctx.app["id"], ctx.args.user, sku["id"], kind)
    if kind == 4 and row.get("consumed") is not False:
        raise RuntimeError("Selected fresh entitlement was already consumed; no deletion authorized.")
    stage.setdefault(key + "_initial", row)
    stage.setdefault(
        key + "_verification_ref", ctx.state["cases"][prefix + "." + key + ".verify"]["rest"][-1]
    )
    registry = ctx.state.setdefault(
        "manual_test_purchase_ids" if kind == 4 else "manual_live_entitlement_ids", []
    )
    if stage[key] not in registry:
        registry.append(stage[key])
    ctx.save()
    return row


async def _snapshot(ctx, prefix, eid, sku):
    row = await ctx.inspect(prefix + ".rest", eid, sku, owner_id=ctx.args.user, owner_type=2)
    await ctx.observe(prefix + ".events")
    await ctx.probe(prefix + ".interaction")
    return row


async def _deleted_purchase(ctx):
    prefix = "checkout.deleted_purchase"
    sku = select_purchase_sku(ctx.skus, ctx.args.consumable, discord.SKUType.consumable)
    stage = _stage(ctx, "deleted_purchase", [sku])
    if stage.get("done"):
        return
    await _baseline(ctx, prefix, stage, sku, "baseline_ids")
    if "purchase_initial" not in stage:
        await ctx.probe(prefix + ".baseline.interaction")
        await _manual(
            ctx,
            prefix,
            stage,
            "purchase_prompted",
            f"Operator only: if necessary publish consumable {sku['name']!r}. Enable Application Test Mode for app {ctx.app['id']}; verify its orange banner. BUY ONE ONLY of this consumable, do NOT consume, then reload and invoke the capture command. No live checkout.",
        )
        await _select(ctx, prefix, stage, sku, "purchase", "baseline_ids", 4)
    eid = stage["purchase"]
    if not stage.get("before_complete"):
        await _snapshot(ctx, prefix + ".before", eid, sku)
        stage["before_complete"] = True
        ctx.save()
    authorization = {
        "explicit_manual_test_purchase": True,
        "baseline_ids": stage["baseline_ids"],
        "baseline_refs": stage["baseline_ids_refs"],
        "selected_entitlement_id": eid,
        "initial_purchase": stage["purchase_initial"],
        "verification_ref": stage["purchase_verification_ref"],
    }
    for step, method, suffix in (
        ("delete", "DELETE", ""),
        ("consume", "POST", "/consume"),
        ("repeat", "POST", "/consume"),
    ):
        case = prefix + "." + step
        mutation = ctx.state["cases"].get(case, {})
        if not mutation.get("mutation_complete"):
            status, current = await ctx.request(case + ".verify", "GET", f"{ctx.base}/{eid}")
            if status != 200:
                raise RuntimeError("Manual fixture GET failed; no mutation attempted.")
            validate_purchase(current, ctx.app["id"], ctx.args.user, sku["id"], 4, deleted=step != "delete")
            if step == "delete" and current.get("consumed") is not False:
                raise RuntimeError("Delete-before-consume requires a positively unconsumed purchase.")
            if step == "consume" and current.get("consumed") is not False:
                raise RuntimeError("Deleted purchase is no longer unconsumed; no first consume authorized.")
            _note(
                ctx,
                case,
                **authorization,
                current_verification_ref=ctx.state["cases"][case + ".verify"]["rest"][-1],
            )
            await ctx.request(case, method, f"{ctx.base}/{eid}{suffix}")
        if not stage.get(step + "_capture_complete"):
            current = await _snapshot(ctx, case + ".after", eid, sku)
            stage[step + "_capture_complete"] = True
            ctx.save()
            if step == "delete":
                validate_purchase(current, ctx.app["id"], ctx.args.user, sku["id"], 4, deleted=True)
                if current.get("consumed") is not False:
                    raise RuntimeError("Deleted-unconsumed state not established; no consume authorized.")
    stage["done"] = True
    _note(
        ctx,
        prefix,
        status="observed",
        selected_entitlement_id=eid,
        classification="deleted_before_consume",
        temporal_expiry_claim=False,
    )


async def _subscription_reports(ctx, prefix, sku):
    status, rows = await ctx.request(
        prefix + ".list",
        "GET",
        f"/skus/{sku['id']}/subscriptions",
        query={"user_id": str(ctx.args.user), "limit": "100"},
    )
    if status != 200 or not isinstance(rows, list) or len(rows) >= 100:
        ctx.block(prefix, "Subscription reporting unavailable or incomplete; cancellation is NOT verified.")
        return []
    # List returns complete subscription objects and is authoritative evidence.
    # A separate detail lookup must not discard an already verified list row.
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("user_id") == str(ctx.args.user)
        and str(sku["id"]) in row.get("sku_ids", [])
        and str(row.get("id", "")).isdigit()
    ]


async def _cancel(ctx, prefix, stage, a, b):
    if not stage.get("cancellation_required") or stage.get("cancellation_verified"):
        return
    stage["cancellation_started"] = True
    ctx.save()
    print(
        "CANCELLATION REQUIRED: these are REAL renewing subscriptions. In Discord User Settings -> Subscriptions cancel every A/B subscription started in this capture, including A if the upgrade failed. Do not delete entitlements. Then invoke the capture command to acknowledge.",
        flush=True,
    )
    await ctx.probe(prefix + ".cancellation.interaction")
    await ctx.observe(prefix + ".cancellation.events")
    resolved = []
    for key, sku, baseline_key in (("a", a, "a_baseline"), ("b", b, "b_baseline")):
        if not stage.get(key):
            rows = await _rows(ctx, prefix + f".cancellation.recover.{key}", sku)
            candidates = [
                row
                for row in rows
                if row.get("id") not in stage.get(baseline_key, [])
                and type(row.get("type")) is int
                and row["type"] in LIVE_TYPES
                and row.get("application_id") == str(ctx.app["id"])
                and row.get("user_id") == str(ctx.args.user)
                and row.get("sku_id") == sku["id"]
                and row.get("guild_id") is None
            ]
            if len(candidates) == 1:
                stage[key] = candidates[0]["id"]
                registry = ctx.state.setdefault("manual_live_entitlement_ids", [])
                if stage[key] not in registry:
                    registry.append(stage[key])
                ctx.save()
        eid = stage.get(key)
        if not eid:
            if key == "a" or stage.get("upgrade_prompted"):
                resolved.append(False)
            continue
        status, row = await ctx.request(
            prefix + f".cancellation.entitlement.{key}", "GET", f"{ctx.base}/{eid}"
        )
        ended = (
            status == 200
            and isinstance(row, dict)
            and row.get("id") == eid
            and row.get("application_id") == str(ctx.app["id"])
            and row.get("user_id") == str(ctx.args.user)
            and row.get("sku_id") == sku["id"]
            and type(row.get("type")) is int
            and row["type"] in LIVE_TYPES
            and classify_temporal(row, datetime.now(UTC)) == "ended"
        )
        reports = await _subscription_reports(ctx, prefix + f".cancellation.{key}", sku)
        canceled = any(cancellation_verified(report, ctx.args.user, sku["id"], eid) for report in reports)
        resolved.append(ended or canceled)
    stage["cancellation_verified"] = bool(resolved) and all(resolved)
    ctx.save()
    if not stage["cancellation_verified"]:
        ctx.block(
            prefix + ".cancellation",
            "UNRESOLVED: cancel all new real subscriptions in Discord; not every started subscription is verified ended/canceled. Resume without buying again.",
        )
    else:
        _note(
            ctx,
            prefix + ".cancellation",
            status="observed",
            cancellation_verified=True,
            remains_entitled_until_period_end=True,
        )


async def _live_upgrade(ctx):
    prefix = "checkout.live_upgrade"
    a = select_user_subscription(ctx.skus, ctx.args.user_sku_a)
    b = select_user_subscription(ctx.skus, ctx.args.user_sku_b)
    if a["id"] == b["id"]:
        raise RuntimeError("Live upgrade requires distinct user A/B SKUs.")
    stage = _stage(ctx, "live_upgrade", [a, b])
    if stage.get("done") and stage.get("cancellation_verified"):
        return
    if stage.get("cancellation_verified"):
        ctx.block(
            prefix,
            "Prior interrupted checkout was canceled; no new purchase or upgrade authorized in this stage.",
        )
        return
    if stage.get("cancellation_started"):
        await _cancel(ctx, prefix, stage, a, b)
        if stage.get("cancellation_verified") and not stage.get("done"):
            ctx.block(
                prefix,
                "The interrupted subscription was canceled. Upgrade evidence remains unavailable; no new purchase or upgrade is authorized.",
            )
        return
    try:
        if not stage.get("done"):
            await _baseline(ctx, prefix, stage, a, "a_baseline")
            await _baseline(ctx, prefix, stage, b, "b_baseline")
            if not stage.get("subscribe_prompted"):
                rows = stage["a_baseline_rows"] + stage["b_baseline_rows"]
                if any(classify_temporal(row, datetime.now(UTC)) not in ("ended", "deleted") for row in rows):
                    raise RuntimeError(
                        "Preexisting active/unknown user A/B entitlement prevents a clean live upgrade; no checkout authorized."
                    )
                await ctx.probe(prefix + ".baseline.interaction")
                stage["cancellation_required"] = True
                stage["cancellation_verified"] = False
                ctx.save()
            if "a_initial" not in stage:
                await _manual(
                    ctx,
                    prefix,
                    stage,
                    "subscribe_prompted",
                    "MANUAL CHECKOUT: create ONE real user subscription now, not before this prompt.\n"
                    "1. In Discord User Settings -> Advanced, DISABLE Application Test Mode; reload and verify the orange banner is gone.\n"
                    "2. Use your app-team account. Open this app's Store (the same Store used for the consumable).\n"
                    f"3. If absent, first publish USER {a['name']!r} and {b['name']!r} to Store, B at a higher price. Leave GUILD SKUs unpublished. Publication exposes offers to other users; stop if not authorized.\n"
                    f"4. Select {a['name']!r} and Subscribe. Complete ONLY if checkout shows 100% team discount AND final total 0.00. Otherwise STOP; never pay.\n"
                    "5. Only AFTER completing that zero-total checkout, type 0.00 here; then follow the command prompt.\n"
                    "This is REAL and renews until CANCELED. Do not subscribe to B yet.",
                    zero=True,
                )
                await _select(ctx, prefix, stage, a, "a", "a_baseline", LIVE_TYPES)
            if not stage.get("a_capture_complete"):
                await _snapshot(ctx, prefix + ".a.before_upgrade", stage["a"], a)
                await _subscription_reports(ctx, prefix + ".a.subscription", a)
                stage["a_capture_complete"] = True
                ctx.save()
            if "b_initial" not in stage:
                if not stage.get("upgrade_prompted"):
                    status, current = await ctx.request(
                        prefix + ".a.pre_upgrade", "GET", f"{ctx.base}/{stage['a']}"
                    )
                    if status != 200:
                        raise RuntimeError("Cannot verify A before upgrade; cancel it instead.")
                    validate_purchase(current, ctx.app["id"], ctx.args.user, a["id"], LIVE_TYPES)
                    if classify_temporal(current, datetime.now(UTC)) != "active":
                        raise RuntimeError("A is not active; do not start another subscription.")
                await _manual(
                    ctx,
                    prefix,
                    stage,
                    "upgrade_prompted",
                    "MANUAL UPGRADE: Application Test Mode stays DISABLED.\n"
                    f"1. Return to this app's Store and select higher-priced USER {b['name']!r}.\n"
                    f"2. UPGRADE the existing {a['name']!r} subscription. Do NOT create an unrelated second subscription or downgrade.\n"
                    "3. Verify this checkout independently shows 100% team discount AND final total 0.00. Stop otherwise; never pay.\n"
                    "4. After completing the zero-total upgrade, type 0.00 here; then follow the command prompts.\n"
                    "You MUST cancel the real subscription after capture; wait for the cancellation instructions.",
                    zero=True,
                )
                await _select(ctx, prefix, stage, b, "b", "b_baseline", LIVE_TYPES)
            ended = await _snapshot(ctx, prefix + ".a.after_upgrade", stage["a"], a)
            replacement = await _snapshot(ctx, prefix + ".b.after_upgrade", stage["b"], b)
            await _subscription_reports(ctx, prefix + ".b.subscription", b)
            if getattr(ctx.args, "other_user", None):
                await ctx.probe(prefix + ".other_user.interaction", actor_id=ctx.args.other_user)
            if not isinstance(ended, dict) or not isinstance(replacement, dict):
                raise RuntimeError("Upgrade endpoints not readable; no transition claimed.")
            classification = classify_upgrade(stage["a_initial"], ended, replacement, datetime.now(UTC))
            if not classification:
                raise RuntimeError(
                    "A-ended/nondeleted plus B-active upgrade transition not verified; not natural expiry."
                )
            stage["done"] = True
            _note(
                ctx,
                prefix,
                status="observed",
                classification=classification,
                previous_entitlement_id=stage["a"],
                replacement_entitlement_id=stage["b"],
                natural_expiry=False,
                future_start=False,
                atomic_boundary=False,
                operator_zero_total_attestation=True,
                charge_independently_verified=False,
            )
    except RuntimeError as exc:
        ctx.block(prefix, str(exc))
    # Interruptions intentionally propagate; the persisted obligation survives.
    await _cancel(ctx, prefix, stage, a, b)


async def capture_checkouts(ctx):
    """No flags means no prompts or IO; attestations gate independent stages."""
    if getattr(ctx.args, "mode", None) == "upgrade":
        from monetization_reference_upgrade import capture_upgrade

        if not getattr(ctx.args, "team_discount_confirmed", False):
            ctx.block("checkout.live_upgrade", "--team-discount-confirmed is required; no checkout prompted.")
            return
        await capture_upgrade(ctx)
        return
    for flag, attestation, function in (
        ("deleted_purchase", "test_mode_confirmed", _deleted_purchase),
        ("live_upgrade", "team_discount_confirmed", _live_upgrade),
    ):
        if not getattr(ctx.args, flag, False):
            continue
        prefix = "checkout." + flag
        if not getattr(ctx.args, attestation, False):
            ctx.block(
                prefix,
                "Explicit --"
                + attestation.replace("_", "-")
                + " attestation required; no checkout prompted.",
            )
            continue
        try:
            await function(ctx)
        except RuntimeError as exc:
            ctx.block(prefix, str(exc))
            if ctx.state.get("pending_write") or ctx.state.get("uncertain_writes"):
                raise
    deleted = ctx.state.get("checkout", {}).get("deleted_purchase", {})
    proof = ctx.state.get("cases", {}).get("checkout.deleted_purchase", {})
    if deleted.get("done") and proof.get("status") == "observed":
        ctx.state["cases"]["lifecycle.consume_deleted"] = {**proof, "initial_consumed": False}
        ctx.save()
