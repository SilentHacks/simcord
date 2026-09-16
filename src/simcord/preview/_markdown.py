"""Small, token-only Markdown projection for the preview surface.

The optional parser is imported only when a snapshot is built.  The browser
receives tokens and creates DOM nodes; untrusted HTML is never sent as markup.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

_ALLOWED_PROFILES = {
    "message",
    "text_display",
    "embed_title",
    "embed_description",
    "embed_field",
    "embed_footer",
    "label",
}
_TIMESTAMP = re.compile(r"<t:(-?\d{1,12})(?::([tTdDfFR]))?>")


def _safe_href(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https", "mailto"} or (
        not parsed.netloc and parsed.scheme != "mailto"
    ):
        return None
    return value


def _inline(children: Iterable[Any]) -> list[dict[str, Any]]:
    tokens = list(children)
    result: list[dict[str, Any]] = []
    remaining_spoilers = sum(
        str(getattr(token, "content", "")).count("||")
        for token in tokens
        if getattr(token, "type", "") == "text"
    )
    spoiler_open = False
    link_open = False

    def append_text(content: str) -> None:
        pos = 0
        for match in _TIMESTAMP.finditer(content):
            if match.start() > pos:
                result.append({"type": "text", "content": content[pos : match.start()]})
            result.append({"type": "timestamp", "unix": int(match.group(1)), "style": match.group(2) or "f"})
            pos = match.end()
        if pos < len(content):
            result.append({"type": "text", "content": content[pos:]})

    for token in tokens:  # pragma: no branch - parser token stream
        kind = getattr(token, "type", "")
        if kind in {"text", "code_inline"}:
            content = str(getattr(token, "content", ""))
            if kind == "code_inline":
                result.append({"type": "code", "content": content})
                continue
            parts = content.split("||")
            for index, part in enumerate(parts):
                append_text(part)
                if index == len(parts) - 1:
                    continue
                remaining_spoilers -= 1
                if spoiler_open:
                    result.append({"type": "spoiler_close"})
                    spoiler_open = False
                elif remaining_spoilers:
                    result.append({"type": "spoiler_open"})
                    spoiler_open = True
                else:
                    result.append({"type": "text", "content": "||"})
            if not content:
                result.append({"type": "text", "content": ""})
            continue
        if kind in {"softbreak", "hardbreak"}:
            result.append({"type": "break"})
            continue
        if kind == "link_open":
            attrs = dict(getattr(token, "attrs", None) or ())
            href = _safe_href(str(attrs.get("href", "")))
            link_open = href is not None
            if href:
                result.append({"type": "link_open", "href": href})
            continue
        if kind == "link_close":
            if link_open:
                result.append({"type": "link_close"})
                link_open = False
            continue
        if kind.endswith("_open") or kind.endswith("_close"):  # pragma: no branch
            name = kind.removesuffix("_open").removesuffix("_close")
            if name == "strong" and getattr(token, "markup", "") == "__":
                name = "u"
            result.append({"type": f"{name}_{'open' if kind.endswith('_open') else 'close'}"})
            continue
    return result


def _blocks(tokens: Iterable[Any]) -> list[dict[str, Any]]:
    root: list[dict[str, Any]] = []
    stack: list[list[dict[str, Any]]] = [root]
    for token in tokens:  # pragma: no branch - parser token stream
        kind = getattr(token, "type", "")
        nesting = int(getattr(token, "nesting", 0) or 0)
        if kind == "inline":
            stack[-1].append({"type": "inline", "children": _inline(getattr(token, "children", ()) or ())})
        elif kind in {"code_block", "fence"}:
            stack[-1].append({"type": "code_block", "content": str(getattr(token, "content", ""))})
        elif nesting == 1:
            item = {
                "type": kind.removesuffix("_open"),
                "tag": str(getattr(token, "tag", "") or ""),
                "children": [],
            }
            stack[-1].append(item)
            stack.append(item["children"])
        elif nesting == -1:  # pragma: no branch - balanced parser output
            stack.pop()
    return root


def markdown_tokens(value: Any, profile: str = "message") -> list[dict[str, Any]]:
    """Return safe JSON-like tokens for a supported field profile.

    ``markdown-it-py`` remains optional; a missing extra degrades to plain text
    rather than making the base SimCord install import it.
    """
    text = "" if value is None else str(value)
    if profile not in _ALLOWED_PROFILES:
        profile = "message"
    try:
        from markdown_it import MarkdownIt
    except ImportError:
        return [{"type": "inline", "children": [{"type": "text", "content": text}]}]
    parser = MarkdownIt("default", {"html": False, "linkify": False, "typographer": False})
    parser.disable("image")
    return _blocks(parser.parse(text))


__all__ = ["markdown_tokens"]
