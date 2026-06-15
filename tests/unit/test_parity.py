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


def test_parity_matrix_gaps_section_in_sync():
    """The committed gap list must match what's derived from discord.py — this is
    the drift guard: a discord.py change makes it stale until regenerated."""
    text = MATRIX.read_text()
    begin = text.index(parity.GAPS_BEGIN_MARKER)
    end = text.index(parity.GAPS_END_MARKER) + len(parity.GAPS_END_MARKER)
    assert text[begin:end] == parity.gaps_section()


def test_parity_matrix_out_of_scope_section_in_sync():
    """The committed out-of-scope list must match the derived one."""
    text = MATRIX.read_text()
    begin = text.index(parity.OOS_BEGIN_MARKER)
    end = text.index(parity.OOS_END_MARKER) + len(parity.OOS_END_MARKER)
    assert text[begin:end] == parity.out_of_scope_section()
