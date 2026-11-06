"""Publish durable real observations only after receipt-proven cleanup; no network."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .ledger import load_ledger, normalize, write_ledger
from .transport import LAB_GUILD, Journal, open_evidence


def packets(path):
    lines = path.read_text().splitlines(keepends=True)
    if not lines or any(not line.endswith("\n") for line in lines):
        raise ValueError("Empty or truncated capture stream; preserve evidence before recovery")
    result = [json.loads(line) for line in lines]
    for row in result:
        if not isinstance(row, dict):
            raise ValueError("Malformed capture stream")
        datetime.fromisoformat(row["utc"])
    return result


def publish(directory, destination, supplements=()):
    directory = Path(directory)
    state = json.loads((directory / "manifest.json").read_text())
    if state.get("guild") != LAB_GUILD or state.get("schema") != 1:
        raise ValueError("Publication requires the approved behavior lab journal")
    if state.get("pending_write") or state.get("uncertain_writes"):
        raise ValueError("Refusing publication with an ambiguous write; never replay it")
    if any(not item.get("deleted") for item in state["owned"]) or any(
        not item.get("done") for item in state["restores"]
    ):
        raise ValueError(
            "Refusing publication before all positively owned resources and restorations are cleaned"
        )
    evidence = open_evidence(directory, (), resume=True)
    journal = Journal(evidence, LAB_GUILD, resume=True)
    for item in journal.state["owned"]:
        journal.prove_deleted(item)
    observations = packets(directory / "observations.jsonl")
    requests = packets(directory / "requests.jsonl")
    # Deterministic normalization may improve (e.g. a newly identified volatile
    # server revision); scenario snapshots are never synthesized or rewritten.
    rows = [normalize(packet["row"], {}) for packet in observations]
    provenance = {
        "recovered_from": "Receipt-proven manifest.json and durable observations.jsonl",
        "capture_started_at": requests[0]["utc"],
        "capture_completed_at": observations[-1]["utc"],
        "capture_runs": [[row["id"] for row in rows]],
    }
    if state.get("permission_oracle_cases"):
        provenance["permission_oracle"] = {
            "implicit": False,
            "cases": state["permission_oracle_cases"],
            "comparison": "Known explicit permission bits; raw live receipts in permissions.jsonl",
        }
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError(
            "Duplicate scenario observations require investigation, not last-write-wins publication"
        )
    captures = []
    for supplement in supplements:
        ledger = load_ledger(supplement)
        captures.append(
            {
                "capture_date": ledger["capture_date"],
                "provenance": ledger["provenance"],
                "rows": [row["id"] for row in ledger["rows"]],
            }
        )
        provenance["capture_runs"].extend(ledger["provenance"]["capture_runs"])
        for row in ledger["rows"]:
            existing = by_id.get(row["id"])
            if existing is not None and existing != row:
                raise ValueError(
                    f"Overlapping live captures disagree: {row['id']}; do not overwrite historical evidence"
                )
            if existing is None:
                rows.append(row)
                by_id[row["id"]] = row
    if captures:
        provenance["supplements"] = captures
    write_ledger(destination, rows, provenance=provenance, capture_date=observations[0]["utc"])
