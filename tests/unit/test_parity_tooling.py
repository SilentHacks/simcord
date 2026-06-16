"""The parity matrix generator is part of the CI drift guard, so its rewrite
machinery is exercised directly here (the section *contents* are checked by
tests/unit/test_parity.py and tests/conformance/test_route_coverage.py).
"""

from __future__ import annotations

from simcord import parity


def _full_document() -> str:
    return "\n".join(
        [
            "# header",
            parity.routes_section(),
            "## middle",
            parity.gaps_section(),
            "## middle 2",
            parity.out_of_scope_section(),
            "# footer",
        ]
    )


def test_update_matrix_is_idempotent_then_repairs_drift(tmp_path):
    doc = tmp_path / "matrix.md"
    doc.write_text(_full_document())

    # A freshly-generated document is already in sync.
    assert parity.update_matrix(doc) is False

    # Corrupt a generated section: update_matrix rewrites it back and reports change.
    doc.write_text(doc.read_text().replace("routes implemented", "routes implemented DRIFT", 1))
    assert parity.update_matrix(doc) is True
    assert "DRIFT" not in doc.read_text()
    # ...and is stable again afterwards.
    assert parity.update_matrix(doc) is False


def test_replace_section_swaps_only_between_markers():
    text = "A" + parity.BEGIN_MARKER + "old" + parity.END_MARKER + "B"
    out = parity._replace_section(text, parity.BEGIN_MARKER, parity.END_MARKER, "NEW")
    assert out == "ANEWB"
