"""Authorized guild and real-time lifecycle capture using the runner's ctx interface.

Only ctx.create grants are removed. Watch IDs are read-only; consume-history IDs
explicitly authorize consumption, never deletion. All projections use raw ctx IO.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import discord


def _instant(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(UTC) if result.tzinfo else None


def classify_temporal(row, now):
    """Classify REST fields, not interaction eligibility or atomic server timing."""
    if not isinstance(row, dict):
        return "unknown"
    if row.get("deleted") is True:
        return "deleted"
    if row.get("deleted") is not False:
        return "unknown"
    if "starts_at" not in row or "ends_at" not in row:
        return "unknown"
    start, end = _instant(row["starts_at"]), _instant(row["ends_at"])
    if (
        (row["starts_at"] is not None and start is None)
        or (row["ends_at"] is not None and end is None)
        or (start is not None and end is not None and start > end)
    ):
        return "unknown"
    if end is not None and now >= end:
        return "ended"
    if start is not None and now < start:
        return "future"
    return "active"


def classify_transition(before, after, before_time, after_time):
    """Recognize activation and expiry, including Discord publishing ends_at on expiry."""
    if after_time < before_time:
        return None
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    identity = ("id", "application_id", "sku_id", "type", "user_id", "guild_id")
    if any(before.get(key) != after.get(key) for key in identity):
        return None
    if before.get("starts_at") != after.get("starts_at"):
        return None
    states = classify_temporal(before, before_time), classify_temporal(after, after_time)
    end = _instant(after.get("ends_at"))
    unchanged_end = before.get("ends_at") == after.get("ends_at")
    published_end = before.get("ends_at") is None and end is not None and before_time < end <= after_time
    if states == ("active", "ended") and (unchanged_end or published_end):
        return "natural_end"
    if states == ("future", "active") and unchanged_end:
        return "natural_start"
    return None


def validate_fixture(row, application_id, sku, user_id, guild_id, *, consume=False):
    """Return a blocker before any mutation of an explicitly supplied fixture."""
    if not isinstance(row, dict) or not row.get("id"):
        return "GET did not return a verifiable entitlement"
    if str(row.get("application_id")) != str(application_id):
        return "entitlement application does not match the authenticated application"
    if not sku or str(row.get("sku_id")) != str(sku.get("id")):
        return "entitlement SKU is not in the authenticated application's catalog"
    if type(row.get("type")) is not int or row["type"] not in (1, 4, 8):
        return "unsupported entitlement type for a lifecycle fixture"
    # Guild grants can name a purchaser as well as their actual guild owner.
    if row.get("guild_id") is not None:
        if str(row["guild_id"]) != str(guild_id):
            return "entitlement guild owner is not the authorized guild"
    elif str(row.get("user_id")) != str(user_id):
        return "entitlement user owner is not the authorized user"
    if consume and (row.get("type") != 4 or sku.get("type") != 3):
        return "consumption requires a type-4 entitlement for a consumable SKU"
    return None


def _note(ctx, case, **fields):
    if fields.get("status") == "observed":
        for stream in ("rest", "requests", "interactions", "gateway", "health"):
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


def _done(ctx, section):
    ctx.state.setdefault("sections", {})[section] = {"done": True}
    ctx.save()


def _complete(ctx, section):
    value = ctx.state.setdefault("sections", {}).get(section, {})
    return isinstance(value, dict) and value.get("done") is True


def _sku(ctx, row):
    return next((sku for sku in ctx.skus if str(sku.get("id")) == str(row.get("sku_id"))), None)


def _owner(row):
    return (str(row["guild_id"]), 1) if row.get("guild_id") is not None else (str(row["user_id"]), 2)


async def _inspect(ctx, case, row, sku):
    owner_id, owner_type = _owner(row)
    return await ctx.inspect(case, str(row["id"]), sku, owner_id=owner_id, owner_type=owner_type)


async def _cleanup(ctx, case, entitlement_id):
    if ctx.state.get("pending_write"):
        ctx.block(
            case,
            "An unresolved mutation prevents automatic cleanup; retain the owned provenance for recovery.",
        )
        return
    owned = ctx.state.get("owned_entitlements", {}).get(entitlement_id)
    if owned and not owned.get("deleted"):
        # ctx.remove additionally enforces the core's positive create provenance.
        await ctx.remove(case, entitlement_id)


async def capture_guild(ctx):
    """Capture one new guild sandbox grant, without changing offering scope."""
    if _complete(ctx, "guild"):
        return
    skus = [
        sku
        for sku in ctx.subscriptions
        if sku.get("type") == 5 and int(sku.get("flags", 0)) & discord.SKUFlags.guild_subscription.flag
    ]
    if ctx.owner_type != 1 or not skus:
        ctx.block(
            "guild.prerequisite",
            "Authenticated offering is user-scoped; a guild-subscription SKU is required. No offering was changed.",
        )
        _done(ctx, "guild")
        return
    sku = skus[0]
    owner = str(ctx.args.guild)
    other = getattr(ctx.args, "other_user", None)
    if not other:
        ctx.block(
            "guild.second_member",
            "A second consenting authorized guild member (--other-user) is unavailable.",
        )
    if not getattr(ctx.args, "other_guild", None):
        ctx.block(
            "guild.other_guild", "A second authorized guild is required to establish cross-guild isolation."
        )

    async def projections(stage):
        for guild_id in (ctx.args.guild, getattr(ctx.args, "other_guild", None)):
            if not guild_id:
                continue
            suffix = "" if guild_id == ctx.args.guild else ".other_guild"
            await ctx.probe(f"guild.{stage}{suffix}.interaction", guild_id=guild_id)
            if other:
                await ctx.probe(f"guild.{stage}{suffix}.second_member", actor_id=other, guild_id=guild_id)

    section = ctx.state["sections"].setdefault("guild", {})
    if section.get("entitlement_id"):
        await _cleanup(ctx, "guild.resume_cleanup", section["entitlement_id"])
        section.pop("entitlement_id")
        ctx.save()
    query = {"guild_id": owner, "sku_ids": str(sku["id"]), "limit": "100", "exclude_ended": "true"}
    rows = await ctx.list_rows("guild.baseline.rest", query=query)
    if rows is None or len(rows) >= 100 or rows:
        ctx.block(
            "guild.prerequisite",
            "Guild SKU baseline is unavailable, full, or contains preexisting grants; no grant was mutated.",
        )
        _done(ctx, "guild")
        return
    await projections("baseline")
    entitlement_id = await ctx.create("guild.create", sku, owner_id=owner, owner_type=1)
    if not entitlement_id:
        ctx.block("guild.projections", "Guild test-create was rejected; no new owned grant is available.")
        _done(ctx, "guild")
        return
    section["entitlement_id"] = entitlement_id
    ctx.save()
    try:
        await ctx.inspect("guild.during", entitlement_id, sku, owner_id=owner, owner_type=1)
        await ctx.observe("guild.create.window")
        await projections("during")
        await ctx.remove("guild.delete", entitlement_id)
        await ctx.inspect("guild.after", entitlement_id, sku, owner_id=owner, owner_type=1)
        await ctx.observe("guild.delete.window")
        await projections("after")
    finally:
        await _cleanup(ctx, "guild.cleanup", entitlement_id)
    _done(ctx, "guild")


async def _watch(ctx, entitlement_id):
    prefix = f"lifecycle.watch.{entitlement_id}"
    baselines = ctx.state.setdefault("watch_baselines", {})
    if baselines.get(entitlement_id, {}).get("transition_complete"):
        return baselines[entitlement_id]["transition"]
    expiry = ctx.state.get("expiry_source")
    status, row = await ctx.request(prefix + ".verify", "GET", f"{ctx.base}/{entitlement_id}")
    sku = _sku(ctx, row) if isinstance(row, dict) else None
    reason = validate_fixture(row, ctx.app["id"], sku, ctx.args.user, ctx.args.guild)
    if (
        not 200 <= status < 300
        or not isinstance(row, dict)
        or sku is None
        or str(row.get("id")) != entitlement_id
        or reason
    ):
        ctx.block(prefix, reason or "GET did not verify the requested entitlement ID")
        return
    resumed = entitlement_id in baselines
    if not resumed:
        temporal = classify_temporal(row, datetime.now(UTC))
        if temporal == "ended":
            await _inspect(ctx, prefix + ".ended_at_entry", row, sku)
            await ctx.probe(prefix + ".ended_at_entry.interaction")
            await ctx.observe(prefix + ".ended_at_entry.window")
            _note(
                ctx,
                prefix,
                status="observed",
                transition="ended_at_entry",
                limitation="Legitimate ended fixture; no before-expiry interaction or transition event captured.",
            )
            if sku.get("type") == 5:
                await _duplicate_after_end(ctx, prefix, row, sku)
            return "ended_at_entry"
        if temporal not in ("future", "active"):
            ctx.block(
                prefix, "Fixture is deleted or its dates are unverifiable; no natural lifecycle inferred."
            )
            return
    now = datetime.now(UTC)
    if not resumed:
        await _inspect(ctx, prefix + ".before", row, sku)
        baseline = {"row": row, "utc": now.isoformat(), "before_probe_complete": False}
        baselines[entitlement_id] = baseline
        ctx.save()
        await ctx.probe(prefix + ".before.interaction")
        baseline["before_probe_complete"] = True
        baseline["probe_completed_utc"] = datetime.now(UTC).isoformat()
        ctx.save()
        await ctx.observe(prefix + ".before.window")
    baseline = baselines[entitlement_id]
    before_time = _instant(baseline.get("utc"))
    if before_time is None:
        ctx.block(prefix, "Saved watch baseline timestamp is invalid; it was not overwritten.")
        return
    if resumed:
        ref = ctx.evidence.record(
            "health",
            event="watch_resumed",
            entitlement_id=entitlement_id,
            limitation="Offline gap; no continuous gateway/no-event claim across resume.",
        )
        _note(ctx, prefix, health=[ref], resumed_with_gap=True)
    deadline = time.monotonic() + max(0, ctx.args.watch_seconds)
    while True:
        if ctx.client.capture_failed.is_set() or (ctx.gateway is not None and ctx.gateway.done()):
            raise RuntimeError("Gateway capture stopped; preserve this directory and resume, not checkout.")
        if expiry:
            from monetization_reference_expiry import check_expiry_subscription

            if not await check_expiry_subscription(ctx, prefix + ".subscription.poll"):
                return
            ctx.evidence.record(
                "health",
                event="expiry_poll",
                connected=ctx.client.is_ready(),
                connection_epoch=ctx.client.connection_epoch,
                sequence=ctx.client.last_sequence,
                health_errors=ctx.client.health_errors,
            )
        status, row = await ctx.request(prefix + ".poll", "GET", f"{ctx.base}/{entitlement_id}")
        if status != 200 or not isinstance(row, dict):
            ctx.block(
                prefix, "Polled entitlement is unavailable; disappearance is not proof of natural expiry."
            )
            return
        sample_time = datetime.now(UTC)
        reason = validate_fixture(row, ctx.app["id"], sku, ctx.args.user, ctx.args.guild)
        if reason or str(row.get("id")) != entitlement_id:
            ctx.block(prefix, reason or "Polled response changed entitlement identity")
            return
        transition = classify_transition(baseline["row"], row, before_time, sample_time)
        if transition:
            classification = transition
            if expiry and not ctx.state.get("expiry_billing_verified"):
                classification = "end_with_unverified_billing"
            if expiry and transition == "natural_end":
                observed_end, expected_end = _instant(row["ends_at"]), _instant(expiry["period_end"])
                if observed_end is None or expected_end is None or observed_end < expected_end:
                    ctx.block(
                        prefix + ".billing_boundary",
                        "Entitlement ended before its recorded billing boundary; post-end evidence is captured but not classified as natural billing expiry.",
                    )
                    classification = "ended_before_billing_boundary"
            await _inspect(ctx, prefix + ".after", row, sku)
            boundary = _instant(
                row.get("ends_at") if transition == "natural_end" else baseline["row"].get("starts_at")
            )
            probe_time = _instant(baseline.get("probe_completed_utc"))
            if probe_time is None or boundary is None or probe_time >= boundary:
                ctx.block(
                    prefix + ".before_projection",
                    "Before interaction was not captured strictly before the temporal boundary; only REST transition evidence is available.",
                )
            after_case = prefix + ".after.interaction"
            if ctx.state["cases"].get(after_case, {}).get("status") != "observed":
                print(
                    "EXPIRY OBSERVED: reload Discord and run the probe once when prompted. No checkout is needed.",
                    flush=True,
                )
                await ctx.probe(after_case)
            await ctx.observe(prefix + ".transition.window")
            _note(
                ctx,
                prefix,
                status="observed",
                transition=classification,
                before_utc=baseline["utc"],
                after_utc=sample_time.isoformat(),
                before_probe_complete=baseline.get("before_probe_complete", False),
                reused_before_source=baseline.get("source_bundle"),
                reused_before_interaction=baseline.get("source_interaction_ref"),
                limitation="REST timestamps bracket a temporal transition; not an atomic eligibility boundary. Gateway absence applies only to connected observation windows.",
            )
            if transition == "natural_end" and sku.get("type") == 5:
                await _duplicate_after_end(ctx, prefix, row, sku)
            healthy = not any(
                name.startswith(prefix + ".") and item.get("status") == "capture_failed"
                for name, item in ctx.state["cases"].items()
            )
            baseline.update(
                transition_complete=healthy and classification in ("natural_end", "natural_start"),
                transition=classification,
            )
            ctx.save()
            return classification
        if classify_temporal(row, sample_time) in ("deleted", "unknown"):
            ctx.block(
                prefix,
                "Entitlement is deleted or its temporal state is unverifiable; deletion is not natural expiry.",
            )
            return
        if time.monotonic() >= deadline:
            await _inspect(ctx, prefix + ".timeout", row, sku)
            if not expiry:
                await ctx.probe(prefix + ".timeout.interaction")
            await ctx.observe(prefix + ".timeout.window")
            ctx.block(
                prefix,
                "No unchanged-schedule natural start/end transition was established within the real polling window; ended-at-entry or rewritten/cancelled schedules do not prove expiry.",
            )
            return
        await asyncio.sleep(min(max(0.1, ctx.args.poll_seconds), max(0, deadline - time.monotonic())))


async def _duplicate_after_end(ctx, prefix, row, sku):
    owner_id, owner_type = _owner(row)
    case = prefix + ".duplicate_after_natural_end"
    if not ctx.state.get("expiry_source"):
        await ctx.probe(case + ".before.interaction")
    previous = ctx.state.get("cases", {}).get(case, {})
    if previous.get("mutation_complete"):
        created = previous.get("created_id")
    else:
        if ctx.state.get("expiry_source"):
            status, current = await ctx.request(case + ".reverify", "GET", f"{ctx.base}/{row['id']}")
            if (
                status != 200
                or validate_fixture(current, ctx.app["id"], sku, ctx.args.user, ctx.args.guild)
                or any(
                    current.get(key) != row.get(key)
                    for key in ("application_id", "user_id", "guild_id", "sku_id", "type", "starts_at")
                )
                or current.get("id") != row["id"]
                or classify_temporal(current, datetime.now(UTC)) != "ended"
            ):
                ctx.block(
                    case, "Original entitlement is no longer GET-verified ended; no test grant attempted."
                )
                return
            active = await ctx.walk(
                case + ".owner_active",
                {
                    **ctx.owner_query(owner_id, owner_type),
                    "sku_ids": sku["id"],
                    "exclude_deleted": "true",
                    "exclude_ended": "true",
                },
            )
            if active is None or any(
                classify_temporal(item, datetime.now(UTC)) != "ended" for item in active
            ):
                ctx.block(
                    case,
                    "Another active or unverifiable owner/SKU grant would confound the after-expiry probe; no create attempted.",
                )
                return
        created = await ctx.create(case, sku, owner_id=owner_id, owner_type=owner_type)
        _note(ctx, case, mutation_complete=True, created_id=created)
    try:
        if created:
            await ctx.inspect(case + ".created", created, sku, owner_id=owner_id, owner_type=owner_type)
        await _inspect(ctx, case + ".original", row, sku)
        if not ctx.state.get("expiry_source"):
            await ctx.probe(case + ".after.interaction")
        await ctx.observe(case + ".window")
    finally:
        if created:
            await _cleanup(ctx, case + ".cleanup", created)
    _note(ctx, case, status="observed", mutation_complete=True, created_id=created)


async def _consume(ctx, entitlement_id):
    prefix = f"lifecycle.consume.{entitlement_id}"
    status, row = await ctx.request(prefix + ".verify", "GET", f"{ctx.base}/{entitlement_id}")
    sku = _sku(ctx, row) if isinstance(row, dict) else None
    reason = validate_fixture(row, ctx.app["id"], sku, ctx.args.user, ctx.args.guild, consume=True)
    if not 200 <= status < 300 or not isinstance(row, dict) or str(row.get("id")) != entitlement_id or reason:
        ctx.block(prefix, reason or "GET did not verify the requested historical entitlement")
        return
    initial_state = classify_temporal(row, datetime.now(UTC))
    if initial_state not in ("ended", "deleted"):
        ctx.block(
            prefix,
            "Explicit consume authorization does not permit a live or temporally unverifiable purchase.",
        )
        return
    await _inspect(ctx, prefix + ".before", row, sku)
    await ctx.probe(prefix + ".before.interaction")
    await ctx.observe(prefix + ".before.window")
    for suffix in ("first", "repeat"):
        # Fresh validation immediately before each mutation, including the repeat.
        status, current = await ctx.request(
            prefix + f".{suffix}.verify", "GET", f"{ctx.base}/{entitlement_id}"
        )
        reason = validate_fixture(current, ctx.app["id"], sku, ctx.args.user, ctx.args.guild, consume=True)
        if (
            not 200 <= status < 300
            or not isinstance(current, dict)
            or str(current.get("id")) != entitlement_id
            or reason
            or classify_temporal(current, datetime.now(UTC)) not in ("ended", "deleted")
        ):
            ctx.block(
                prefix + "." + suffix,
                reason or "Historical state or identity could not be reverified before consume",
            )
            return
        mutation_case = prefix + "." + suffix
        if not ctx.state.get("cases", {}).get(mutation_case, {}).get("mutation_complete"):
            await ctx.request(mutation_case, "POST", f"{ctx.base}/{entitlement_id}/consume")
            _note(ctx, mutation_case, mutation_complete=True)
        await _inspect(ctx, prefix + f".{suffix}.after", current, sku)
        await ctx.probe(prefix + f".{suffix}.interaction")
        await ctx.observe(prefix + f".{suffix}.window")
    _note(
        ctx,
        prefix,
        status="observed",
        initial_state=initial_state,
        initial_consumed=row.get("consumed", False),
        limitation="Historical consumption only; does not manufacture an ended/deleted entitlement.",
    )
    return initial_state


async def capture_lifecycle(ctx):
    """Capture only supplied legitimate fixtures; no fixture fabrication or CLI IO."""
    if _complete(ctx, "lifecycle") and getattr(ctx.args, "mode", None) != "watch":
        return
    watch = list(dict.fromkeys(str(value) for value in getattr(ctx.args, "watch_entitlement", []) or []))
    consume = list(dict.fromkeys(str(value) for value in getattr(ctx.args, "consume_history", []) or []))
    ctx.block(
        "lifecycle.exact_boundary",
        "Exact ends_at<=now atomic interaction eligibility is unproven: REST samples and later interactions are not simultaneous server-clock evidence.",
    )
    transitions = set()
    if ctx.state.get("expiry_source"):
        transition = await _watch(ctx, ctx.state["expiry_source"]["entitlement_id"])
        if transition == "natural_end":
            prefix = f"lifecycle.watch.{ctx.state['expiry_source']['entitlement_id']}"
            ctx.state["cases"]["lifecycle.natural_expiry"] = dict(ctx.state["cases"][prefix])
            duplicate = ctx.state["cases"].get(prefix + ".duplicate_after_natural_end")
            if duplicate:
                ctx.state["cases"]["lifecycle.duplicate_after_natural_end"] = dict(duplicate)
        else:
            ctx.block(
                "lifecycle.natural_expiry",
                "Natural expiry was not established in this watch; preserve the directory for resume.",
            )
        _done(ctx, "lifecycle")
        return
    if not watch:
        for case, reason in (
            ("natural_expiry", "No authorized legitimate live entitlement IDs were supplied."),
            (
                "future_start",
                "No authorized entitlement IDs with a legitimate future starts_at were supplied.",
            ),
            (
                "duplicate_after_natural_end",
                "Requires an observed natural end of an authorized subscription, not test-delete or an already-ended row.",
            ),
        ):
            ctx.block("lifecycle." + case, reason)
    elif not getattr(ctx.args, "live_lifecycle_confirmed", False):
        ctx.block(
            "lifecycle.watch.authorization",
            "Watching live lifecycle fixtures requires --live-lifecycle-confirmed; IDs were not polled.",
        )
    else:
        for entitlement_id in watch:
            if not entitlement_id.isascii() or not entitlement_id.isdecimal():
                ctx.block("lifecycle.watch.invalid_id", "Watch entitlement ID must be a decimal snowflake.")
                continue
            transition = await _watch(ctx, entitlement_id)
            if transition:
                transitions.add(transition)
                root = ctx.state["cases"][f"lifecycle.watch.{entitlement_id}"]
                if transition == "natural_end":
                    ctx.state["cases"]["lifecycle.natural_expiry"] = dict(root)
                    duplicate = ctx.state["cases"].get(
                        f"lifecycle.watch.{entitlement_id}.duplicate_after_natural_end"
                    )
                    if duplicate:
                        ctx.state["cases"]["lifecycle.duplicate_after_natural_end"] = dict(duplicate)
                elif transition == "natural_start":
                    ctx.state["cases"]["lifecycle.future_start"] = dict(root)
                ctx.save()
        if "natural_end" not in transitions:
            ctx.block(
                "lifecycle.natural_expiry",
                "Supplied fixtures did not establish an observed natural end in this watch window.",
            )
            ctx.block(
                "lifecycle.duplicate_after_natural_end",
                "No natural end of a subscription was established; no substitute delete or synthetic timestamp was used.",
            )
        if "natural_start" not in transitions:
            ctx.block(
                "lifecycle.future_start",
                "Supplied fixtures did not establish a legitimate future-to-active transition in this watch window.",
            )
    ctx.block(
        "lifecycle.consume_ended",
        "No GET-verified ended type-4 consumable has been captured with explicit consume authorization.",
    )
    if ctx.state["cases"].get("lifecycle.consume_deleted", {}).get("status") != "observed":
        ctx.block(
            "lifecycle.consume_deleted",
            "No GET-verified deleted type-4 consumable has been captured with explicit consume authorization; 404 alone cannot verify one.",
        )
    for entitlement_id in consume:
        if not entitlement_id.isascii() or not entitlement_id.isdecimal():
            ctx.block("lifecycle.consume.invalid_id", "Consume entitlement ID must be a decimal snowflake.")
            continue
        state = await _consume(ctx, entitlement_id)
        if state in ("ended", "deleted"):
            root = ctx.state["cases"][f"lifecycle.consume.{entitlement_id}"]
            ctx.state["cases"][f"lifecycle.consume_{state}"] = dict(root)
            if root.get("initial_consumed") is True:
                ctx.block(
                    f"lifecycle.consume_{state}_unconsumed",
                    "The supplied historical row was already consumed; unconsumed historical behavior remains unverified.",
                )
            ctx.save()
    _done(ctx, "lifecycle")
