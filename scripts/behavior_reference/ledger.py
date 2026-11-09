"""Normalized observations and a strict new-mismatch/stale-gap conformance ratchet."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from .scenarios import BY_ID, DOCS

SCHEMA = 1
SNOWFLAKE = re.compile(r"(?<!\d)\d{17,22}(?!\d)")
TIMESTAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:[\d.:+Z-]+")
MESSAGE_FIELDS = (
    "content",
    "tts",
    "mention_everyone",
    "mentions",
    "mention_roles",
    "attachments",
    "embeds",
    "flags",
    "pinned",
    "type",
    "poll",
    "message_reference",
    "reactions",
    "sticker_items",
)
CHANNEL_FIELDS = (
    "id",
    "type",
    "name",
    "topic",
    "flags",
    "permission_overwrites",
    "parent_id",
    "thread_metadata",
    "owner_id",
    "applied_tags",
    "rate_limit_per_user",
)


def normalize(value, symbols):
    reverse = {}
    for key, item in symbols.items():
        if key not in {"future-minute", "future-29days", "past-minute", "missing", "result"}:
            reverse.setdefault(str(item), "$" + key)
    if symbols.get("result") and str(symbols["result"]) not in reverse:
        reverse[str(symbols["result"])] = "$result"

    def walk(item):
        if isinstance(item, dict):
            return {
                walk(str(key)): "$position"
                if key == "position"
                else "$version"
                if key == "version"
                else walk(value)
                for key, value in item.items()
                if key.lower()
                not in {
                    "token",
                    "authorization",
                    "session_id",
                    "access_token",
                    "refresh_token",
                    "client_secret",
                }
            }
        if isinstance(item, (list, tuple)):
            return [walk(value) for value in item]
        if isinstance(item, int) and item >= 10**17:
            return reverse.get(str(item), "$id")
        if isinstance(item, str):
            if item in reverse:
                return reverse[item]
            item = TIMESTAMP.sub("$timestamp", item)
            item = SNOWFLAKE.sub(lambda match: reverse.get(match[0], "$id"), item)
            if item.startswith(
                ("https://cdn.discordapp.com/", "https://media.discordapp.net/", "https://cdn.simcord.test/")
            ):
                return "$attachment-url"
            for key in ("command-name", "external-emoji-name"):
                if key in symbols:
                    item = item.replace(str(symbols[key]), "$" + key)
            return item
        return item

    return walk(value)


def project(value, operation=""):
    """Compare modeled effects, canonicalizing absent optional SDK defaults."""
    if isinstance(value, list):
        return [project(item, operation) for item in value]
    if not isinstance(value, dict):
        return value
    if {"status", "response", "effect"} <= value.keys():
        return {**value, "response": project(value["response"]), "effect": project(value["effect"])}
    if "code" in value and "status" not in value:
        return {key: value[key] for key in ("code", "errors") if key in value}
    if "content" in value and "tts" in value:
        result = {key: value[key] for key in MESSAGE_FIELDS if key in value}
        result.setdefault("flags", 0)
        for key in ("mentions", "mention_roles", "attachments", "embeds", "reactions", "sticker_items"):
            result.setdefault(key, [])
        result["mentions"] = [user["id"] if isinstance(user, dict) else user for user in result["mentions"]]
        result["reactions"] = [
            {key: reaction[key] for key in ("count", "me", "emoji") if key in reaction}
            for reaction in result["reactions"]
        ]
        result["attachments"] = [
            {
                **{key: attachment[key] for key in ("filename", "size", "content_type") if key in attachment},
                "description": attachment.get("description"),
            }
            for attachment in result["attachments"]
        ]
        result["embeds"] = [
            {
                **{key: item for key, item in embed.items() if key != "content_scan_version"},
                "type": embed.get("type", "rich"),
            }
            for embed in result["embeds"]
        ]
        if "poll" in result:
            # These probes cover creation/limits, not transient tally/expiry metadata.
            result["poll"] = {
                key: value["poll"][key]
                for key in ("question", "answers", "allow_multiselect", "layout_type")
                if key in value["poll"]
            }
        return result
    if operation == "command" or ("application_id" in value and "description" in value):
        return value
    if "type" in value and "id" in value and "name" in value:
        fields = CHANNEL_FIELDS
        if operation == "gateway":
            fields += (
                "position",
                "nsfw",
                "last_message_id",
                "bitrate",
                "user_limit",
                "rtc_region",
                "available_tags",
                "default_reaction_emoji",
            )
        result = {key: value[key] for key in fields if key in value}
        for key, default in (("topic", None), ("flags", 0), ("rate_limit_per_user", 0)):
            result.setdefault(key, default)
        if "permission_overwrites" in result:
            result["permission_overwrites"] = sorted(
                result["permission_overwrites"], key=lambda item: (item["type"], str(item["id"]))
            )
        if operation == "gateway":
            result.setdefault("nsfw", False)
            result.setdefault("last_message_id", None)
        if "thread_metadata" in result:
            result.setdefault("applied_tags", [])
            result["thread_metadata"] = {
                key: value["thread_metadata"][key]
                for key in ("archived", "locked", "auto_archive_duration", "invitable")
                if key in value["thread_metadata"]
            }
        return result
    if "items" in value and "has_more" in value:
        return {
            "items": [
                {"pinned_at": item.get("pinned_at"), "message": project(item["message"])}
                for item in value["items"]
            ],
            "has_more": value["has_more"],
        }
    if "user" in value and "roles" in value:
        return {key: value[key] for key in ("nick", "communication_disabled_until") if key in value}
    if "gateway_channel" in value:
        return {
            key: project(item, "gateway") if key == "gateway_channel" else project(item)
            for key, item in value.items()
        }
    return {key: project(item) for key, item in value.items()}


def scenario_snapshot(case):
    return json.loads(json.dumps(asdict(case)))


def observation(case, method, operation, status, response, effect, symbols, *, request, source_kind="live"):
    if operation == "guild-channels" and isinstance(response, list):
        owned = {str(symbols[key]) for key in ("category", "text", "news", "voice", "stage", "forum")}
        response = [item for item in response if str(item.get("id")) in owned]
        response.sort(
            key=lambda item: next(key for key in sorted(symbols) if str(symbols[key]) == str(item["id"]))
        )
    return {
        "id": case.id,
        "source": {"kind": source_kind, "docs": DOCS[case.source]},
        "control": case.control,
        "scenario": scenario_snapshot(case),
        "request": normalize(
            {"method": method, "operation": operation, "target": case.target, **request}, symbols
        ),
        "observation": normalize(
            {
                "status": status,
                "code": response.get("code", 0) if isinstance(response, dict) else 0,
                "errors": response.get("errors") if isinstance(response, dict) else None,
                "response": project(response, operation),
                "effect": project(effect),
            },
            symbols,
        ),
    }


def validate_ledger(ledger):
    if set(ledger) != {"schema", "capture_date", "provenance", "rows"} or ledger["schema"] != SCHEMA:
        raise ValueError("Unsupported behavior ledger schema")
    datetime.fromisoformat(ledger["capture_date"])
    if ledger["provenance"].get("kind") != "live" or not ledger["provenance"].get("recorder"):
        raise ValueError("Only provenance-labelled real live captures belong in the ledger")
    ids = set()
    for row in ledger["rows"]:
        if row["id"] in ids or row["id"] not in BY_ID:
            raise ValueError("Duplicate or unknown behavior row")
        ids.add(row["id"])
        if row["source"] != {"kind": "live", "docs": DOCS[BY_ID[row["id"]].source]}:
            raise ValueError("Live row must distinguish live evidence from its documentation source")
        if row.get("scenario") != scenario_snapshot(BY_ID[row["id"]]):
            raise ValueError(
                f"Scenario changed since live capture: {row['id']}; recapture rather than rewriting historical evidence"
            )
        text = json.dumps(row)
        if (
            SNOWFLAKE.search(text)
            or TIMESTAMP.search(text)
            or re.search(
                r'"(?:token|authorization|session_id|access_token|refresh_token|client_secret)"\s*:',
                text,
                re.I,
            )
        ):
            raise ValueError("Ledger row contains an unnormalized identifier, timestamp or credential field")
        if re.search(r'"version"\s*:\s*(?:\d|"\d)', text):
            raise ValueError("Ledger row contains an unnormalized volatile server version")
        outcome = row["observation"]
        if (
            type(outcome["status"]) is not int
            or not 100 <= outcome["status"] <= 599
            or type(outcome["code"]) is not int
        ):
            raise ValueError("Malformed status/code observation")
        if row["control"] != BY_ID[row["id"]].control:
            raise ValueError("Scenario control differs from catalogue")
    for row in ledger["rows"]:
        if row["control"] and row["control"] not in ids:
            raise ValueError(f"Capture lacks single-cause control for {row['id']}")
    return ledger


def write_ledger(path, rows, *, provenance=None, capture_date=None):
    source = {
        "kind": "live",
        "recorder": "scripts.behavior_reference",
        "api": "v10",
        "gateway_capabilities": 1 << 15,
        "http_visibility_rollout": "Live observation on capture_date; documented HTTP omission starts 2026-11-16, with no early HTTP opt-in",
        "normalization": {
            "ids": "symbolic",
            "timestamps": "symbolic",
            "version": "$version: volatile server revision",
            "position": "$position: ambient guild ordering; raw identity preservation tested separately",
        },
        **(provenance or {}),
    }
    source.setdefault("capture_runs", [[row["id"] for row in rows]])
    ledger = validate_ledger(
        {
            "schema": SCHEMA,
            "capture_date": capture_date or datetime.now(UTC).isoformat(),
            "provenance": source,
            "rows": rows,
        }
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def load_ledger(path):
    return validate_ledger(json.loads(Path(path).read_text()))


def evidence_table(ledger):
    rows = ["| Scenario | Evidence | Status/code | Single-cause control |", "| --- | --- | --- | --- |"]
    for row in ledger["rows"]:
        outcome = row["observation"]
        rows.append(
            f"| `{row['id']}` | Live ({ledger['capture_date'][:10]}); [docs]({row['source']['docs']}) | {outcome['status']}/{outcome['code']} | `{row['control'] or '—'}` |"
        )
    return "\n".join(rows)


def ratchet(expected, actual, gaps):
    if gaps.get("schema") != SCHEMA or not isinstance(gaps.get("gaps"), dict):
        raise ValueError("Known gaps require schema=1 and an explicit gaps mapping")
    captured = {row["id"]: project(row["observation"]) for row in expected}
    replayed = {row["id"]: project(row["observation"]) for row in actual}
    failures = []
    expected_requests = {row["id"]: row.get("request") for row in expected}
    actual_requests = {row["id"]: row.get("request") for row in actual}
    for id in expected_requests.keys() & actual_requests.keys():
        if expected_requests[id] != actual_requests[id]:
            failures.append(
                f"{id}: raw request semantics differ from capture; never accept this as a known behavior gap"
            )
    for id in sorted(captured.keys() | replayed.keys() | gaps["gaps"].keys()):
        gap = gaps["gaps"].get(id)
        if id not in captured or id not in replayed:
            failures.append(f"{id}: stale gap or missing replay/capture")
        elif captured[id] == replayed[id]:
            if gap:
                failures.append(f"{id}: stale known gap now passes; remove it")
        elif not gap or not str(gap.get("reason", "")).strip():
            failures.append(
                f"{id}: new mismatch\nexpected={json.dumps(captured[id], sort_keys=True)}\nactual={json.dumps(replayed[id], sort_keys=True)}"
            )
        elif gap.get("expected") != captured[id] or gap.get("actual") != replayed[id]:
            failures.append(f"{id}: known-gap signature changed; investigate rather than accepting drift")
    if failures:
        raise AssertionError("\n".join(failures))


async def replay(ledger_path, gaps_path):
    from .offline import Offline
    from .runner import Runner

    ledger = load_ledger(ledger_path)
    gaps = json.loads(await asyncio.to_thread(Path(gaps_path).read_text))
    by_id = {}
    capture_runs = ledger["provenance"]["capture_runs"]
    expected_ids = {row["id"] for row in ledger["rows"]}
    if {case_id for run in capture_runs for case_id in run} != expected_ids:
        raise ValueError("Capture runs must cover exactly the recorded scenarios")
    for run in capture_runs:
        if len(run) != len(set(run)):
            raise ValueError("A capture run contains duplicate scenarios")
        with TemporaryDirectory(prefix="simcord-behavior-replay-") as directory:
            adapter = await Offline.create(Path(directory), ledger["provenance"])
            runner = Runner(adapter, gateway=adapter.gateway)
            await runner.setup()
            for case_id in run:
                row = await runner.run(BY_ID[case_id])
                previous = by_id.get(case_id)
                if previous is not None and (
                    previous["request"] != row["request"]
                    or project(previous["observation"]) != project(row["observation"])
                ):
                    raise AssertionError(f"Repeated scenario differs between capture runs: {case_id}")
                by_id.setdefault(case_id, row)
    rows = list(by_id.values())
    ratchet(ledger["rows"], rows, gaps)
    return rows
