"""Recorder-side receipt correlation and positive cleanup ownership.

These checks authorize recorder mutations; validation reports never do.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit


def receipt_pair(directory, reference):
    try:
        stream, line = reference.rsplit(":", 1)
        if stream != "rest.jsonl" or int(line) < 1:
            raise ValueError
        receipts = (directory / stream).read_text(encoding="utf-8").splitlines()
        receipt = json.loads(receipts[int(line) - 1])
        request_ref = receipt["request_id"]
        stream, line = request_ref.rsplit(":", 1)
        if stream != "requests.jsonl" or int(line) < 1:
            raise ValueError
        requests = (directory / stream).read_text(encoding="utf-8").splitlines()
        request = json.loads(requests[int(line) - 1])
        if request.get("request_id") != request_ref:
            raise ValueError
        path = urlsplit(receipt.get("path_query", "")).path.removeprefix("/api/v10")
        if request.get("method") != receipt.get("method") or request.get("path") != path:
            raise ValueError
        if request.get("body") != receipt.get("body") or request.get("action") != receipt.get("action"):
            raise ValueError
        if type(receipt.get("status")) is not int or not 200 <= receipt["status"] < 300:
            raise ValueError
        return request, receipt
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        raise RuntimeError("Cleanup requires a correlated positive create receipt.") from None


def require_entitlement_ownership(directory, state, application_id, entitlement_id):
    owned = state.get("owned_entitlements", {}).get(entitlement_id)
    excluded = (
        state.get("baseline_ids", [])
        + state.get("manual_live_entitlement_ids", [])
        + state.get("manual_test_purchase_ids", [])
    )
    if not isinstance(owned, dict) or entitlement_id in excluded:
        raise RuntimeError("Refusing cleanup without a positive create receipt.")
    request, receipt = receipt_pair(directory, owned.get("create_ref", ""))
    body, row = request.get("body"), receipt.get("response")
    if not isinstance(body, dict) or not isinstance(row, dict):
        raise RuntimeError("Cleanup create receipt has no identity proof.")
    owner_type = owned.get("owner_type")
    owner_key, other_key = ("guild_id", "user_id") if owner_type == 1 else ("user_id", "guild_id")
    owner_fields = ("guild_id", "other_guild_id") if owner_type == 1 else ("user_id", "other_user_id")
    authorized_owners = {state.get(field) for field in owner_fields} - {None}
    if (
        request.get("method") != "POST"
        or request.get("path") != f"/applications/{application_id}/entitlements"
        or type(row.get("type")) is not int
        or row["type"] != 8
        or row.get("id") != entitlement_id
        or row.get("application_id") != application_id
        or row.get("sku_id") != owned.get("sku_id")
        or body.get("sku_id") != row.get("sku_id")
        or type(owner_type) is not int
        or owner_type not in (1, 2)
        or type(body.get("owner_type")) is not int
        or owned.get("owner_id") not in authorized_owners
        or body.get("owner_type") != owner_type
        or str(body.get("owner_id")) != owned.get("owner_id")
        or row.get(owner_key) != owned.get("owner_id")
        or (owner_type == 2 and row.get(other_key) is not None)
        or row.get("deleted", False) is not False
    ):
        raise RuntimeError("Cleanup create receipt does not prove this test grant's identity.")


def require_command_ownership(directory, application_id, guild_id, command):
    request, receipt = receipt_pair(directory, command.get("create_ref", ""))
    body, row = request.get("body"), receipt.get("response")
    if (
        request.get("method") != "POST"
        or request.get("path") != f"/applications/{application_id}/guilds/{guild_id}/commands"
        or not isinstance(body, dict)
        or body.get("name") != "simcord_parity_probe"
        or not isinstance(row, dict)
        or type(row.get("id")) is not str
        or not row["id"].isdigit()
        or int(row["id"]) <= 0
        or row.get("id") != command.get("id")
        or row.get("name") != body.get("name")
        or type(row.get("type")) is not int
        or row["type"] != 1
    ):
        raise RuntimeError("Cleanup receipt does not prove this temporary command's identity.")


def begin_write(evidence, state, intent, *, cleaning=False):
    """Fsync intent before request recording or I/O; unknown writes never replay."""
    unresolved = list(state.get("uncertain_writes", []))
    if state.get("pending_write"):
        unresolved.append(state["pending_write"])
    if unresolved and (
        not cleaning
        or any(
            item.get("method") == intent["method"] and item.get("path") == intent["path"]
            for item in unresolved
        )
    ):
        raise RuntimeError("An unresolved mutation exists; do not replay it.")
    if state.get("pending_write"):
        state.setdefault("uncertain_writes", []).append(state["pending_write"])
    state["pending_write"] = intent
    evidence.manifest(**state)


def recover_receipt(directory, intent):
    """Resolve the last recorded attempt, never an earlier 429 before a lost attempt."""
    request_path = directory / "requests.jsonl"
    requests = (
        [json.loads(line) for line in request_path.read_text(encoding="utf-8").splitlines()]
        if request_path.exists()
        else []
    )
    attempts = [
        (number, row)
        for number, row in enumerate(requests, 1)
        if number >= intent["request_start_line"]
        and row.get("action") == intent["case"]
        and row.get("method") == intent["method"]
        and row.get("path") == intent["path"]
    ]
    if not attempts:
        return False, None, None
    number, request = attempts[-1]
    if (
        request.get("request_id") != f"requests.jsonl:{number}"
        or request.get("body") != intent.get("body")
        or request.get("query") != intent.get("query")
    ):
        return True, None, None
    rest_path = directory / "rest.jsonl"
    rows = rest_path.read_text(encoding="utf-8").splitlines() if rest_path.exists() else []
    for number, line in reversed(list(enumerate(rows, 1))):
        receipt = json.loads(line)
        if (
            receipt.get("request_id") == request["request_id"]
            and receipt.get("action") == intent["case"]
            and receipt.get("method") == intent["method"]
            and urlsplit(receipt.get("path_query", "")).path.removeprefix("/api/v10") == intent["path"]
            and receipt.get("body") == intent.get("body")
        ):
            return True, receipt, f"rest.jsonl:{number}"
    return True, None, None
