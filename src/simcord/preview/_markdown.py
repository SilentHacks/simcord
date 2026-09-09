"""Small, token-only Markdown projection for the preview surface.

The optional parser is imported only when a snapshot is built.  The browser
receives tokens and creates DOM nodes; untrusted HTML is never sent as markup.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

_ALLOWED_PROFILES = {"message", "text_display", "embed_title", "embed_description", "embed_field", "embed_footer", "label"}
_TIMESTAMP = re.compile(r"<t:(-?\d{1,12})(?::([tTdDfFR]))?>")
_SPOILER = re.compile(r"\|\|([^|]*(?:\|[^|]+)*)\|\|")


def _safe_href(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https", "mailto"} or (not parsed.netloc and parsed.scheme != "mailto"):
        return None
    return value


def _inline(children: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for token in children:
        kind = getattr(token, "type", "")
        if kind in {"html_inline", "html_block", "image"}:
            # html=False normally turns HTML into text; keep this guard for
            # parser/plugin changes and never create an HTML-bearing token.
            continue
        if kind in {"text", "code_inline"}:
            content = str(getattr(token, "content", ""))
            if kind == "code_inline":
                result.append({"type": "code", "content": content})
                continue
            pos = 0
            for match in _TIMESTAMP.finditer(content):
                if match.start() > pos:
                    result.append({"type": "text", "content": content[pos : match.start()]})
                result.append({"type": "timestamp", "unix": int(match.group(1)), "style": match.group(2) or "f"})
                pos = match.end()
            if pos < len(content):
                result.append({"type": "text", "content": content[pos:]})
            if not content and kind == "text":
                result.append({"type": "text", "content": ""})
            continue
        if kind in {"softbreak", "hardbreak"}:
            result.append({"type": "break"})
            continue
        if kind.endswith("_open") or kind.endswith("_close"):
            name = kind.removesuffix("_open").removesuffix("_close")
            if name == "s" and getattr(token, "markup", "") == "__":
                name = "u"
            result.append({"type": f"{name}_{'open' if kind.endswith('_open') else 'close'}"})
            continue
        if kind == "link_open":
            attrs = dict(getattr(token, "attrs", None) or ())
            href = _safe_href(str(attrs.get("href", "")))
            if href:
                result.append({"type": "link_open", "href": href})
            else:
                result.append({"type": "text", "content": ""})
            continue
        if kind == "link_close":
            result.append({"type": "link_close"})
            continue
    return result


def _blocks(tokens: Iterable[Any]) -> list[dict[str, Any]]:
    root: list[dict[str, Any]] = []
    stack: list[list[dict[str, Any]]] = [root]
    for token in tokens:
        kind = getattr(token, "type", "")
        nesting = int(getattr(token, "nesting", 0) or 0)
        if kind in {"html_block", "html_inline"}:
            continue
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
        elif nesting == -1:
            if len(stack) > 1:
                stack.pop()
        elif kind == "text":
            stack[-1].append({"type": "text", "content": str(getattr(token, "content", ""))})
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
