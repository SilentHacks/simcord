"""Field-aware, token-only Discord text projection for Preview."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlparse

# These policies intentionally differ by field. Embed author/footer and component
# labels are plain Discord labels; only message-like bodies accept block syntax
# and semantic references. Embed titles/names stay inline and cannot link out.
_FIELD_POLICIES: dict[str, dict[str, bool | str]] = {
    "message": {
        "mode": "blocks",
        "links": True,
        "spoilers": True,
        "mentions": True,
        "emoji": True,
        "commands": True,
        "timestamps": True,
        "subtext": True,
    },
    "text_display": {
        "mode": "blocks",
        "links": True,
        "spoilers": True,
        "mentions": True,
        "emoji": True,
        "commands": True,
        "timestamps": True,
        "subtext": True,
    },
    "embed_title": {
        "mode": "inline",
        "links": False,
        "spoilers": True,
        "mentions": False,
        "emoji": True,
        "commands": False,
        "timestamps": True,
        "subtext": False,
    },
    "embed_description": {
        "mode": "blocks",
        "links": True,
        "spoilers": True,
        "mentions": False,
        "emoji": True,
        "commands": False,
        "timestamps": True,
        "subtext": False,
    },
    "embed_field_name": {
        "mode": "inline",
        "links": False,
        "spoilers": True,
        "mentions": False,
        "emoji": True,
        "commands": False,
        "timestamps": True,
        "subtext": False,
    },
    "embed_field_value": {
        "mode": "blocks",
        "links": True,
        "spoilers": True,
        "mentions": False,
        "emoji": True,
        "commands": False,
        "timestamps": True,
        "subtext": False,
    },
    "embed_footer": {
        "mode": "plain",
        "links": False,
        "spoilers": False,
        "mentions": False,
        "emoji": False,
        "commands": False,
        "timestamps": False,
        "subtext": False,
    },
    "label": {
        "mode": "plain",
        "links": False,
        "spoilers": False,
        "mentions": False,
        "emoji": False,
        "commands": False,
        "timestamps": False,
        "subtext": False,
    },
    "description": {
        "mode": "plain",
        "links": False,
        "spoilers": False,
        "mentions": False,
        "emoji": False,
        "commands": False,
        "timestamps": False,
        "subtext": False,
    },
    "system": {
        "mode": "plain",
        "links": False,
        "spoilers": False,
        "mentions": False,
        "emoji": False,
        "commands": False,
        "timestamps": False,
        "subtext": False,
    },
}
_TIMESTAMP = re.compile(r"<t:(-?\d{1,20})(?::([tTdDfFR]))?>")
_CUSTOM_EMOJI = re.compile(r"<(a?):([A-Za-z0-9_]{2,32}):(\d{1,20})>")
_USER_MENTION = re.compile(r"<@!?([0-9]{1,20})>")
_ROLE_MENTION = re.compile(r"<@&([0-9]{1,20})>")
_CHANNEL_MENTION = re.compile(r"<#([0-9]{1,20})>")
_COMMAND_MENTION = re.compile(r"</([^:\n]{1,100}):(\d{1,20})>")
_EVERYONE_MENTION = re.compile(r"@(everyone|here)\b")


def _safe_href(value: str) -> str | None:
    if not value or any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value):
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https", "mailto"} or (
        (scheme in {"http", "https"} and not parsed.netloc) or (scheme == "mailto" and not parsed.path)
    ):
        return None
    return value


def _text_token(value: str) -> dict[str, str]:
    return {"type": "text", "content": value}


def _append_token(token: Any) -> dict[str, Any] | None:
    kind = getattr(token, "type", "")
    meta = getattr(token, "meta", None) or {}
    if kind == "discord_timestamp":
        return {"type": "timestamp", "unix": meta["unix"], "style": meta["style"]}
    if kind == "discord_emoji":
        return {"type": "emoji", "emoji": meta["emoji"]}
    if kind == "discord_mention":
        return {"type": "mention", "kind": meta["kind"], "id": meta["id"], "label": meta["label"]}
    if kind == "discord_command":
        return {"type": "command", "id": meta["id"], "label": meta["label"]}
    return None


def _markdown_parser(policy: Mapping[str, bool | str], context: Mapping[str, Any]) -> Any:
    from markdown_it import MarkdownIt

    parser = MarkdownIt("default", {"html": False, "linkify": False, "typographer": False})
    parser.disable("image")
    if not policy["links"]:
        parser.disable(["link", "autolink"])
    if policy["spoilers"]:
        parser.inline.add_terminator_char("|")

    def spoiler_rule(state: Any, silent: bool) -> bool:
        if not policy["spoilers"] or state.src[state.pos : state.pos + 2] != "||":
            return False
        end = state.src.find("||", state.pos + 2)
        while end >= 0:
            escapes = 0
            cursor = end - 1
            while cursor >= state.pos and state.src[cursor] == "\\":
                escapes += 1
                cursor -= 1
            if escapes % 2 == 0:
                break
            end = state.src.find("||", end + 2)
        if end < 0:
            return False
        if not silent:
            token = state.push("discord_spoiler", "span", 0)
            token.children = state.md.inline.parse(state.src[state.pos + 2 : end], state.md, state.env, [])
        state.pos = end + 2
        return True

    def semantic_rule(state: Any, silent: bool) -> bool:
        if state.src[state.pos] not in "<@":
            return False
        match = _TIMESTAMP.match(state.src, state.pos) if policy["timestamps"] else None
        if match:
            if not silent:
                token = state.push("discord_timestamp", "", 0)
                token.meta = {"unix": int(match.group(1)), "style": match.group(2) or "f"}
            state.pos = match.end()
            return True

        if policy["emoji"] and (match := _CUSTOM_EMOJI.match(state.src, state.pos)):
            emoji = (context.get("emojis") or {}).get(match.group(3))
            if (
                isinstance(emoji, Mapping)
                and emoji.get("name") == match.group(2)
                and bool(emoji.get("animated")) == bool(match.group(1))
            ):
                if not silent:
                    token = state.push("discord_emoji", "", 0)
                    token.meta = {"emoji": dict(emoji)}
                state.pos = match.end()
                return True

        if policy["mentions"]:
            for pattern, collection, prefix, kind in (
                (_USER_MENTION, "users", "@", "user"),
                (_ROLE_MENTION, "roles", "@", "role"),
                (_CHANNEL_MENTION, "channels", "#", "channel"),
            ):
                if match := pattern.match(state.src, state.pos):
                    name = (context.get(collection) or {}).get(match.group(1))
                    if isinstance(name, str):
                        if not silent:
                            token = state.push("discord_mention", "", 0)
                            token.meta = {"kind": kind, "id": match.group(1), "label": f"{prefix}{name}"}
                        state.pos = match.end()
                        return True
            if match := _EVERYONE_MENTION.match(state.src, state.pos):
                if context.get("everyone"):
                    if not silent:
                        token = state.push("discord_mention", "", 0)
                        token.meta = {"kind": "everyone", "id": "", "label": match.group(0)}
                    state.pos = match.end()
                    return True

        if policy["commands"] and (match := _COMMAND_MENTION.match(state.src, state.pos)):
            command_name = (context.get("commands") or {}).get(match.group(2))
            path = match.group(1).strip()
            if isinstance(command_name, str) and path.split(" ", 1)[0] == command_name:
                if not silent:
                    token = state.push("discord_command", "", 0)
                    token.meta = {"id": match.group(2), "label": f"/{path}"}
                state.pos = match.end()
                return True
        return False

    # Code spans are parsed before entering their contents; escaped punctuation
    # is consumed before the next position can be recognized as a reference.
    parser.inline.ruler.before("text", "discord_spoiler", spoiler_rule)
    parser.inline.ruler.before("text", "discord_semantic", semantic_rule)
    return parser


def _inline(children: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    link_open = False
    for token in children:
        kind = getattr(token, "type", "")
        if kind in {"text", "html_inline"}:
            result.append(_text_token(str(getattr(token, "content", ""))))
            continue
        if kind == "code_inline":
            result.append({"type": "code", "content": str(getattr(token, "content", ""))})
            continue
        if kind == "discord_spoiler":
            result.append({"type": "spoiler_open"})
            result.extend(_inline(getattr(token, "children", ()) or ()))
            result.append({"type": "spoiler_close"})
            continue
        if semantic := _append_token(token):
            result.append(semantic)
            continue
        if kind in {"softbreak", "hardbreak"}:
            result.append({"type": "break"})
            continue
        if kind == "link_open":
            attrs = dict(getattr(token, "attrs", None) or ())
            href = _safe_href(str(attrs.get("href", "")))
            link_open = href is not None
            if href is not None:
                result.append({"type": "link_open", "href": href})
            continue
        if kind == "link_close":
            if link_open:
                result.append({"type": "link_close"})
                link_open = False
            continue
        if kind.endswith("_open") or kind.endswith("_close"):
            name = kind.removesuffix("_open").removesuffix("_close")
            if name == "strong" and getattr(token, "markup", "") == "__":
                name = "u"
            result.append({"type": f"{name}_{'open' if kind.endswith('_open') else 'close'}"})
    return result


def _blocks(tokens: Iterable[Any], policy: Mapping[str, bool | str]) -> list[dict[str, Any]]:
    root: list[dict[str, Any]] = []
    stack: list[list[dict[str, Any]]] = [root]
    for token in tokens:
        kind = getattr(token, "type", "")
        nesting = int(getattr(token, "nesting", 0) or 0)
        if kind == "inline":
            children = list(getattr(token, "children", ()) or ())
            content = str(getattr(token, "content", ""))
            subtext = bool(policy["subtext"]) and content.startswith("-# ")
            if subtext and children and getattr(children[0], "type", "") == "text":
                children[0].content = str(children[0].content)[3:]
            stack[-1].append({"type": "subtext" if subtext else "inline", "children": _inline(children)})
        elif kind in {"code_block", "fence"}:
            info = str(getattr(token, "info", "") or "").strip()
            block: dict[str, Any] = {"type": "code_block", "content": str(getattr(token, "content", ""))}
            if info:
                block["language"] = info.split(maxsplit=1)[0]
                block["info"] = info
            stack[-1].append(block)
        elif kind == "html_block":
            stack[-1].append(
                {"type": "inline", "children": [_text_token(str(getattr(token, "content", "")))]}
            )
        elif nesting == 1:
            item = {
                "type": kind.removesuffix("_open"),
                "tag": str(getattr(token, "tag", "") or ""),
                "children": [],
            }
            stack[-1].append(item)
            stack.append(item["children"])
        elif nesting == -1:
            stack.pop()
    return root


def _discord_multiline_quotes(text: str) -> str:
    """Expand Discord's quote-rest-of-message marker without rewriting code fences."""
    output: list[str] = []
    fence: tuple[str, int] | None = None
    quoted = False
    for line in text.splitlines(keepends=True):
        if quoted:
            indent = len(line) - len(line.lstrip(" "))
            output.append(f"{line[:indent]}> {line[indent:]}")
            continue
        if fence is not None:
            output.append(line)
            close = re.match(r"^ {0,3}(`{3,}|~{3,})[ \t]*(?:\r?\n)?$", line)
            if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= fence[1]:
                fence = None
            continue
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            fence = (marker.group(1)[0], len(marker.group(1)))
            output.append(line)
            continue
        quote = re.match(r"^( {0,3})>>>(?=[ \t\r\n]|$)[ \t]*(.*?)(\r?\n)?$", line)
        if quote:
            output.append(f"{quote.group(1)}> {quote.group(2)}{quote.group(3) or ''}")
            quoted = True
        else:
            output.append(line)
    return "".join(output)


def markdown_tokens(
    value: Any,
    profile: str = "message",
    *,
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return field-policy-specific safe tokens; references resolve only in context."""
    if value is None:
        return []
    text = str(value)
    policy = _FIELD_POLICIES.get(profile, _FIELD_POLICIES["message"])
    if policy["mode"] == "blocks":
        text = _discord_multiline_quotes(text)
    if policy["mode"] == "plain":
        return [{"type": "inline", "children": [_text_token(text)]}]
    try:
        parser = _markdown_parser(policy, context or {})
    except ImportError:
        return [{"type": "inline", "children": [_text_token(text)]}]
    semantic_context = context or {}
    if policy["mode"] == "inline":
        tokens = parser.parseInline(text, {"context": semantic_context})
    else:
        tokens = parser.parse(text, {"context": semantic_context})
    return _blocks(tokens, policy)


__all__ = ["markdown_tokens"]
