"""Auto-generated route inventory for the parity matrix.

The route table is the source of truth for what is implemented; the docs page
embeds the generated sections between markers so they cannot rot. Two sections
are generated:

* the **implemented** routes (from simcord's own route table), and
* the **not-yet-implemented** discord.py REST methods, derived by reading the
  ``Route(...)`` literals out of ``discord.http.HTTPClient`` and subtracting the
  ones simcord serves — so a discord.py upgrade that adds or renames a route
  changes this list, making the committed matrix stale until it is regenerated
  (``tests/unit/test_parity.py`` enforces sync). That is the parity drift guard.

Regenerate with ``python -m simcord.parity docs/parity-matrix.md``.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

from .http import routes as _routes  # noqa: F401  — importing registers every route handler
from .http.router import _ROUTES

BEGIN_MARKER = "<!-- routes:begin (generated — do not edit by hand) -->"
END_MARKER = "<!-- routes:end -->"
GAPS_BEGIN_MARKER = "<!-- gaps:begin (generated — do not edit by hand) -->"
GAPS_END_MARKER = "<!-- gaps:end -->"

_METHOD_ORDER = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}

# discord.py HTTPClient members that are not REST endpoints (transport plumbing,
# the gateway, the CDN), so they have no route to compare against.
_HTTP_INFRA = frozenset(
    {
        "request",
        "close",
        "ws_connect",
        "static_login",
        "get_from_cdn",
        "get_bot_gateway",
        "get_gateway",
        "recreate",
        "clear",
        "get_ratelimit",
    }
)

# Pulls the (verb, path) out of a ``Route('GET', '/channels/{channel_id}')``
# literal — discord.py writes every endpoint's route this way.
_ROUTE_LITERAL = re.compile(r"""Route\(\s*['"](\w+)['"]\s*,\s*\n?\s*['"]([^'"]+)['"]""")


def _normalize(path: str) -> str:
    """Reduce a route template to verb-comparable shape: ``{anything}`` -> ``{}``.

    simcord and discord.py name path parameters differently (``{event_id}`` vs
    ``{guild_scheduled_event_id}``), so compare structure, not parameter names.
    """
    return re.sub(r"\{[^}]+\}", "{}", path if path.startswith("/") else "/" + path)


def implemented_routes() -> list[tuple[str, str]]:
    """Every implemented (method, path template), sorted by path then method."""
    out = []
    for method, handlers in _ROUTES.items():
        for segments, _handler in handlers:
            out.append((method, "/" + "/".join(segments)))
    return sorted(out, key=lambda r: (r[1], _METHOD_ORDER.get(r[0], 9)))


def discordpy_rest_routes() -> dict[str, list[tuple[str, str]]]:
    """Map each discord.py ``HTTPClient`` REST method to the route(s) it builds.

    Methods whose route cannot be read from source (they delegate to another
    method, or build the path dynamically) are omitted.
    """
    from discord.http import HTTPClient

    out: dict[str, list[tuple[str, str]]] = {}
    for name, func in inspect.getmembers(HTTPClient, inspect.isfunction):
        if name.startswith("_") or name in _HTTP_INFRA:
            continue
        try:
            source = inspect.getsource(func)
        except (OSError, TypeError):
            # discord.py installed without source (zipped/compiled): no route to
            # read. test_route_extraction_is_healthy guards against this silently
            # gutting the gap list.
            continue
        found = _ROUTE_LITERAL.findall(source)
        if found:
            out[name] = [(verb, _normalize(path)) for verb, path in found]
    return out


def unimplemented_routes() -> list[tuple[str, str, str]]:
    """The discord.py REST routes simcord does not serve: (method, verb, path)."""
    implemented = {(verb, _normalize(path)) for verb, path in implemented_routes()}
    gaps: set[tuple[str, str, str]] = set()
    for name, built in discordpy_rest_routes().items():
        for verb, path in built:
            if (verb, path) not in implemented:
                gaps.add((name, verb, path))
    return sorted(gaps, key=lambda g: (g[2], _METHOD_ORDER.get(g[1], 9), g[0]))


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


def gaps_section() -> str:
    """The generated markdown section listing not-yet-implemented discord.py routes."""
    gaps = unimplemented_routes()
    lines = [
        GAPS_BEGIN_MARKER,
        "",
        f"{len(gaps)} discord.py REST routes are not yet implemented; calling one fails loudly "
        "with `RouteNotImplemented` (path parameters shown as `{}`). Open an issue if your bot "
        "needs one.",
        "",
        "| discord.py `HTTPClient` method | Route |",
        "| --- | --- |",
    ]
    lines += [f"| `{name}` | `{verb} {path}` |" for name, verb, path in gaps]
    lines += ["", GAPS_END_MARKER]
    return "\n".join(lines)


def _replace_section(text: str, begin: str, end: str, section: str) -> str:
    start = text.index(begin)
    stop = text.index(end) + len(end)
    return text[:start] + section + text[stop:]


def update_matrix(path: Path) -> bool:
    """Rewrite both generated sections in ``path``; returns True if it changed."""
    text = path.read_text()
    updated = _replace_section(text, BEGIN_MARKER, END_MARKER, routes_section())
    updated = _replace_section(updated, GAPS_BEGIN_MARKER, GAPS_END_MARKER, gaps_section())
    if updated != text:
        path.write_text(updated)
        return True
    return False


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/parity-matrix.md")
    changed = update_matrix(target)
    print(f"{target}: {'updated' if changed else 'already in sync'}")
