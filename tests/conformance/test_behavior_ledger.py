"""Only genuine captures enter this ratchet; documentation is not live evidence."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "behavior"


async def test_live_behavior_ledger_has_no_new_mismatch_or_stale_gap(monkeypatch):
    ledger, gaps = FIXTURES / "ledger.json", FIXTURES / "known_gaps.json"
    assert ledger.exists() and gaps.exists(), "Publish capture and explicitly justified known gaps together"
    monkeypatch.syspath_prepend(str(ROOT))
    module = importlib.import_module("scripts.behavior_reference.ledger")
    await module.replay(ledger, gaps)
    document = await asyncio.to_thread((ROOT / "docs" / "parity-matrix.md").read_text)
    generated = document.split("<!-- behavior:begin (generated — do not edit by hand) -->", 1)[1]
    generated = generated.split("<!-- behavior:end -->", 1)[0].strip()
    assert generated == module.evidence_table(module.load_ledger(ledger))
