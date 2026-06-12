from pathlib import Path

from simcord import parity

MATRIX = Path(__file__).parents[2] / "docs" / "parity-matrix.md"


def test_parity_matrix_routes_section_in_sync():
    """The committed matrix must match the route table; regenerate with
    `python -m simcord.parity docs/parity-matrix.md`."""
    text = MATRIX.read_text()
    begin = text.index(parity.BEGIN_MARKER)
    end = text.index(parity.END_MARKER) + len(parity.END_MARKER)
    assert text[begin:end] == parity.routes_section()
