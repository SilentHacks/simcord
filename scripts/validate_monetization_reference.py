#!/usr/bin/env python3
"""Validate raw capture integrity, not Discord behavior or parity agreement.

Exit 0: valid evidence (possibly partial / externally blocked); 1: bad or
unfinished capture; 2: CLI usage error. Reports never echo captured values.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

STREAMS = ("actions", "requests", "rest", "gateway", "interactions", "health")
MUTATIONS = {"POST", "PUT", "PATCH", "DELETE"}
REDACTED = {"[REDACTED]", "<redacted>", "[redacted]"}
SECRET_KEYS = {"token", "authorization", "session_id", "access_token", "refresh_token", "client_secret"}
REFERENCE = re.compile(r"^(actions|requests|rest|gateway|interactions|health)\.jsonl:([1-9][0-9]*)$")


def _time(value):
    if not isinstance(value, str):
        raise ValueError
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError
    return result.timestamp()


def _path(value):
    return re.sub(r"^/api/v[0-9]+", "", urlsplit(value).path)


def _secret(value):
    if isinstance(value, dict):
        return any(
            (
                str(key).lower() in SECRET_KEYS
                and item not in (None, "")
                and not (isinstance(item, str) and item in REDACTED)
            )
            or _secret(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_secret(item) for item in value)
    return isinstance(value, str) and bool(
        re.search(
            r"(?:\b(?:Bot|Bearer)\s+[A-Za-z0-9_.-]{12,}|/webhooks/[0-9]+/(?!\[REDACTED\])[A-Za-z0-9_.-]{12,})",
            value,
        )
    )


def _json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def constant(value):
        raise ValueError

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def _new_report():
    return {
        "valid": False,
        "schema_version": None,
        "legacy": False,
        "errors": [],
        "warnings": [],
        "pending_prerequisites": [],
        "coverage": {
            "cases": [],
            "rest_outcomes": 0,
            "interaction_payloads": 0,
            "gateway_events": {},
            "observation_windows": [],
            "pagination": {"pages": 0, "full_pages": 0, "linked_pages": 0, "calibrated": False},
        },
    }


def _claimed(version, cases, stream, ref):
    if version == 1 or not cases:
        return True
    return any(
        isinstance(case, dict) and case.get("status") == "observed" and ref in case.get(stream, [])
        for case in cases.values()
    )


def _load_bundle(directory, report, issue):
    def load(path):
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            issue(
                "unreadable_file",
                path.name if path.name in {"manifest.json", *(s + ".jsonl" for s in STREAMS)} else "bundle",
            )
            return None

    text = load(Path(directory) / "manifest.json")
    try:
        manifest = _json(text) if text is not None else None
        if not isinstance(manifest, dict):
            raise ValueError
    except (ValueError, TypeError):
        issue("invalid_manifest", "manifest.json")
        return None
    version = manifest.get("schema_version")
    report["schema_version"] = version if type(version) is int else None
    report["legacy"] = version == 1
    if type(version) is not int or version not in (1, 2):
        issue("unsupported_schema", "manifest.json")
        return None
    if _secret(manifest):
        issue("unredacted_secret", "manifest.json")
    if manifest.get("status") not in ("completed", "completed_with_blockers"):
        issue("unfinished_capture", "manifest.json")
    if manifest.get("pending_write") is not None or manifest.get("uncertain_writes"):
        issue("unresolved_mutation", "manifest.json")
    if manifest.get("cleanup_errors"):
        issue("cleanup_failed", "manifest.json")
    app = manifest.get("application_id")
    if not isinstance(app, str) or not app.isdigit():
        issue("missing_application_identity", "manifest.json")

    streams = {}
    times = {}
    for stream in STREAMS:
        path = Path(directory) / (stream + ".jsonl")
        rows = []
        streams[stream] = rows
        if not path.exists():
            continue
        text = load(path)
        if text is None:
            continue
        if text and not text.endswith("\n"):
            issue("truncated_jsonl", path.name)
        previous = None
        for number, line in enumerate(text.splitlines(), 1):
            location = f"{stream}.jsonl:{number}"
            try:
                row = _json(line)
                if not isinstance(row, dict):
                    raise ValueError
            except ValueError:
                issue("invalid_jsonl", location)
                rows.append({})
                continue
            rows.append(row)
            if _secret(row):
                issue("unredacted_secret", location)
            try:
                stamp = _time(row.get("utc"))
                times[location] = stamp
                if previous is not None and stamp < previous:
                    issue("timestamp_regression", location)
                previous = stamp
                if "started_utc" in row and _time(row["started_utc"]) > stamp:
                    issue("invalid_request_timing", location)
            except (ValueError, TypeError, OverflowError):
                issue("invalid_timestamp", location)
            mono = row.get("monotonic_ns", row.get("monotonic"))
            if mono is not None and (type(mono) not in (int, float) or mono < 0):
                issue("invalid_monotonic_timestamp", location)
    _check_shapes(manifest, streams, issue)
    return manifest, streams, times, version


def _check_shapes(manifest, streams, issue):
    """Reject expected malformed fields at their source without discarding other diagnostics."""

    def bad(location):
        issue("malformed_evidence_structure", location)

    def string_fields(record, keys, location):
        for key in keys:
            if key in record and record[key] is not None and not isinstance(record[key], str):
                bad(location)
                record[key] = None

    def string_list(record, key, location):
        if key in record and (
            not isinstance(record[key], list) or any(not isinstance(item, str) for item in record[key])
        ):
            bad(location)
            record[key] = []

    string_fields(
        manifest,
        (
            "application_id",
            "user_id",
            "other_user_id",
            "guild_id",
            "other_guild_id",
            "status",
            "temporary_command_id",
        ),
        "manifest.json",
    )
    for key in ("created_entitlement_ids", "deleted_entitlement_ids", "purchase_entitlement_ids"):
        string_list(manifest, key, "manifest.json")
    for key in ("manual_test_purchase_ids", "manual_live_entitlement_ids"):
        value = manifest.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            issue("invalid_manual_entitlement_registry", "manifest.json")
            manifest[key] = []
    if not isinstance(manifest.get("config", {}), dict):
        bad("manifest.json")
        manifest["config"] = {}
    string_list(manifest.get("config", {}), "consume_history", "manifest.json")
    if not isinstance(manifest.get("skus", []), list):
        bad("manifest.json")
        manifest["skus"] = []
    for sku in manifest.get("skus", []):
        if isinstance(sku, dict):
            string_fields(sku, ("id",), "manifest.json")
    commands = manifest.get("temporary_commands", {})
    if isinstance(commands, dict):
        for entry in commands.values():
            if isinstance(entry, dict):
                string_fields(entry, ("id", "create_ref"), "manifest.json")
    if not isinstance(manifest.get("cases", {}), dict):
        issue("invalid_cases", "manifest.json")
        manifest["cases"] = {}
    for index, case in enumerate(manifest.get("cases", {}).values(), 1):
        location = f"cases[{index}]"
        if not isinstance(case, dict):
            continue
        string_fields(
            case,
            (
                "status",
                "selected_entitlement_id",
                "verification_ref",
                "expected_actor_id",
                "expected_guild_id",
                "source_bundle",
            ),
            location,
        )
        for key in (*STREAMS, "baseline_refs", "baseline_ids"):
            if key in case and (
                not isinstance(case[key], list) or any(not isinstance(ref, str) for ref in case[key])
            ):
                issue("invalid_references", location)
                case[key] = []
    for stream, rows in streams.items():
        for index, row in enumerate(rows, 1):
            location = f"{stream}.jsonl:{index}"
            string_fields(
                row,
                (
                    "method",
                    "path",
                    "path_query",
                    "request_id",
                    "event",
                    "action",
                    "interaction_id",
                ),
                location,
            )
            if stream == "requests":
                query = row.get("query")
                if query is not None and (
                    not isinstance(query, dict)
                    or any(
                        not isinstance(value, (str, int, float, bool, type(None)))
                        and not (
                            isinstance(value, list)
                            and all(isinstance(item, (str, int, float, bool)) for item in value)
                        )
                        for value in query.values()
                    )
                ):
                    issue("invalid_request", location)
                    row["method"] = None
                try:
                    if isinstance(row.get("path"), str):
                        _path(row["path"])
                except ValueError:
                    issue("invalid_request", location)
                    row["method"] = None
            if stream == "rest" and isinstance(row.get("path_query"), str):
                try:
                    _path(row["path_query"])
                except ValueError:
                    issue("invalid_response", location)
                    row["path_query"] = None
            for key in ("connection_epoch", "monotonic_ns", "monotonic"):
                if key in row and type(row[key]) not in (int, float):
                    bad(location)
                    row.pop(key)
            payload = row.get("payload") if stream in ("gateway", "interactions") else row.get("response")
            objects = payload if isinstance(payload, list) else [payload]
            for item in objects:
                if not isinstance(item, dict):
                    continue
                if stream == "gateway":
                    string_fields(item, ("t",), location)
                    item = item.get("d")
                    if not isinstance(item, dict):
                        continue
                string_fields(item, ("id", "application_id", "user_id", "guild_id", "sku_id"), location)
                string_list(item, "sku_ids", location)
                if stream == "interactions":
                    for key in ("member", "user"):
                        if key in item and item[key] is not None and not isinstance(item[key], dict):
                            bad(location)
                            item[key] = {}
                    member = item.get("member") or {}
                    if "user" in member and not isinstance(member["user"], dict):
                        bad(location)
                        member["user"] = {}


def _ref_rows(streams, case, key, location, issue):
    refs = case.get(key, [])
    if not isinstance(refs, list):
        issue("invalid_references", location)
        return []
    found = []
    for ref in refs:
        match = REFERENCE.fullmatch(ref) if isinstance(ref, str) else None
        if not match or match[1] != key or int(match[2]) > len(streams[match[1]]):
            issue("broken_reference", location)
        else:
            found.append(streams[match[1]][int(match[2]) - 1])
    return found


def _correlate(streams, times, version, cases, issue):
    requests = streams["requests"]
    responses = streams["rest"]
    pairs = []
    used = set()
    for index, request in enumerate(requests):
        location = f"requests.jsonl:{index + 1}"
        method, path = request.get("method"), request.get("path")
        if method not in {"GET", *MUTATIONS} or not isinstance(path, str):
            issue("invalid_request", location)
            continue
        if version == 2 and not isinstance(request.get("request_id"), str):
            issue("missing_request_id", location)
            continue
        matches = [
            i
            for i, row in enumerate(responses)
            if i not in used
            and (
                row.get("request_id") == request.get("request_id")
                if version == 2
                else row.get("method") == method
                and isinstance(row.get("path_query"), str)
                and _path(row["path_query"]) == _path(path)
                and row.get("attempt") == request.get("attempt")
            )
        ]
        if not matches:
            issue(
                "unresolved_mutation" if method in MUTATIONS else "missing_response",
                location,
                warning=method == "GET" and not _claimed(version, cases, "requests", location),
            )
            continue
        match = matches[0]
        used.add(match)
        response = responses[match]
        raw_path = response.get("path_query")
        correlated = True
        if (
            response.get("method") != method
            or not isinstance(raw_path, str)
            or _path(raw_path) != _path(path)
            or response.get("body") != request.get("body")
        ):
            issue("correlation_mismatch", location)
            correlated = False
        if isinstance(raw_path, str):
            expected = parse_qs(urlencode(request.get("query") or {}, doseq=True), keep_blank_values=True)
            if parse_qs(urlsplit(raw_path).query, keep_blank_values=True) != expected:
                issue("query_mismatch", location)
                correlated = False
        if correlated:
            pairs.append((request, response, f"rest.jsonl:{match + 1}"))
        if times.get(f"rest.jsonl:{match + 1}", 0) < times.get(location, 0):
            issue("response_precedes_request", location)
    if len(used) != len(responses):
        issue("uncorrelated_response", "rest.jsonl")
    ids = [row.get("request_id") for row in requests if row.get("request_id") is not None]
    if len(ids) != len(set(str(value) for value in ids)):
        issue("duplicate_request_id", "requests.jsonl")
    return pairs


def _manual_authorized(manifest, pair_lookup, entitlement, mutation_ref):
    app = manifest.get("application_id")
    cases = manifest.get("cases", {})
    manual_ids = manifest.get("manual_test_purchase_ids", [])
    live_ids = manifest.get("manual_live_entitlement_ids", [])
    if entitlement not in manual_ids or entitlement in live_ids:
        return False
    if manifest.get("config", {}).get("test_mode_confirmed") is not True:
        return False
    for case in cases.values() if isinstance(cases, dict) else ():
        if (
            not isinstance(case, dict)
            or case.get("explicit_manual_test_purchase") is not True
            or case.get("selected_entitlement_id") != entitlement
            or mutation_ref not in case.get("rest", [])
        ):
            continue
        verification = pair_lookup.get(case.get("verification_ref"))
        if verification is None:
            continue
        position, request, response = verification
        snapshot = response.get("response")
        if (
            position >= pair_lookup[mutation_ref][0]
            or request.get("method") != "GET"
            or _path(request.get("path", "")) != f"/applications/{app}/entitlements/{entitlement}"
            or response.get("status") != 200
            or not isinstance(snapshot, dict)
            or snapshot.get("id") != entitlement
            or snapshot.get("type") != 4
            or snapshot.get("application_id") != app
            or snapshot.get("user_id") not in {manifest.get("user_id"), manifest.get("other_user_id")}
            or snapshot.get("user_id") is None
            or snapshot.get("deleted") is not False
            or snapshot.get("consumed") is not False
        ):
            continue
        baseline = case.get("baseline_ids")
        refs = case.get("baseline_refs", [])
        if (
            not isinstance(baseline, list)
            or any(not isinstance(value, str) for value in baseline)
            or len(set(baseline)) != len(baseline)
            or entitlement in baseline
            or not isinstance(refs, list)
            or not refs
        ):
            continue
        collected, first, branches, valid = [], None, {}, True
        for ref in refs:
            page = pair_lookup.get(ref)
            if page is None:
                valid = False
                break
            page_position, page_request, page_response = page
            query = page_request.get("query") or {}
            rows = page_response.get("response")
            if (
                page_position >= position
                or page_request.get("method") != "GET"
                or _path(page_request.get("path", "")) != f"/applications/{app}/entitlements"
                or page_response.get("status") != 200
                or not isinstance(rows, list)
                or str(query.get("user_id")) != snapshot["user_id"]
                or str(query.get("sku_ids")) != snapshot.get("sku_id")
                or str(query.get("exclude_deleted")).lower() != "false"
                or str(query.get("limit", 100)) != "100"
                or any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in rows)
            ):
                valid = False
                break
            cursor = "after" if "after" in query else "before" if "before" in query else None
            if first is None:
                valid = cursor is None and len(rows) <= 100
                first = (page_position, rows)
            else:
                old_position, old_rows = branches.get(cursor, first)
                old_ids = [int(row["id"]) for row in old_rows if row["id"].isdigit()]
                valid = (
                    page_position > old_position
                    and len(old_rows) == 100
                    and bool(old_ids)
                    and cursor is not None
                    and str(query[cursor]) == str(max(old_ids) if cursor == "after" else min(old_ids))
                    and len(rows) <= 100
                )
            if not valid:
                break
            collected.extend(row["id"] for row in rows)
            if cursor:
                branches[cursor] = (page_position, rows)
        complete = first is not None and (
            (len(first[1]) < 100 and not branches)
            or (
                set(branches) == {"before", "after"} and all(len(page[1]) < 100 for page in branches.values())
            )
        )
        if valid and complete and len(collected) == len(set(collected)) and set(collected) == set(baseline):
            return True
    return False


def _prove_mutations(manifest, pairs, coverage, version, issue):
    app = manifest.get("application_id")
    created, deleted, command_created, command_deleted = {}, set(), {}, set()
    identity = False
    list_pages = []
    seen_entitlements = set()
    purchase_snapshots = {}
    cases = manifest.get("cases", {})
    probes = (
        [
            case
            for name, case in cases.items()
            if name.startswith("reject.non_sandbox_delete") and isinstance(case, dict)
        ]
        if isinstance(cases, dict)
        else []
    )
    unknown_entitlements = set()
    sources = manifest.get("source_bundles", [])
    source_paths = (
        [item.get("path") if isinstance(item, dict) else item for item in sources]
        if isinstance(sources, list)
        else []
    )
    manual_ids = manifest.get("manual_test_purchase_ids", [])
    live_ids = manifest.get("manual_live_entitlement_ids", [])
    pair_lookup = {ref: (index, request, response) for index, (request, response, ref) in enumerate(pairs)}

    for request, row, location in pairs:
        status, payload = row.get("status"), row.get("response")
        method, path = request["method"], _path(request["path"])
        if row.get("outcome_unknown") is True:
            issue("unresolved_mutation", location)
            continue
        if status is None:
            if method in MUTATIONS or row.get("outcome_unknown"):
                issue("unresolved_mutation", location)
            else:
                issue(
                    "transport_capture_failed",
                    location,
                    warning=not _claimed(version, cases, "rest", location),
                )
            continue
        if type(status) is not int or not 100 <= status <= 599 or "response" not in row:
            issue("invalid_response", location)
            continue
        coverage["rest_outcomes"] += 1
        success = 200 <= status < 300
        if path == "/oauth2/applications/@me" and success:
            identity = isinstance(payload, dict) and payload.get("id") == app
            if not identity:
                issue("application_identity_mismatch", location)
        target = re.match(r"/applications/([^/]+)", path)
        if target and target[1] != app:
            issue("application_identity_mismatch", location)
        objects = payload if isinstance(payload, list) else [payload]
        for item in objects:
            if isinstance(item, dict) and "application_id" in item and item["application_id"] != app:
                issue("application_identity_mismatch", location)
        base = f"/applications/{app}/entitlements"
        if path == base and method == "POST" and success:
            body = request.get("body")
            if not isinstance(body, dict) or not isinstance(payload, dict):
                issue("invalid_create_provenance", location)
            else:
                owner_type = body.get("owner_type")
                owner_field = "guild_id" if owner_type == 1 else "user_id"
                other_field = "user_id" if owner_type == 1 else "guild_id"
                authorized = (
                    {manifest.get("guild_id"), manifest.get("other_guild_id")}
                    if owner_type == 1
                    else {manifest.get("user_id"), manifest.get("other_user_id")}
                )
                entitlement = payload.get("id")
                if (
                    not isinstance(entitlement, str)
                    or not entitlement.isdigit()
                    or entitlement in live_ids
                    or entitlement in manual_ids
                    or entitlement in seen_entitlements
                    or entitlement in created
                    or type(payload.get("type")) is not int
                    or payload.get("type") != 8
                    or type(owner_type) is not int
                    or owner_type not in (1, 2)
                    or not isinstance(body.get("sku_id"), str)
                    or payload.get("sku_id") != body.get("sku_id")
                    or not isinstance(body.get("owner_id"), str)
                    or payload.get(owner_field) != body.get("owner_id")
                    or payload.get(other_field) is not None
                    or payload.get(owner_field) is None
                    or payload.get(owner_field) not in authorized
                    or payload.get("application_id") != app
                ):
                    issue("invalid_create_provenance", location)
                else:
                    created[entitlement] = {
                        "sku_id": payload.get("sku_id"),
                        "owner_id": payload[owner_field],
                        "owner_type": owner_type,
                    }
        if path.startswith(base + "/") and method == "DELETE":
            entitlement = path[len(base) + 1 :]
            if entitlement not in created or entitlement in live_ids:
                snapshot = purchase_snapshots.get(entitlement, {})
                allowed_probe = any(
                    probe.get("explicit_source_purchase") is True
                    and probe.get("source_bundle") in source_paths
                    and location in probe.get("rest", [])
                    and snapshot.get("application_id") == app
                    and snapshot.get("type") == 4
                    and snapshot.get("user_id") in {manifest.get("user_id"), manifest.get("other_user_id")}
                    for probe in probes
                )
                unknown_probe = (
                    entitlement in unknown_entitlements
                    and not success
                    and str(request.get("action", "")).startswith("reject.unknown_entitlement")
                )
                allowed = (
                    _manual_authorized(manifest, pair_lookup, entitlement, location)
                    if entitlement in manual_ids
                    else allowed_probe or unknown_probe
                )
                if (
                    entitlement in live_ids
                    or not allowed
                    or "cleanup" in str(request.get("action", "")).lower()
                ):
                    issue("unowned_cleanup", location)
            if status == 204 or (
                status == 404 and isinstance(payload, dict) and payload.get("code") == 10029
            ):
                deleted.add(entitlement)
        command_route = re.fullmatch(
            rf"/applications/{re.escape(str(app))}(?:/guilds/([^/]+))?/commands(?:/([^/]+))?", path
        )
        if (
            command_route
            and method == "POST"
            and command_route[2] is None
            and success
            and isinstance(payload, dict)
        ):
            guild, command = command_route[1], payload.get("id")
            if guild is not None and guild not in {manifest.get("guild_id"), manifest.get("other_guild_id")}:
                issue("unauthorized_command_guild", location)
            if isinstance(command, str):
                command_created[(guild, command)] = location
        if command_route and command_route[2] is not None and method == "DELETE":
            key = (command_route[1], command_route[2])
            if key not in command_created:
                issue("unowned_command_cleanup", location)
            if status in (204, 404):
                command_deleted.add(key)
        elif "/commands/" in path and method == "DELETE":
            issue("unowned_command_cleanup", location)
        if path == base and method == "GET" and success:
            if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
                issue("invalid_list_payload", location)
            else:
                list_pages.append((row, location))
        if path == base or path.startswith(base + "/"):
            seen_entitlements.update(
                item["id"] for item in objects if isinstance(item, dict) and isinstance(item.get("id"), str)
            )
        if (
            path.startswith(base + "/")
            and method == "GET"
            and success
            and isinstance(payload, dict)
            and payload.get("id") == path[len(base) + 1 :]
        ):
            purchase_snapshots[payload["id"]] = payload
        if (
            path.startswith(base + "/")
            and method == "GET"
            and status == 404
            and isinstance(payload, dict)
            and payload.get("code") == 10029
        ):
            unknown_entitlements.add(path[len(base) + 1 :])
        if path.endswith("/consume") and method == "POST":
            eid = path.split("/")[-2]
            snapshot = purchase_snapshots.get(eid, {})
            explicit = (
                eid in manifest.get("purchase_entitlement_ids", [])
                or eid in manifest.get("config", {}).get("consume_history", [])
                or any(
                    probe.get("explicit_source_purchase") and eid in str(request.get("action", ""))
                    for probe in probes
                )
            )
            verified_test = (
                snapshot.get("type") == 4
                and snapshot.get("application_id") == app
                and snapshot.get("user_id") in {manifest.get("user_id"), manifest.get("other_user_id")}
            )
            unknown_probe = (
                eid in unknown_entitlements
                and not success
                and str(request.get("action", "")).startswith("reject.unknown_entitlement")
            )
            allowed = (
                _manual_authorized(manifest, pair_lookup, eid, location)
                if eid in manual_ids
                else eid in created or (explicit and verified_test) or unknown_probe
            )
            if eid in live_ids or not allowed:
                issue("unverified_consume_target", location)
    if not identity:
        issue("missing_application_identity_response", "rest.jsonl")
    return created, deleted, command_created, command_deleted, list_pages


def _check_cleanup_registry(manifest, version, created, deleted, command_created, command_deleted, issue):
    manual_ids = manifest.get("manual_test_purchase_ids", [])
    live_ids = manifest.get("manual_live_entitlement_ids", [])
    owned = (
        manifest.get("owned_entitlements", {})
        if version == 2
        else {key: {} for key in manifest.get("created_entitlement_ids", [])}
    )
    if not isinstance(owned, dict):
        issue("invalid_ownership_registry", "manifest.json")
        owned = {}
    for entitlement, entry in owned.items():
        if entitlement in live_ids or entitlement in manual_ids:
            issue("manual_entitlement_claimed_owned", "manifest.json")
        if entitlement not in created:
            issue("missing_create_provenance", "manifest.json")
        elif version == 2 and (
            not isinstance(entry, dict)
            or any(
                entry.get(key) != created[entitlement].get(key)
                for key in ("sku_id", "owner_id", "owner_type")
            )
        ):
            issue("ownership_registry_mismatch", "manifest.json")
        if entitlement not in deleted:
            issue("owned_entitlement_not_cleaned", "manifest.json")
        if version == 2 and (not isinstance(entry, dict) or entry.get("deleted") is not True):
            issue("cleanup_not_checkpointed", "manifest.json")
    if set(created) - set(owned):
        issue("unregistered_created_entitlement", "manifest.json")
    if set(manifest.get("deleted_entitlement_ids", [])) - deleted:
        issue("unsupported_cleanup_claim", "manifest.json")
    if set(manifest.get("deleted_entitlement_ids", [])) & set(manual_ids + live_ids):
        issue("manual_entitlement_claimed_cleanup", "manifest.json")
    if set(command_created) - command_deleted:
        issue("temporary_command_not_cleaned", "manifest.json")
    command = manifest.get("temporary_command_id")
    primary_key = (manifest.get("guild_id"), command)
    if command and not any(
        key in command_created and key in command_deleted for key in (primary_key, (None, command))
    ):
        issue("unsupported_command_cleanup", "manifest.json")
    commands = manifest.get("temporary_commands", {})
    if not isinstance(commands, dict):
        issue("invalid_command_registry", "manifest.json")
    else:
        for guild, entry in commands.items():
            key = (guild, entry.get("id")) if isinstance(entry, dict) else (guild, None)
            if (
                key not in command_created
                or key not in command_deleted
                or entry.get("create_ref") != command_created.get(key)
                or entry.get("deleted") is not True
            ):
                issue("unsupported_command_cleanup", "manifest.json")


def _pagination(list_pages, times, pagination, issue):
    pagination["pages"] = len(list_pages)
    groups = {}
    cursor_checks = []
    for row, location in list_pages:
        query = parse_qs(urlsplit(row["path_query"]).query)
        try:
            limit = int(query.get("limit", [100])[0])
            if not 1 <= limit <= 100:
                raise ValueError
        except (ValueError, TypeError):
            issue("invalid_page_limit", location)
            continue
        if len(row["response"]) >= limit:
            pagination["full_pages"] += 1
        cursor_key = "after" if "after" in query else "before" if "before" in query else None
        if cursor_key:
            filters = {key: value for key, value in query.items() if key not in ("after", "before")}
            for previous, previous_ref in list_pages:
                if times.get(previous_ref, 0) >= times.get(location, 0):
                    continue
                old_query = parse_qs(urlsplit(previous["path_query"]).query)
                old_ids = [
                    int(item["id"]) for item in previous["response"] if str(item.get("id", "")).isdigit()
                ]
                boundary = (min(old_ids) if cursor_key == "before" else max(old_ids)) if old_ids else None
                if {
                    key: value for key, value in old_query.items() if key not in ("after", "before")
                } == filters and str(boundary) == query[cursor_key][0]:
                    pagination["linked_pages"] += 1
                    break
        ids = [int(item["id"]) for item in row["response"] if str(item.get("id", "")).isdigit()]
        filters = tuple(
            sorted(
                (key, tuple(value)) for key, value in query.items() if key not in ("after", "before", "limit")
            )
        )
        group = groups.setdefault(filters, {"ids": set(), "pages": [], "linked": False})
        group["ids"].update(ids)
        if cursor_key and query[cursor_key][0].isdigit():
            boundary = int(query[cursor_key][0])
            strict = all(value < boundary if cursor_key == "before" else value > boundary for value in ids)
            linked = any(
                len(old_ids) == 100 and boundary == (min(old_ids) if cursor_key == "before" else max(old_ids))
                for old_ids in group["pages"]
                if old_ids
            )
            group["linked"] |= linked and limit == 100
            cursor_checks.append(
                {
                    "ref": location,
                    "direction": cursor_key,
                    "limit": limit,
                    "cursor_excluded": boundary not in ids,
                    "strict_numeric": strict,
                    "filters": [key for key, _ in filters],
                    "order": "ascending"
                    if ids == sorted(ids)
                    else "descending"
                    if ids == sorted(ids, reverse=True)
                    else "mixed",
                }
            )
        if limit == 100:
            group["pages"].append(ids)
    pagination["large_traversal_captured"] = any(
        len(group["ids"]) > 100 and group["linked"] for group in groups.values()
    )
    pagination["max_distinct_per_filter"] = max((len(group["ids"]) for group in groups.values()), default=0)
    pagination["cursor_checks"] = cursor_checks
    pagination["cursor_directions_captured"] = sorted({check["direction"] for check in cursor_checks})
    pagination["filtered_cursor_captured"] = any(
        set(check["filters"]) & {"user_id", "guild_id"} and "sku_ids" in check["filters"]
        for check in cursor_checks
    )
    pagination["multi_sku_captured"] = any(
        len(dict(filters).get("sku_ids", ("",))[0].split(",")) > 1 for filters in groups
    )
    # These are capture facts, not approval of a simulator policy or atomic boundary.
    if pagination["full_pages"]:
        issue("list_history_may_be_partial", "rest.jsonl", warning=True)


def _observations(health, times, requested_seconds, version, cases, windows, issue):
    starts = {}
    window_refs = set()
    for index, row in enumerate(health, 1):
        location = f"health.jsonl:{index}"
        event = row.get("event")
        if (
            event in {"capture_failed", "sequence_gap", "invalid_session", "gateway_error"}
            or row.get("sequence_contiguous") is False
        ):
            issue(
                "gateway_capture_unhealthy",
                location,
                warning=not _claimed(version, cases, "health", location),
            )
        if event == "observation_start":
            action = row.get("action")
            if not isinstance(action, str):
                issue("invalid_observation", location)
                continue
            if action in starts:
                issue(
                    "unfinished_observation",
                    starts[action][1],
                    warning=not _claimed(version, cases, "health", starts[action][1]),
                )
            starts[action] = (row, location, index)
        elif event == "observation_end":
            begin = starts.pop(row.get("action"), None)
            if begin is None:
                issue(
                    "invalid_observation", location, warning=not _claimed(version, cases, "health", location)
                )
                continue
            start, start_ref, start_index = begin
            elapsed = times.get(location, 0) - times.get(start_ref, 0)
            if "monotonic_ns" in row and "monotonic_ns" in start:
                elapsed = (row["monotonic_ns"] - start["monotonic_ns"]) / 1_000_000_000
            requested = row.get("requested_seconds", start.get("requested_seconds", requested_seconds))
            valid_duration = (
                type(requested) in (int, float)
                and requested >= 0
                and elapsed > 0
                and elapsed + 0.01 >= requested
            )
            healthy = (
                start.get("connected") is True
                and row.get("connected") is True
                and not any(
                    item.get("event")
                    in {"disconnected", "sequence_gap", "capture_failed", "READY", "RESUMED"}
                    or item.get("sequence_contiguous") is False
                    for item in health[start_index : index - 1]
                )
            )
            healthy = (
                healthy
                and start.get("connection_epoch") == row.get("connection_epoch")
                and start.get("health_errors") == row.get("health_errors")
                and start.get("sequence_contiguous") is not False
                and row.get("sequence_contiguous") is not False
            )
            if not valid_duration or not healthy:
                issue(
                    "invalid_observation_window",
                    location,
                    warning=not (
                        _claimed(version, cases, "health", start_ref)
                        or _claimed(version, cases, "health", location)
                    ),
                )
            else:
                window_refs.update((start_ref, location))
                windows.append(
                    {
                        "start": start_ref,
                        "end": location,
                        "seconds": round(elapsed, 3),
                        "sequence_verified": row.get("sequence_contiguous") is True,
                    }
                )
    for _, start_ref, _ in starts.values():
        issue(
            "unfinished_observation",
            start_ref,
            warning=_claimed(version, cases, "health", start_ref) is False,
        )
    return window_refs


def _raw_payloads(manifest, streams, times, coverage, issue):
    app = manifest.get("application_id")
    health = streams["health"]
    sequence = {}
    for stream in ("gateway", "interactions"):
        ready_times = sorted(
            times.get(f"health.jsonl:{number}", 0)
            for number, entry in enumerate(health, 1)
            if entry.get("event") == "READY"
        )
        for index, row in enumerate(streams[stream], 1):
            location = f"{stream}.jsonl:{index}"
            payload = row.get("payload")
            if not isinstance(payload, dict):
                issue("missing_raw_payload", location)
                continue
            if stream == "gateway":
                event, data, seq = payload.get("t"), payload.get("d"), payload.get("s")
                if event not in {
                    "ENTITLEMENT_CREATE",
                    "ENTITLEMENT_UPDATE",
                    "ENTITLEMENT_DELETE",
                    "SUBSCRIPTION_CREATE",
                    "SUBSCRIPTION_UPDATE",
                    "SUBSCRIPTION_DELETE",
                } or not isinstance(data, dict):
                    issue("invalid_gateway_payload", location)
                    continue
                counts = coverage["gateway_events"]
                counts[event] = counts.get(event, 0) + 1
            else:
                data, seq = payload, row.get("sequence")
                if not isinstance(data.get("entitlements"), list) or not data.get("id"):
                    issue("invalid_interaction_payload", location)
                else:
                    coverage["interaction_payloads"] += 1
            if type(seq) is not int or seq < 0:
                issue("missing_gateway_sequence", location)
            # Filtered dispatches legitimately skip sequences. Only backwards/duplicate
            # values in one stream prove a fault; continuous health is separate proof.
            else:
                epoch = row.get(
                    "connection_epoch", sum(stamp <= times.get(location, 0) for stamp in ready_times)
                )
                key = (stream, epoch)
                if seq <= sequence.get(key, -1):
                    issue("gateway_sequence_regression", location)
                sequence[key] = seq
            if stream == "gateway" and event.startswith("SUBSCRIPTION_"):
                allowed_skus = {sku.get("id") for sku in manifest.get("skus", []) if isinstance(sku, dict)}
                if (
                    data.get("user_id") not in {manifest.get("user_id"), manifest.get("other_user_id")}
                    or not data.get("sku_ids")
                    or not set(data["sku_ids"]) <= allowed_skus
                ):
                    issue("subscription_identity_mismatch", location)
            elif data.get("application_id") != app:
                issue("application_identity_mismatch", location)


def _claims(manifest, streams, window_refs, case_coverage, pending, issue):
    cases = manifest.get("cases", {})
    if not isinstance(cases, dict):
        issue("invalid_cases", "manifest.json")
        cases = {}
    for index, (case_name, case) in enumerate(cases.items(), 1):
        location = f"cases[{index}]"
        if not isinstance(case, dict):
            issue("invalid_case", location)
            continue
        evidence = {key: _ref_rows(streams, case, key, location, issue) for key in STREAMS}
        status = case.get("status")
        if status == "blocked":
            label = (
                case_name
                if isinstance(case_name, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", case_name)
                else location
            )
            pending.append({"case": label, "location": location, "reason": "external_prerequisite"})
            if not isinstance(case.get("reason"), str) or not case["reason"].strip():
                issue("missing_blocker_reason", location)
        elif status == "capture_failed":
            issue("case_capture_failed", location)
        elif status != "observed":
            issue("invalid_case_status", location)
        elif not any(evidence[key] for key in ("rest", "gateway", "interactions", "health")):
            issue("unsupported_observed_claim", location)
        else:
            actor, guild = case.get("expected_actor_id"), case.get("expected_guild_id")
            if actor is not None or guild is not None:
                authorized_actor = actor is None or actor in {
                    manifest.get("user_id"),
                    manifest.get("other_user_id"),
                }
                authorized_guild = guild is None or guild in {
                    manifest.get("guild_id"),
                    manifest.get("other_guild_id"),
                }
                payloads = [
                    row["payload"] for row in evidence["interactions"] if isinstance(row.get("payload"), dict)
                ]
                receipts = [row for row in evidence["actions"] if row.get("event") == "probe_received"]
                if receipts:
                    accepted = {row.get("interaction_id") for row in receipts}
                    payloads = [payload for payload in payloads if payload.get("id") in accepted]
                    if len(receipts) != 1 or not payloads:
                        issue("unsupported_interaction_identity_claim", location)

                def matches(payload, actor=actor, guild=guild):
                    member = payload.get("member") or {}
                    user = member.get("user") or payload.get("user") or {}
                    return (actor is None or user.get("id") == actor) and (
                        guild is None or payload.get("guild_id") == guild
                    )

                if (
                    not authorized_actor
                    or not authorized_guild
                    or not payloads
                    or not all(matches(payload) for payload in payloads)
                ):
                    issue("unsupported_interaction_identity_claim", location)
            if any(
                ref not in window_refs
                for ref, row in zip(case.get("health", []), evidence["health"], strict=False)
                if row.get("event") in {"observation_start", "observation_end"}
            ):
                issue("unsupported_observation_claim", location)
            case_coverage.append(
                {"location": location, "raw_streams": [key for key, rows in evidence.items() if rows]}
            )
        if case.get("pagination_complete") is True or case.get("pagination_calibrated") is True:
            issue("unsupported_pagination_claim", location)
    if manifest.get("pagination_calibrated") is True:
        issue("unsupported_pagination_claim", "manifest.json")


def _validate_bundle(directory, report, issue):
    loaded = _load_bundle(directory, report, issue)
    if loaded is None:
        return report
    manifest, streams, times, version = loaded
    coverage = report["coverage"]
    cases = manifest.get("cases", {})
    pairs = _correlate(streams, times, version, cases, issue)
    created, deleted, commands, removed_commands, pages = _prove_mutations(
        manifest, pairs, coverage, version, issue
    )
    _check_cleanup_registry(manifest, version, created, deleted, commands, removed_commands, issue)
    _pagination(pages, times, coverage["pagination"], issue)
    windows = _observations(
        streams["health"],
        times,
        manifest.get("observation_seconds", 0),
        version,
        cases,
        coverage["observation_windows"],
        issue,
    )
    _raw_payloads(manifest, streams, times, coverage, issue)
    _claims(manifest, streams, windows, coverage["cases"], report["pending_prerequisites"], issue)
    if version == 1:
        issue("legacy_partial_evidence", "manifest.json", warning=True)
    if any(
        row.get("event") == "disconnected" and row.get("action") != "cleanup" for row in streams["health"]
    ):
        issue("connection_gap_outside_window", "health.jsonl", warning=True)
    report["valid"] = not report["errors"]
    return report


def validate_bundle(directory: Path) -> dict:
    """Validate integrity, not calibration or permission to mutate; never echo raw values."""
    report = _new_report()

    def issue(code, location, warning=False):
        report["warnings" if warning else "errors"].append({"code": code, "location": location})

    try:
        return _validate_bundle(Path(directory), report, issue)
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError, RecursionError):
        issue("malformed_evidence_structure", "bundle")
        report["valid"] = False
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=Path)
    args = parser.parse_args(argv)
    reports = [validate_bundle(directory) for directory in args.directories]
    print(json.dumps(reports, separators=(",", ":"), allow_nan=False))
    return 0 if all(report["valid"] for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
