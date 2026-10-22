"""Prepare a purchase-free expiry watch from an authenticated completed capture."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from monetization_reference_lifecycle import _instant, classify_temporal, validate_fixture
from validate_monetization_reference import validate_bundle


def load_expiry_source(directory):
    directory = Path(directory).resolve()
    report = validate_bundle(directory)
    if not report["valid"]:
        raise RuntimeError(
            "Expiry source evidence is invalid; preserve it for review, do not repeat checkout."
        )
    manifest = json.loads((directory / "manifest.json").read_text())
    stage = manifest.get("checkout", {}).get("live_upgrade", {})
    identity = stage.get("identity", {})
    app, user, guild = manifest["application_id"], manifest["user_id"], manifest["guild_id"]
    eid = stage.get("a")
    if (
        not stage.get("cancellation_verified")
        or identity.get("application_id") != app
        or identity.get("user_id") != user
        or not eid
        or stage.get("upgrade_prompted")
    ):
        raise RuntimeError("Need an identified, verified-canceled A subscription with no upgrade attempt.")
    sku_id = identity["sku_ids"][0]
    sku = next((item for item in manifest["skus"] if item["id"] == sku_id and item["type"] == 5), None)
    if sku is None:
        raise RuntimeError("Saved entitlement is not a subscription SKU.")
    requests = {
        row["request_id"]: row
        for row in map(json.loads, (directory / "requests.jsonl").read_text().splitlines())
    }
    responses = list(map(json.loads, (directory / "rest.jsonl").read_text().splitlines()))
    entitlement = subscription = None
    for number, response in enumerate(responses, 1):
        request = requests.get(response.get("request_id"), {})
        if request.get("method") != "GET" or response.get("status") != 200:
            continue
        body = response.get("response")
        if request.get("path") == f"/applications/{app}/entitlements/{eid}":
            reason = validate_fixture(body, app, sku, user, guild)
            if (
                not reason
                and body.get("id") == eid
                and body.get("type") in (1, 8)
                and classify_temporal(body, _instant(response["utc"])) == "active"
            ):
                entitlement = (body, response["utc"], f"rest.jsonl:{number}")
        if (
            request.get("path") == f"/skus/{sku_id}/subscriptions"
            and isinstance(body, list)
            and len(body) < 100
            and str((request.get("query") or {}).get("user_id")) == user
        ):
            matches = [
                row
                for row in body
                if row.get("user_id") == user
                and sku_id in row.get("sku_ids", [])
                and eid in row.get("entitlement_ids", [])
            ]
            if len(matches) == 1:
                subscription = (matches[0], f"rest.jsonl:{number}")
    if not entitlement or not subscription:
        raise RuntimeError("Missing correlated active entitlement and subscription-list evidence.")
    sub, subscription_ref = subscription
    end = _instant(sub.get("current_period_end"))
    if sub.get("status") != 2 or not _instant(sub.get("canceled_at")) or end is None:
        raise RuntimeError("Latest saved subscription is not verified canceled with a known period end.")
    before = None
    for number, record in enumerate(
        map(json.loads, (directory / "interactions.jsonl").read_text().splitlines()), 1
    ):
        payload = record.get("payload", {})
        actor = (payload.get("member") or {}).get("user", payload.get("user", {}))
        stamp = _instant(record.get("utc"))
        if (
            payload.get("application_id") == app
            and payload.get("guild_id") == guild
            and actor.get("id") == user
            and stamp
            and stamp < end
        ):
            for row in payload.get("entitlements", []):
                if (
                    row.get("id") == eid
                    and not validate_fixture(row, app, sku, user, guild)
                    and classify_temporal(row, stamp) == "active"
                ):
                    before = (record["utc"], f"interactions.jsonl:{number}")
    if before is None:
        raise RuntimeError("No authenticated before-expiry interaction contains the watched entitlement.")
    hashes = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in ("manifest.json", "requests.jsonl", "rest.jsonl", "interactions.jsonl")
    }
    return {
        "path": str(directory),
        "sha256": hashes,
        "application_id": app,
        "user_id": user,
        "guild_id": guild,
        "channel": manifest["config"].get("channel", "parity"),
        "entitlement_id": eid,
        "sku_id": sku_id,
        "sku_a": sku["name"],
        "sku_b": manifest["config"]["user_sku_b"],
        "subscription_id": sub["id"],
        "period_end": end.isoformat(),
        "subscription_ref": subscription_ref,
        "baseline": {
            "row": entitlement[0],
            "utc": entitlement[1],
            "before_probe_complete": True,
            "probe_completed_utc": before[0],
            "source_rest_ref": entitlement[2],
            "source_interaction_ref": before[1],
            "source_bundle": str(directory),
        },
    }


async def check_expiry_subscription(ctx, case):
    source = ctx.state["expiry_source"]
    status, rows = await ctx.request(
        case,
        "GET",
        f"/skus/{source['sku_id']}/subscriptions",
        query={"user_id": source["user_id"], "limit": "100"},
    )
    matches = (
        [row for row in rows if isinstance(row, dict) and row.get("id") == source["subscription_id"]]
        if isinstance(rows, list)
        else []
    )
    if status != 200 or not isinstance(rows, list) or len(rows) >= 100 or len(matches) != 1:
        ctx.state["expiry_billing_verified"] = False
        ctx.block(
            case + ".verified",
            "Subscription reporting unavailable/incomplete. Continue capturing the authorized entitlement, but do not certify natural billing expiry.",
        )
        return True
    row = matches[0]
    if (
        row.get("user_id") != source["user_id"]
        or source["sku_id"] not in row.get("sku_ids", [])
        or (row.get("entitlement_ids") and source["entitlement_id"] not in row["entitlement_ids"])
        or row.get("status") not in (1, 2)
        or not _instant(row.get("canceled_at"))
        or _instant(row.get("current_period_end")) != _instant(source["period_end"])
    ):
        ctx.block(
            case + ".verified",
            "Subscription was resumed, changed, or lost its identity/schedule. No purchase/upgrade/cancellation is automated.",
        )
        return False
    ctx.state["expiry_billing_verified"] = True
    ctx.state["cases"].pop(case + ".verified", None)
    ctx.save()
    return True


async def prepare_expiry(ctx):
    source = ctx.state["expiry_source"]
    if not await check_expiry_subscription(ctx, "expiry.preflight.subscription"):
        return False
    end = _instant(source["period_end"])
    if end is None:
        ctx.block("expiry.schedule", "Recorded expiry date is invalid; no command or grant created.")
        return False
    start = end - timedelta(minutes=30)
    print(
        f"Expiry-only watch: expected period end {end.isoformat()}; no purchase or upgrade flow.", flush=True
    )
    if datetime.now(UTC) < start:
        ctx.block(
            "expiry.start_window",
            f"Too early. Run the same command from {start.isoformat()} UTC. Nothing is scheduled; no Gateway command or entitlement was created.",
        )
        return False
    ctx.state.setdefault("watch_baselines", {}).setdefault(source["entitlement_id"], dict(source["baseline"]))
    ctx.save()
    return True
