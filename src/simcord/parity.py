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
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from .http import routes as _routes  # noqa: F401  — importing registers every route handler
from .http.router import _ROUTES

BEGIN_MARKER = "<!-- routes:begin (generated — do not edit by hand) -->"
END_MARKER = "<!-- routes:end -->"
GAPS_BEGIN_MARKER = "<!-- gaps:begin (generated — do not edit by hand) -->"
GAPS_END_MARKER = "<!-- gaps:end -->"
OOS_BEGIN_MARKER = "<!-- out-of-scope:begin (generated — do not edit by hand) -->"
OOS_END_MARKER = "<!-- out-of-scope:end -->"

_METHOD_ORDER = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}

# Friendly titles for the top-level resource each route lives under (its first path
# segment). Used to group the long flat route lists into per-area sections in the
# docs; an unmapped segment falls back to its own name, so a new discord.py resource
# still groups sensibly without a code change.
_AREA_TITLES = {
    "applications": "Applications",
    "auth": "Authentication",
    "channels": "Channels",
    "guilds": "Guilds",
    "interactions": "Interactions",
    "invites": "Invites",
    "oauth2": "OAuth2",
    "skus": "Monetization",
    "soundboard-default-sounds": "Soundboard",
    "stage-instances": "Stage instances",
    "sticker-packs": "Sticker packs",
    "stickers": "Stickers",
    "users": "Users",
    "webhooks": "Webhooks",
}


def _area(path: str) -> str:
    """The top-level resource a route path belongs to (its first segment)."""
    return path.strip("/").split("/", 1)[0]


def _area_title(area: str) -> str:
    return _AREA_TITLES.get(area, area.replace("-", " ").capitalize())


_R = TypeVar("_R", bound=tuple[str, ...])


def _grouped_tables(
    rows: list[_R],
    *,
    area_of: Callable[[_R], str],
    admonition: str,
    header: str,
    row: Callable[[_R], str],
) -> list[str]:
    """Render ``rows`` as one collapsible table per resource area.

    Each area becomes an expanded-by-default Material admonition (so the content
    stays searchable and Ctrl-F-able, but a reader can collapse areas they don't
    care about), titled with the friendly area name and its route count. ``rows``
    are pre-sorted, so each group preserves that order.
    """
    groups: dict[str, list[_R]] = {}
    for item in rows:
        groups.setdefault(area_of(item), []).append(item)
    lines: list[str] = []
    for area in sorted(groups):
        members = groups[area]
        lines.append(f'???+ {admonition} "{_area_title(area)} · {len(members)} route(s)"')
        lines += ["", f"    {header}", "    | --- | --- |"]
        lines += [f"    {row(item)}" for item in members]
        lines.append("")
    return lines


# discord.py REST methods a bot can never drive, so they are deliberately never
# implemented rather than left as "not yet" gaps — distinguishing intent from
# backlog keeps the gap count honest. Kept deliberately small: only operations
# genuinely impossible for a bot, not merely unbuilt ones.
#
# * ``start_group`` — creating a group DM is a user-account-only action; bots
#   cannot open group DMs at all.
OUT_OF_SCOPE = frozenset({"start_group"})

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
        except (OSError, TypeError):  # pragma: no cover - only when discord.py ships without source
            # discord.py installed without source (zipped/compiled): no route to
            # read. test_route_extraction_is_healthy guards against this silently
            # gutting the gap list.
            continue
        found = _ROUTE_LITERAL.findall(source)
        if found:
            out[name] = [(verb, _normalize(path)) for verb, path in found]
    return out


def unimplemented_routes() -> list[tuple[str, str, str]]:
    """The discord.py REST routes simcord does not serve: (method, verb, path).

    Excludes :data:`OUT_OF_SCOPE` methods, which are reported separately as
    deliberate non-goals rather than backlog.
    """
    implemented = {(verb, _normalize(path)) for verb, path in implemented_routes()}
    gaps: set[tuple[str, str, str]] = set()
    for name, built in discordpy_rest_routes().items():
        if name in OUT_OF_SCOPE:
            continue
        for verb, path in built:
            if (verb, path) not in implemented:
                gaps.add((name, verb, path))
    return sorted(gaps, key=lambda g: (g[2], _METHOD_ORDER.get(g[1], 9), g[0]))


def out_of_scope_routes() -> list[tuple[str, str, str]]:
    """The discord.py REST routes simcord deliberately never implements.

    Derived from :data:`OUT_OF_SCOPE` against the live discord.py surface, so a
    name that discord.py renames or drops makes the matrix stale (and the drift
    guard fail) rather than silently lingering.
    """
    built = discordpy_rest_routes()
    out: set[tuple[str, str, str]] = set()
    for name in OUT_OF_SCOPE:
        for verb, path in built.get(name, ()):
            out.add((name, verb, path))
    return sorted(out, key=lambda g: (g[2], _METHOD_ORDER.get(g[1], 9), g[0]))


def routes_section() -> str:
    """The generated markdown section listing every implemented route, by area."""
    routes = implemented_routes()
    lines = [
        BEGIN_MARKER,
        "",
        f"**{len(routes)} routes implemented**, grouped by resource below. Anything else fails "
        "loudly with `RouteNotImplemented`.",
        "",
    ]
    lines += _grouped_tables(
        routes,
        area_of=lambda r: _area(r[1]),
        admonition="success",
        header="| Method | Route |",
        row=lambda r: f"| `{r[0]}` | `{r[1]}` |",
    )
    lines.append(END_MARKER)
    return "\n".join(lines)


def gaps_section() -> str:
    """The generated markdown section listing not-yet-implemented discord.py routes."""
    gaps = unimplemented_routes()
    lines = [
        GAPS_BEGIN_MARKER,
        "",
        f"**{len(gaps)} discord.py REST routes are not yet implemented**, grouped by resource "
        "below; calling one fails loudly with `RouteNotImplemented` (path parameters shown as "
        "`{}`). Open an issue if your bot needs one.",
        "",
    ]
    lines += _grouped_tables(
        gaps,
        area_of=lambda g: _area(g[2]),
        admonition="warning",
        header="| Route | discord.py `HTTPClient` method |",
        row=lambda g: f"| `{g[1]} {g[2]}` | `{g[0]}` |",
    )
    lines.append(GAPS_END_MARKER)
    return "\n".join(lines)


def out_of_scope_section() -> str:
    """The generated markdown section listing deliberate non-goals."""
    routes = out_of_scope_routes()
    lines = [
        OOS_BEGIN_MARKER,
        "",
        f"{len(routes)} discord.py REST route(s) are intentionally out of scope — actions a bot "
        "cannot perform, so they will not be implemented (calling one still fails loudly with "
        "`RouteNotImplemented`).",
        "",
        "| discord.py `HTTPClient` method | Route |",
        "| --- | --- |",
    ]
    lines += [f"| `{name}` | `{verb} {path}` |" for name, verb, path in routes]
    lines += ["", OOS_END_MARKER]
    return "\n".join(lines)


def _replace_section(text: str, begin: str, end: str, section: str) -> str:
    start = text.index(begin)
    stop = text.index(end) + len(end)
    return text[:start] + section + text[stop:]


def update_matrix(path: Path) -> bool:
    """Rewrite every generated section in ``path``; returns True if it changed."""
    text = path.read_text()
    updated = _replace_section(text, BEGIN_MARKER, END_MARKER, routes_section())
    updated = _replace_section(updated, GAPS_BEGIN_MARKER, GAPS_END_MARKER, gaps_section())
    updated = _replace_section(updated, OOS_BEGIN_MARKER, OOS_END_MARKER, out_of_scope_section())
    if updated != text:
        path.write_text(updated)
        return True
    return False


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint, run as a subprocess
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/parity-matrix.md")
    changed = update_matrix(target)
    print(f"{target}: {'updated' if changed else 'already in sync'}")
