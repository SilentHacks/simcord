"""Wire-format Discord component traversal and message validation.

This module deliberately deals only in JSON-like values.  It is shared by the
backend and the actor helpers so that the component tree seen by either side is
exactly the tree Discord would accept.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from typing import Any

# discord.MessageFlags.components_v2.  Kept numeric to avoid importing discord.py
# (and to keep this module usable by the in-memory backend on its own).
COMPONENTS_V2_FLAG = 1 << 15
_MAX_COMPONENT_ID = (1 << 32) - 1

# Components which Discord permits in a message's V2 layout.  Action rows are
# also valid in a V2 container; the remaining values are the Bot UI Kit types.
_V2_TYPES = {9, 10, 11, 12, 13, 14, 17}
_SELECT_TYPES = {3, 5, 6, 7, 8}
_V2_CONTAINER_CHILDREN = {1, 9, 10, 12, 13, 14}


class ComponentValidationError(ValueError):
    """A component tree cannot be represented by Discord's message API."""


def walk_components(components: Any) -> Iterator[dict[str, Any]]:
    """Yield every component in wire order, including nested V2 children.

    Discord currently uses three nesting keys: ``components`` (arrays),
    ``accessory`` (a section), and ``component`` (a modal label). Walking all
    three here prevents consumers from missing nested interactive components.
    """
    roots = (components,) if isinstance(components, dict) else components
    for component in roots:
        if not isinstance(component, dict):
            continue
        yield component
        children = component.get("components")
        if isinstance(children, list):
            yield from walk_components(children)
        for key in ("accessory", "component"):
            child = component.get(key)
            if isinstance(child, dict):
                yield from walk_components(child)


def _fail(path: str, detail: str) -> ComponentValidationError:
    return ComponentValidationError(f"components.{path}: {detail}")


def _string(value: Any, path: str, *, minimum: int = 0, maximum: int | None = None) -> str:
    if not isinstance(value, str):
        raise _fail(path, "must be a string")
    if len(value) < minimum:
        raise _fail(path, f"must be at least {minimum} character(s)")
    if maximum is not None and len(value) > maximum:
        raise _fail(path, f"must be {maximum} or fewer characters")
    return value


def _component_type(component: Mapping[str, Any], path: str) -> int:
    value = component.get("type")
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(path, "type is required")
    if value not in {1, 2, 3, 4, 5, 6, 7, 8, *range(9, 15), *range(17, 24)}:
        raise _fail(path, f"unsupported type {value}")
    return value


def _check_id(component: Mapping[str, Any], path: str, explicit: set[int]) -> None:
    value = component.get("id")
    if value is None or value == 0:
        return
    if value in explicit:
        raise _fail(path, f"id {value} is not unique")
    explicit.add(value)


def _check_media(value: Any, path: str) -> None:
    if not isinstance(value, Mapping):
        raise _fail(path, "media must be an object")
    _string(value.get("url"), f"{path}.url", minimum=1)


def _check_select(component: Mapping[str, Any], path: str, custom_ids: set[str]) -> None:
    _string(component.get("custom_id"), f"{path}.custom_id", minimum=1, maximum=100)
    if component["custom_id"] in custom_ids:
        raise _fail(path, f"custom_id {component['custom_id']!r} is not unique")
    custom_ids.add(component["custom_id"])
    if "placeholder" in component and component["placeholder"] is not None:
        _string(component["placeholder"], f"{path}.placeholder", maximum=150)
    minimum = component.get("min_values", 1)
    maximum = component.get("max_values", 1)
    if isinstance(minimum, bool) or not isinstance(minimum, int) or not 0 <= minimum <= 25:
        raise _fail(path, "min_values must be between 0 and 25")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 25:
        raise _fail(path, "max_values must be between 1 and 25")
    if minimum > maximum:
        raise _fail(path, "min_values cannot exceed max_values")

    if component["type"] == 3:
        options = component.get("options")
        if not isinstance(options, list) or not 1 <= len(options) <= 25:
            raise _fail(path, "options must contain between 1 and 25 items")
        values: set[str] = set()
        for index, option in enumerate(options):
            option_path = f"{path}.options[{index}]"
            if not isinstance(option, Mapping):
                raise _fail(option_path, "must be an object")
            _string(option.get("label"), f"{option_path}.label", minimum=1, maximum=100)
            value = _string(option.get("value"), f"{option_path}.value", minimum=1, maximum=100)
            if value in values:
                raise _fail(option_path, f"value {value!r} is not unique")
            values.add(value)
            if "description" in option and option["description"] is not None:
                _string(option["description"], f"{option_path}.description", maximum=100)
            if "emoji" in option and option["emoji"] is not None and not isinstance(option["emoji"], Mapping):
                raise _fail(option_path, "emoji must be an object")


def _check_component(
    component: Mapping[str, Any],
    path: str,
    *,
    custom_ids: set[str],
    explicit_ids: set[int],
    text_total: list[int],
) -> None:
    kind = _component_type(component, path)
    _check_id(component, path, explicit_ids)

    if kind == 1:  # action row
        children = component.get("components")
        if not isinstance(children, list) or not 1 <= len(children) <= 5:
            raise _fail(path, "action rows must contain between 1 and 5 components")
        for index, child in enumerate(children):
            if not isinstance(child, Mapping):
                raise _fail(f"{path}.components[{index}]", "must be an object")
            child_kind = _component_type(child, f"{path}.components[{index}]")
            if child_kind not in ({2, *_SELECT_TYPES}):
                raise _fail(path, "action rows may contain only buttons and select menus")
            _check_component(
                child,
                f"{path}.components[{index}]",
                custom_ids=custom_ids,
                explicit_ids=explicit_ids,
                text_total=text_total,
            )
        return

    if kind == 2:  # button
        style = component.get("style")
        if isinstance(style, bool) or not isinstance(style, int) or style not in {1, 2, 3, 4, 5, 6}:
            raise _fail(path, "style must be between 1 and 6")
        if "label" in component and component["label"] is not None:
            _string(component["label"], f"{path}.label", maximum=80)
        if style == 5:  # link button
            _string(component.get("url"), f"{path}.url", minimum=1)
            if "custom_id" in component:
                raise _fail(path, "link buttons cannot have custom_id")
        elif style == 6:  # premium button
            sku_id = component.get("sku_id")
            if isinstance(sku_id, bool) or not isinstance(sku_id, (int, str)) or not str(sku_id):
                raise _fail(path, "premium buttons require sku_id")
            if any(key in component for key in ("custom_id", "url", "label", "emoji")):
                raise _fail(path, "premium buttons cannot have custom_id, url, label, or emoji")
        else:
            _string(component.get("custom_id"), f"{path}.custom_id", minimum=1, maximum=100)
            if component["custom_id"] in custom_ids:
                raise _fail(path, f"custom_id {component['custom_id']!r} is not unique")
            custom_ids.add(component["custom_id"])
        if (
            "emoji" in component
            and component["emoji"] is not None
            and not isinstance(component["emoji"], Mapping)
        ):
            raise _fail(path, "emoji must be an object")
        return

    if kind in _SELECT_TYPES:
        _check_select(component, path, custom_ids)
        return

    if kind == 10:  # text display
        value = _string(component.get("content"), f"{path}.content", minimum=1, maximum=4000)
        text_total[0] += len(value)
        if text_total[0] > 4000:
            raise _fail(path, "text display content exceeds the 4000-character total")
        return

    if kind == 11:  # thumbnail
        _check_media(component.get("media"), f"{path}.media")
        if "description" in component and component["description"] is not None:
            _string(component["description"], f"{path}.description", maximum=256)
        return

    if kind == 9:  # section
        children = component.get("components")
        if not isinstance(children, list) or not 1 <= len(children) <= 3:
            raise _fail(path, "sections must contain between 1 and 3 text displays")
        for index, child in enumerate(children):
            if not isinstance(child, Mapping) or child.get("type") != 10:
                raise _fail(path, "section components must be text displays")
            _check_component(
                child,
                f"{path}.components[{index}]",
                custom_ids=custom_ids,
                explicit_ids=explicit_ids,
                text_total=text_total,
            )
        accessory = component.get("accessory")
        if not isinstance(accessory, Mapping) or accessory.get("type") not in {2, 11}:
            raise _fail(path, "sections require a button or thumbnail accessory")
        _check_component(
            accessory,
            f"{path}.accessory",
            custom_ids=custom_ids,
            explicit_ids=explicit_ids,
            text_total=text_total,
        )
        return

    if kind == 12:  # media gallery
        items = component.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 10:
            raise _fail(path, "media galleries must contain between 1 and 10 items")
        for index, item in enumerate(items):
            item_path = f"{path}.items[{index}]"
            if not isinstance(item, Mapping):
                raise _fail(item_path, "must be an object")
            _check_media(item.get("media"), f"{item_path}.media")
            if "description" in item and item["description"] is not None:
                _string(item["description"], f"{item_path}.description", maximum=256)
        return

    if kind == 13:  # file
        _check_media(component.get("file"), f"{path}.file")
        return

    if kind == 14:  # separator
        spacing = component.get("spacing", 1)
        if spacing not in {1, 2}:
            raise _fail(path, "spacing must be 1 or 2")
        return

    if kind == 17:  # container
        children = component.get("components")
        if not isinstance(children, list):
            raise _fail(path, "container components must be an array")
        for index, child in enumerate(children):
            if not isinstance(child, Mapping) or child.get("type") not in _V2_CONTAINER_CHILDREN:
                raise _fail(path, "container has an unsupported child component")
            _check_component(
                child,
                f"{path}.components[{index}]",
                custom_ids=custom_ids,
                explicit_ids=explicit_ids,
                text_total=text_total,
            )
        return


def _assign_ids(components: list[dict[str, Any]], explicit_ids: set[int]) -> None:
    next_id = 1
    for component in walk_components(components):
        value = component.get("id")
        if value is None or (type(value) is int and value == 0):
            while next_id in explicit_ids:
                next_id += 1
            component["id"] = next_id
            next_id += 1


def validate_components(components: Any, *, flags: int = 0) -> list[dict[str, Any]]:
    """Validate and normalize a message component tree.

    Returned dictionaries are a deep copy.  V2 components receive stable IDs,
    while legacy components retain the historical wire ``id=0``.
    """
    if not isinstance(components, list):
        raise _fail("", "must be an array")
    normalized = deepcopy(components)
    if len(list(walk_components(normalized))) > 40:
        raise _fail("", "messages may contain at most 40 components")
    contains_v2 = any(component.get("type") in _V2_TYPES for component in walk_components(normalized))
    v2 = bool(int(flags) & COMPONENTS_V2_FLAG)
    if len(normalized) > 5 and not v2:
        raise _fail("", "legacy messages may contain at most 5 action rows")
    if contains_v2 and not v2:
        raise _fail("", "V2 components require the components_v2 message flag")
    for component in walk_components(normalized):
        value = component.get("id")
        if v2 and value is not None and not (type(value) is int and value == 0):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_COMPONENT_ID:
                raise _fail("id", "id must be a positive 32-bit integer")
        elif not v2:
            component["id"] = 0

    custom_ids: set[str] = set()
    explicit_ids: set[int] = set()
    text_total = [0]
    for index, component in enumerate(normalized):
        if not isinstance(component, Mapping):
            raise _fail(f"[{index}]", "must be an object")
        kind = _component_type(component, f"[{index}]")
        if v2:
            if kind not in ({1, *_V2_TYPES}):
                raise _fail(f"[{index}]", "legacy interactive components cannot be mixed with V2 layouts")
            _check_component(
                component,
                f"[{index}]",
                custom_ids=custom_ids,
                explicit_ids=explicit_ids,
                text_total=text_total,
            )
        else:
            if kind != 1:
                raise _fail(f"[{index}]", "legacy messages may contain only action rows")
            _check_component(
                component,
                f"[{index}]",
                custom_ids=custom_ids,
                explicit_ids=explicit_ids,
                text_total=text_total,
            )

    if v2:
        _assign_ids(normalized, explicit_ids)
    else:
        for component in walk_components(normalized):
            component["id"] = 0
    return normalized


def validate_message_state(
    components: Any,
    *,
    flags: int,
    content: str | None,
    embeds: Any,
    poll: Any = None,
    stickers: Any = None,
    previous_flags: int = 0,
) -> list[dict[str, Any]]:
    if content is not None and (not isinstance(content, str) or len(content) > 2000):
        raise _fail("", "content must be a string of 2000 or fewer characters")
    if not isinstance(embeds, list):
        raise _fail("", "embeds must be an array")
    v2 = bool(int(flags) & COMPONENTS_V2_FLAG)
    if int(previous_flags) & COMPONENTS_V2_FLAG and not v2:
        raise _fail("", "the components_v2 flag cannot be removed")
    if v2 and not components:
        raise _fail("", "components_v2 messages require at least one component")
    if v2 and ((content or "") or embeds or poll is not None or stickers):
        raise _fail("", "components_v2 messages cannot contain content, embeds, polls, or stickers")
    return validate_components(components, flags=int(flags))


def _replace_media(
    media: dict[str, Any], attachments_by_name: Mapping[str, Mapping[str, Any]], path: str
) -> Mapping[str, Any] | None:
    url = media.get("url")
    if not isinstance(url, str) or not url.startswith("attachment://"):
        return None
    filename = url.removeprefix("attachment://")
    attachment = attachments_by_name.get(filename)
    if attachment is None:
        raise _fail(path, f"attachment reference {url!r} has no uploaded file")
    media["url"] = attachment["url"]
    media["proxy_url"] = attachment["proxy_url"]
    media["content_type"] = attachment.get("content_type")
    media["attachment_id"] = attachment["id"]
    return attachment


def resolve_attachment_references(
    components: list[dict[str, Any]], attachments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Replace ``attachment://name`` media URLs with deterministic CDN data."""
    result = deepcopy(components)
    by_name = {str(item.get("filename")): item for item in attachments}
    for component in walk_components(result):
        kind = component.get("type")
        if kind == 11:
            _replace_media(component["media"], by_name, "accessory.media")
        elif kind == 12:
            for item in component.get("items") or []:
                _replace_media(item["media"], by_name, "items.media")
        elif kind == 13:
            attachment = _replace_media(component["file"], by_name, "file")
            if attachment is not None:
                component.setdefault("name", attachment["filename"])
                component.setdefault("size", attachment["size"])
    return result


def component_mentions(components: Any) -> str:
    """Return concatenated TextDisplay content for mention extraction."""
    return "\n".join(
        str(component.get("content", ""))
        for component in walk_components(components)
        if component.get("type") == 10
    )
