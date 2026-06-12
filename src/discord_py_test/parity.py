"""Auto-generated route inventory for the parity matrix.

The route table is the source of truth for what is implemented; the docs page
embeds the generated section between markers so it cannot rot. Regenerate with
``python -m discord_py_test.parity docs/parity-matrix.md``; a unit test asserts
the committed page is in sync.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .http import routes as _routes  # noqa: F401  — importing registers every route handler
from .http.router import _ROUTES

BEGIN_MARKER = "<!-- routes:begin (generated — do not edit by hand) -->"
END_MARKER = "<!-- routes:end -->"

_METHOD_ORDER = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}


def implemented_routes() -> list[tuple[str, str]]:
    """Every implemented (method, path template), sorted by path then method."""
    out = []
    for method, handlers in _ROUTES.items():
        for segments, _handler in handlers:
            out.append((method, "/" + "/".join(segments)))
    return sorted(out, key=lambda r: (r[1], _METHOD_ORDER.get(r[0], 9)))


def routes_section() -> str:
    """The generated markdown section listing every implemented route."""
    lines = [
        BEGIN_MARKER,
        "",
        f"{len(implemented_routes())} routes implemented. Anything else fails loudly with "
        "`RouteNotImplemented`.",
        "",
        "| Method | Route |",
        "| --- | --- |",
    ]
    lines += [f"| `{method}` | `{path}` |" for method, path in implemented_routes()]
    lines += ["", END_MARKER]
    return "\n".join(lines)


def update_matrix(path: Path) -> bool:
    """Rewrite the generated section in ``path``; returns True if it changed."""
    text = path.read_text()
    begin = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    updated = text[:begin] + routes_section() + text[end:]
    if updated != text:
        path.write_text(updated)
        return True
    return False


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/parity-matrix.md")
    changed = update_matrix(target)
    print(f"{target}: {'updated' if changed else 'already in sync'}")
