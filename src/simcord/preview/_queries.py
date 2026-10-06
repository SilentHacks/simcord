"""Bounded authorized message and entity queries with signed cursors."""

from __future__ import annotations

import base64
import hashlib
import heapq
import hmac
import json
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from ..backend.access import can_access_channel, can_access_message
from ..backend.errors import BackendError, SetupError
from ..backend.models import EPHEMERAL_FLAG, Message
from ..components import walk_components
from ..enums import ComponentType
from ._component_projection import _annotate_tree
from ._diagnostics import make_diagnostic, valid_command_name
from ._identity import _identity_wire, resolve_identity
from ._markdown import markdown_summary, markdown_tokens
from ._messages import _identity_name, _markdown_context, _message_type_info, _user_allowed

if TYPE_CHECKING:
    from . import Preview
    from ._pages import _Page


QUERY_PAGE_SIZE = 50
_ENTITY_TYPES = {
    int(ComponentType.USER_SELECT): "users",
    int(ComponentType.ROLE_SELECT): "roles",
    int(ComponentType.CHANNEL_SELECT): "channels",
    int(ComponentType.MENTIONABLE_SELECT): "mentionables",
}


class _QueryError(SetupError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _normalize_query(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 128:
        raise _QueryError("query-invalid")
    normalized = unicodedata.normalize("NFC", value.strip()).casefold()
    normalized = unicodedata.normalize("NFC", normalized)
    if len(normalized) > 128:
        raise _QueryError("query-invalid")
    return normalized


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _b64encode(decoded) != value:
        raise ValueError("non-canonical cursor encoding")
    return decoded


def _make_cursor(
    page: _Page,
    scope: str,
    query: str,
    order: str,
    direction: str,
    position: tuple[Any, ...],
) -> str:
    binding = hashlib.sha256(
        json.dumps([scope, query, order], ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    raw = json.dumps(
        {
            "generation": page.generation,
            "binding": binding,
            "direction": direction,
            "position": [str(value) for value in position],
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(page.cursor_secret, raw, hashlib.sha256).digest()
    return f"{_b64encode(raw)}.{_b64encode(signature)}"


def _read_cursor(
    page: _Page,
    cursor: Any,
    scope: str,
    query: str,
    order: str,
    position_length: int,
) -> dict[str, Any] | None:
    if cursor is None:
        return None
    if not isinstance(cursor, str) or len(cursor) > 1024 or cursor.count(".") != 1:
        raise _QueryError("stale-cursor")
    try:
        encoded, encoded_signature = cursor.split(".", 1)
        raw, signature = _b64decode(encoded), _b64decode(encoded_signature)
        expected = hmac.new(page.cursor_secret, raw, hashlib.sha256).digest()
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise _QueryError("stale-cursor") from None
    if (
        not hmac.compare_digest(signature, expected)
        or not isinstance(value, dict)
        or isinstance(value.get("generation"), bool)
        or not isinstance(value.get("generation"), int)
        or value.get("generation") != page.generation
        or value.get("binding")
        != hashlib.sha256(
            json.dumps([scope, query, order], ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        or value.get("direction") not in {"before", "after"}
        or not isinstance(value.get("position"), list)
        or len(value["position"]) != position_length
        or any(not isinstance(item, str) for item in value["position"])
    ):
        raise _QueryError("stale-cursor")
    return value


def validate_message_query(page: _Page, query: Any, filter_value: Any, cursor: Any) -> tuple[str, str | None]:
    normalized = _normalize_query(query)
    if filter_value != "all" or (cursor is not None and not isinstance(cursor, str)):
        raise _QueryError("query-invalid")
    _read_cursor(page, cursor, "messages:all", normalized, "message-id", 1)
    return normalized, cursor


def _candidate_scope(control_key: str, modal_handle: str | None) -> str:
    return f"candidate:{control_key}:{modal_handle or ''}"


def validate_candidate_query(
    preview: Preview,
    page: _Page,
    control_key: Any,
    modal_handle: Any,
    query: Any,
    cursor: Any,
) -> tuple[str, str | None, str | None]:
    if (
        not isinstance(control_key, str)
        or not control_key
        or len(control_key) > 256
        or (modal_handle is not None and not isinstance(modal_handle, str))
        or (cursor is not None and not isinstance(cursor, str))
    ):
        raise _QueryError("control-unavailable")
    normalized = _normalize_query(query)
    handle = modal_handle
    cursor_state = _read_cursor(
        page,
        cursor,
        _candidate_scope(control_key, handle),
        normalized,
        "candidate-label-id",
        1,
    )
    component, handle = candidate_control(preview, page, control_key, handle)
    if cursor_state is not None:
        entity_id = cursor_state["position"][0]
        kind = _ENTITY_TYPES[int(component["type"])]
        if not any(
            item["id"] == entity_id
            and (not normalized or normalized in _normal_text(item["label"]) or normalized in item["id"])
            for item in _candidate_source(preview, page, component, kind)
        ):
            raise _QueryError("stale-cursor")
    return normalized, cursor, handle


def _normal_text(value: Any) -> str:
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", str(value)).casefold())


def _message_summary_parts(preview: Preview, page: _Page, message: Message, channel: Any) -> dict[str, Any]:
    author_name = _identity_name(
        preview, page, message.author_id, message=message, override=message.author_name
    )
    context = _markdown_context(preview, page, message, channel)
    type_info = _message_type_info(int(message.type))
    excerpt = ""
    if type_info["kind"] not in {"system", "unknown"} and message.content:
        excerpt = markdown_summary(markdown_tokens(message.content, "message", context=context))
    component_summaries: list[dict[str, str]] = []
    text_display_tokens: list[dict[str, Any]] = []
    for component in walk_components(message.components):
        try:
            component_type = int(component.get("type", -1))
            name = ComponentType(component_type).name.lower()
        except (TypeError, ValueError):
            component_type = -1
            name = "unknown"
        raw_label = component.get("label") or component.get("placeholder") or ""
        component_tokens = None
        if component_type == int(ComponentType.TEXT_DISPLAY):
            raw_label = component.get("content", "")
            if isinstance(raw_label, str):
                component_tokens = markdown_tokens(raw_label, "text_display", context=context)
                if text_display_tokens:
                    text_display_tokens.append({"type": "break"})
                text_display_tokens.extend(component_tokens)
        label = (
            markdown_summary(
                component_tokens if component_tokens is not None else markdown_tokens(raw_label, "label"),
                80,
            )
            if isinstance(raw_label, str)
            else ""
        )
        component_summaries.append({"kind": name, "label": label})
        if len(component_summaries) >= 50:
            break
    if not excerpt and text_display_tokens:
        excerpt = markdown_summary(text_display_tokens)

    content_kinds: list[str] = []
    if message.content and type_info["kind"] not in {"system", "unknown"}:
        content_kinds.append("text")
    if message.embeds:
        content_kinds.append("embeds")
    if message.components:
        content_kinds.append("components")
    if message.attachments:
        content_kinds.append("attachments")
    if message.stickers:
        content_kinds.append("stickers")
    if message.poll is not None:
        content_kinds.append("poll")
    thread = preview.env.backend.channels.get(message.id)
    if (
        thread is not None
        and thread.is_thread
        and thread.parent_id == message.channel_id
        and can_access_channel(preview.env, thread.id, page.viewer, history=True)
    ):
        content_kinds.append("thread")
    if message.flags & EPHEMERAL_FLAG:
        content_kinds.append("ephemeral")

    attachment_kinds = set()
    for attachment in message.attachments:
        content_type = str(attachment.get("content_type") or "").lower()
        attachment_kinds.add(
            content_type.split("/", 1)[0]
            if content_type.startswith(("image/", "video/", "audio/"))
            else "file"
        )
    return {
        "author_name": author_name,
        "excerpt": excerpt,
        "contentKinds": content_kinds,
        "components": component_summaries,
        "attachments": {"count": len(message.attachments), "kinds": sorted(attachment_kinds)},
        "createdAt": message.timestamp,
        "editedAt": message.edited_timestamp,
        "ephemeral": bool(message.flags & EPHEMERAL_FLAG),
    }


def _message_summary(
    preview: Preview,
    page: _Page,
    message: Message,
    parts: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "author": _identity_wire(
            resolve_identity(preview, page, message.author_id, message=message, override=message.author_name)
        ),
        "createdAt": parts["createdAt"],
        "editedAt": parts["editedAt"],
        "excerpt": parts["excerpt"],
        "contentKinds": parts["contentKinds"],
        "components": parts["components"],
        "attachments": parts["attachments"],
        "ephemeral": parts["ephemeral"],
    }


def _summary_matches(parts: Mapping[str, Any], message: Message, query: str) -> bool:
    if not query:
        return True
    fields = [
        parts["author_name"],
        parts["excerpt"],
        *parts["contentKinds"],
        parts["createdAt"] or "",
        parts["editedAt"] or "",
        *[item["kind"] for item in parts["components"]],
        *[item["label"] for item in parts["components"]],
        *parts["attachments"]["kinds"],
    ]
    return any(query in _normal_text(value) for value in fields)


def _paginate(
    page: _Page,
    scope: str,
    query: str,
    order: str,
    source: Callable[[], Iterable[Any]],
    key: Callable[[Any], tuple[Any, ...]],
    cursor: Any,
    *,
    position_length: int,
    position_parser: Callable[[list[str]], Any],
    resolve_position: Callable[[Any, Iterable[Any]], tuple[Any, ...] | None] | None = None,
    cursor_position: Callable[[Any], tuple[Any, ...]] | None = None,
) -> tuple[list[Any], bool, bool, str | None, str | None]:
    cursor_state = _read_cursor(page, cursor, scope, query, order, position_length)
    direction = cursor_state["direction"] if cursor_state is not None else None
    pivot = position_parser(cursor_state["position"]) if cursor_state is not None else None
    if cursor_state is not None and resolve_position is not None:
        pivot = resolve_position(pivot, source())
        if pivot is None:
            raise _QueryError("stale-cursor")
    has_previous = False
    has_next = False
    if direction == "after":

        def after_pivot() -> Iterable[Any]:
            nonlocal has_previous
            assert pivot is not None
            for item in source():
                if key(item) <= pivot:
                    has_previous = True
                else:
                    yield item

        matches = heapq.nsmallest(QUERY_PAGE_SIZE + 1, after_pivot(), key=key)
        rows = matches[:QUERY_PAGE_SIZE]
        has_next = len(matches) > QUERY_PAGE_SIZE
    elif direction == "before":

        def before_pivot() -> Iterable[Any]:
            nonlocal has_next
            assert pivot is not None
            for item in source():
                if key(item) >= pivot:
                    has_next = True
                else:
                    yield item

        matches = heapq.nlargest(QUERY_PAGE_SIZE + 1, before_pivot(), key=key)
        rows = list(reversed(matches[:QUERY_PAGE_SIZE]))
        has_previous = len(matches) > QUERY_PAGE_SIZE
    else:
        matches = heapq.nsmallest(QUERY_PAGE_SIZE + 1, source(), key=key)
        rows = matches[:QUERY_PAGE_SIZE]
        has_next = len(matches) > QUERY_PAGE_SIZE
    if not rows:
        return [], False, False, None, None
    previous = (
        _make_cursor(
            page, scope, query, order, "before", cursor_position(rows[0]) if cursor_position else key(rows[0])
        )
        if has_previous
        else None
    )
    following = (
        _make_cursor(
            page,
            scope,
            query,
            order,
            "after",
            cursor_position(rows[-1]) if cursor_position else key(rows[-1]),
        )
        if has_next
        else None
    )
    return rows, has_previous, has_next, previous, following


def _message_position(value: list[str]) -> tuple[int]:
    return (int(value[0]),)


def _message_navigation(
    preview: Preview,
    page: _Page,
    visible: list[Message],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not can_access_channel(preview.env, page.channel_id, page.viewer, history=True):
        return [], {
            "query": "",
            "filter": "all",
            "hasPrevious": False,
            "hasNext": False,
            "previousCursor": None,
            "nextCursor": None,
        }
    query = page.navigation_query
    cursor = page.navigation_cursor
    if query.isascii() and query.isdecimal() and len(query) <= 20:
        try:
            message = preview.env.backend.get_message(page.channel_id, int(query))
        except (BackendError, ValueError):
            message = None
        if (
            message is not None
            and str(message.id) == query
            and can_access_message(preview.env, page.channel_id, message, page.viewer, history=True)
        ):
            parts = _message_summary_parts(
                preview, page, message, preview.env.backend.get_channel(page.channel_id)
            )
            rows = [_message_summary(preview, page, message, parts)]
        else:
            rows = []
        return rows, {
            "query": query,
            "filter": "all",
            "hasPrevious": False,
            "hasNext": False,
            "previousCursor": None,
            "nextCursor": None,
        }

    channel = preview.env.backend.get_channel(page.channel_id)

    def source() -> Iterable[dict[str, Any]]:
        for message in visible:
            parts = _message_summary_parts(preview, page, message, channel)
            if _summary_matches(parts, message, query):
                yield {"message": message, "parts": parts}

    records, has_previous, has_next, previous, following = _paginate(
        page,
        "messages:all",
        query,
        "message-id",
        source,
        lambda item: (item["message"].id,),
        cursor,
        position_length=1,
        position_parser=_message_position,
    )
    return (
        [_message_summary(preview, page, item["message"], item["parts"]) for item in records],
        {
            "query": query,
            "filter": "all",
            "hasPrevious": has_previous,
            "hasNext": has_next,
            "previousCursor": previous,
            "nextCursor": following,
        },
    )


def _candidate_filter(component: Mapping[str, Any]) -> dict[str, list[int]] | None:
    values = component.get("channel_types")
    if not isinstance(values, list) or not values:
        return None
    return {
        "channelTypes": [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
    }


def _candidate_source(
    preview: Preview, page: _Page, component: Mapping[str, Any], kind: str
) -> Iterable[dict[str, Any]]:
    env = preview.env
    channel = env.backend.get_channel(page.channel_id)
    if channel.guild_id is None:
        if kind in {"users", "mentionables"}:
            for user_id in channel.recipient_ids:
                if _user_allowed(preview, page, user_id):
                    label = _identity_name(preview, page, user_id)
                    yield {"id": str(user_id), "label": label, "kind": "user"}
        return
    guild = env.backend.guilds.get(channel.guild_id)
    if guild is None:
        return
    if kind in {"users", "mentionables"}:
        for user_id in guild.members:
            if _user_allowed(preview, page, user_id):
                yield {
                    "id": str(user_id),
                    "label": _identity_name(preview, page, user_id),
                    "kind": "user",
                }
    if kind in {"roles", "mentionables"}:
        for role_id, role in guild.roles.items():
            if role_id != guild.id:
                yield {"id": str(role_id), "label": role.name, "kind": "role"}
    if kind == "channels":
        allowed_types = component.get("channel_types")
        for candidate in env.backend.channels.values():
            if (
                candidate.guild_id == channel.guild_id
                and can_access_channel(env, candidate.id, page.viewer)
                and (
                    not isinstance(allowed_types, list)
                    or not allowed_types
                    or candidate.type in allowed_types
                )
            ):
                yield {
                    "id": str(candidate.id),
                    "label": candidate.name or str(candidate.id),
                    "kind": "channel",
                    "type": int(candidate.type),
                }


def _candidate_key(item: Mapping[str, Any]) -> tuple[str, str]:
    return (_normal_text(item["label"]), str(item["id"]))


def _candidate_position(value: list[str]) -> str:
    return value[0]


def _candidate_wire(
    preview: Preview, page: _Page, component: Mapping[str, Any], item: Mapping[str, Any]
) -> dict[str, Any]:
    kind = item["kind"]
    entity_id = int(item["id"])
    if kind == "user":
        identity = _identity_wire(resolve_identity(preview, page, entity_id))
        user = preview.env.backend.get_user(entity_id)
        username = f"{user.name}#{user.discriminator}" if user.discriminator not in ("0", "") else user.name
        return {**identity, "label": identity["name"], "username": username, "kind": "user"}
    if kind == "role":
        channel = preview.env.backend.get_channel(page.channel_id)
        if channel.guild_id is None:
            raise SetupError("role is unavailable")
        guild = preview.env.backend.guilds[channel.guild_id]
        role = guild.roles[entity_id]
        return {
            "id": str(entity_id),
            "name": role.name,
            "label": role.name,
            "kind": "role",
            "color": int(role.color or 0),
            "icon_color": int(role.color or 0),
            "members": sum(1 for member in guild.members.values() if entity_id in member.role_ids),
        }
    return {
        "id": str(entity_id),
        "name": item["label"],
        "label": item["label"],
        "kind": "channel",
        "type": item["type"],
    }


def _candidate_defaults(
    preview: Preview, page: _Page, component: Mapping[str, Any], kind: str
) -> list[dict[str, Any]]:
    defaults = component.get("default_values")
    if not isinstance(defaults, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in defaults[:25]:
        entity_id = value.get("id") if isinstance(value, Mapping) else value
        declared_kind = value.get("type") if isinstance(value, Mapping) else None
        if isinstance(entity_id, bool) or not isinstance(entity_id, (str, int)):
            continue
        entity_id = str(entity_id)
        if not entity_id.isascii() or not entity_id.isdecimal() or entity_id in seen:
            continue
        for item in _candidate_source(preview, page, component, kind):
            if item["id"] != entity_id or (declared_kind is not None and declared_kind != item["kind"]):
                continue
            result.append(_candidate_wire(preview, page, component, item))
            seen.add(entity_id)
            break
    return result


def _candidate_descriptor(
    preview: Preview,
    page: _Page,
    component: Mapping[str, Any],
    control_key: str,
    *,
    modal_handle: str | None,
) -> dict[str, Any]:
    kind = _ENTITY_TYPES.get(int(component.get("type", -1)))
    if kind is None:
        return {}
    query_state = page.candidate_queries.get(control_key)
    if query_state is not None and query_state.get("modal_handle") != modal_handle:
        query_state = None
    query = query_state.get("query", "") if query_state else ""
    cursor = query_state.get("cursor") if query_state else None
    scope = _candidate_scope(control_key, modal_handle)

    def source() -> Iterable[dict[str, Any]]:
        for item in _candidate_source(preview, page, component, kind):
            if not query or query in _normal_text(item["label"]) or query in item["id"]:
                yield item

    rows, has_previous, has_next, previous, following = _paginate(
        page,
        scope,
        query,
        "candidate-label-id",
        source,
        _candidate_key,
        cursor,
        position_length=1,
        position_parser=_candidate_position,
        resolve_position=lambda entity_id, items: next(
            (_candidate_key(item) for item in items if item["id"] == entity_id), None
        ),
        cursor_position=lambda item: (str(item["id"]),),
    )
    entries = [_candidate_wire(preview, page, component, item) for item in rows]
    return {
        "type": kind,
        "filter": _candidate_filter(component),
        "selected": _candidate_defaults(
            preview,
            page,
            {**component, "default_values": query_state["selected_values"]}
            if query_state and query_state.get("selected_values") is not None
            else component,
            kind,
        ),
        "entries": entries,
        "state": "available" if entries else "empty",
        "query": query,
        "hasPrevious": has_previous,
        "hasNext": has_next,
        "previousCursor": previous,
        "nextCursor": following,
    }


def _candidates(
    preview: Preview,
    page: _Page,
    components: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for component in walk_components(components):
        try:
            kind = _ENTITY_TYPES.get(int(component.get("type", -1)))
        except (TypeError, ValueError):
            kind = None
        control_key = component.get("control_key")
        if kind is None or not isinstance(control_key, str):
            continue
        modal_handle = (
            page.modal_handle
            if page.modal_handle is not None and control_key.startswith(f"modal:{page.modal_handle}:")
            else None
        )
        try:
            result[control_key] = _candidate_descriptor(
                preview, page, component, control_key, modal_handle=modal_handle
            )
        except _QueryError as exc:
            if exc.code != "stale-cursor":
                raise
            page.candidate_queries[control_key]["cursor"] = None
            result[control_key] = _candidate_descriptor(
                preview, page, component, control_key, modal_handle=modal_handle
            )
            diagnostics.append(
                make_diagnostic(
                    "stale-cursor",
                    state="recovered",
                    subject={"messageId": control_key.split(":", 2)[1], "controlKey": control_key}
                    if modal_handle is None
                    else None,
                )
            )
        component.pop("default_values", None)
    for control_key in tuple(page.candidate_queries):
        if not control_key.startswith("command:"):
            continue
        try:
            component, modal_handle = candidate_control(preview, page, control_key, None)
            result[control_key] = _candidate_descriptor(
                preview, page, component, control_key, modal_handle=modal_handle
            )
        except _QueryError as exc:
            if exc.code == "stale-cursor":
                page.candidate_queries[control_key]["cursor"] = None
                component, modal_handle = candidate_control(preview, page, control_key, None)
                result[control_key] = _candidate_descriptor(
                    preview, page, component, control_key, modal_handle=modal_handle
                )
                diagnostics.append(make_diagnostic("stale-cursor", state="recovered"))
            else:
                diagnostics.append(make_diagnostic("command-unavailable", state="recovered"))
    page.candidate_queries = {key: value for key, value in page.candidate_queries.items() if key in result}
    return result


def candidate_control(
    preview: Preview, page: _Page, control_key: str, modal_handle: str | None
) -> tuple[dict[str, Any], str | None]:
    if not can_access_channel(preview.env, page.channel_id, page.viewer, history=True):
        raise _QueryError("control-unavailable")
    if control_key.startswith("command:"):
        parts = control_key.split(":")
        if (
            len(parts) != 5
            or parts[0] != "command"
            or parts[3] != "option"
            or not re.fullmatch(r"[0-9]{1,20}", parts[1])
            or not re.fullmatch(r"[^.]+(?:\.[^.]+)*", parts[2])
            or not valid_command_name(parts[4])
            or any(not valid_command_name(segment) for segment in parts[2].split("."))
            or modal_handle is not None
            or page.modal is not None
            or str(int(parts[1])) != parts[1]
            or page.layout != "channel"
            or page.status != "current"
        ):
            raise _QueryError("control-unavailable")
        command_id, dotted_path, option_name = parts[1], parts[2], parts[4]
        entry_key = f"{command_id}:{dotted_path}"
        entries = page.command_catalog.get("entries", [])
        catalog_entry = next(
            (entry for entry in entries if isinstance(entry, Mapping) and entry.get("key") == entry_key),
            None,
        )
        if catalog_entry is None:
            raise _QueryError("control-unavailable")
        from ._commands import entry_for_leaf, visible_leaf

        resolved = visible_leaf(preview, page, command_id, dotted_path.split("."))
        if resolved is None:
            raise _QueryError("control-unavailable")
        root, path, leaf = resolved
        if entry_for_leaf(root, path, leaf)["schemaFingerprint"] != catalog_entry.get("schemaFingerprint"):
            raise _QueryError("control-unavailable")
        option = next(
            (
                item
                for item in leaf.get("options") or []
                if item.get("name") == option_name and int(item.get("type", -1)) in {6, 7, 8, 9}
            ),
            None,
        )
        if option is None:
            raise _QueryError("control-unavailable")
        option_type = int(option["type"])
        component_type = {6: 5, 8: 6, 9: 7, 7: 8}[option_type]
        component: dict[str, Any] = {"type": component_type}
        if option_type == 7 and option.get("channel_types"):
            component["channel_types"] = list(option["channel_types"])
        return component, None
    roots: Any
    scope: str
    if modal_handle is not None:
        if page.modal is None or modal_handle != page.modal_handle:
            raise _QueryError("control-unavailable")
        payload = deepcopy(page.modal.modal or {})
        roots = payload.get("components", [])
        _annotate_tree(roots, f"modal:{modal_handle}")
        scope = "modal"
    else:
        if page.modal is not None:
            raise _QueryError("control-unavailable")
        match = re.fullmatch(r"message:([0-9]{1,20}):component:.{1,200}", control_key)
        if match is None or str(int(match.group(1))) not in page.snapshot.get("messages", {}):
            raise _QueryError("control-unavailable")
        message_id = int(match.group(1))
        try:
            message = preview.env.backend.get_message(page.channel_id, message_id)
        except BackendError:
            raise _QueryError("control-unavailable") from None
        if not can_access_message(preview.env, page.channel_id, message, page.viewer, history=True):
            raise _QueryError("control-unavailable")
        roots = deepcopy(message.components)
        _annotate_tree(roots, f"message:{message.id}")
        scope = "message"
    for component in walk_components(roots):
        if component.get("control_key") == control_key:
            try:
                valid = int(component.get("type", -1)) in _ENTITY_TYPES
            except (TypeError, ValueError):
                valid = False
            if valid and not component.get("disabled"):
                return component, modal_handle if scope == "modal" else None
    raise _QueryError("control-unavailable")
