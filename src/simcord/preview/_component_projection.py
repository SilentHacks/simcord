"""Detached component, embed, emoji, and attachment projections."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

from ..backend.cdn import CDN_BASE
from ..backend.models import Message
from ..components import walk_components
from ..enums import ComponentType
from ._markdown import markdown_tokens

if TYPE_CHECKING:
    from ..env import Env
    from . import Preview
    from ._pages import _Page


def _clean(value: Any, *, drop_urls: bool = False) -> Any:
    """Copy allowlisted-ish backend data without recursive private payloads."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"token", "webhook_token", "proxy_url", "referenced_message", "resolved", "key"}:
                continue
            if drop_urls and key in {"url", "proxy_url"}:
                continue
            out[key] = _clean(item, drop_urls=drop_urls)
        return out
    if isinstance(value, list):
        return [_clean(item, drop_urls=drop_urls) for item in value]
    return value


def _safe_link(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https", "mailto"}:
        return None
    if parsed.scheme.lower() != "mailto" and not parsed.netloc:
        return None
    return value


def _asset_available(page: _Page, asset_id: str | None) -> bool:
    return bool(asset_id and (record := page.assets.get(asset_id)) is not None and record.available)


def _attachment(env: Env, message: Message, attachment: dict[str, Any], page: _Page) -> dict[str, Any]:
    attachment_id = str(attachment.get("id", ""))
    url = attachment.get("url")
    content_type = str(attachment.get("content_type") or "application/octet-stream")
    filename = str(attachment.get("filename", "attachment"))
    key = f"attachment:{message.channel_id}:{message.id}:{attachment_id}"
    source = ("attachment", message.channel_id, message.id, attachment_id)
    asset_id = page.asset_id(key, attachment, source=source)
    preview = None
    if content_type.startswith("text/") and isinstance(url, str):
        raw = env.backend.cdn.get(url)
        if raw is not None:
            preview = raw[:4096].decode("utf-8", "replace").strip()
    return {
        "id": attachment_id,
        "filename": filename,
        "description": attachment.get("description"),
        "content_type": content_type,
        "preview": preview,
        "size": int(attachment.get("size", 0) or 0),
        "inline": (
            content_type.lower().startswith(("image/", "audio/", "video/"))
            and not filename.lower().endswith((".svg", ".html", ".htm", ".xhtml"))
            and content_type.lower() not in {"image/svg+xml", "text/html", "application/xhtml+xml"}
        ),
        "width": attachment.get("width"),
        "height": attachment.get("height"),
        "duration_secs": attachment.get("duration_secs"),
        "asset_id": asset_id,
        "available": _asset_available(page, asset_id),
        "spoiler": bool(attachment.get("spoiler", False)) or filename.startswith("SPOILER_"),
    }


def _asset_meta(
    page: _Page,
    url: Any,
    fallback: dict[str, Any] | None = None,
    message: Message | None = None,
) -> str | None:
    if not isinstance(url, str) or not url:  # pragma: no cover - component schema requires a URL
        return None
    metadata = dict(fallback or {})
    metadata.setdefault("url", url)
    source = None
    if fallback is not None and message is not None:
        key = f"attachment:{message.channel_id}:{message.id}:{fallback.get('id', '')}"
        source = ("attachment", message.channel_id, message.id, str(fallback.get("id", "")))
    elif message is not None:
        # A copied CDN URL is only usable when the owning message remains
        # authorized; bytes with the same URL never transfer ownership.
        key = f"url:{url}:message:{message.channel_id}:{message.id}"
        source = ("message", message.channel_id, message.id)
    else:
        key = f"url:{url}"
    return page.asset_id(key, metadata, source=source)


def _known_emoji(page: _Page, emoji_id: str) -> Any | None:
    try:
        identity = int(emoji_id)
    except ValueError:
        return None
    backend = page.preview.env.backend
    emoji = backend.application_emojis.get(identity)
    if emoji is not None:
        return emoji if emoji.available else None
    channel = backend.channels.get(page.channel_id)
    guild = (
        backend.guilds.get(channel.guild_id) if channel is not None and channel.guild_id is not None else None
    )
    emoji = guild.emojis.get(identity) if guild is not None else None
    if emoji is None or not emoji.available:
        return None
    if guild is not None and emoji.role_ids:
        member = guild.members.get(page.viewer.id)
        if member is None or not set(emoji.role_ids).intersection(member.role_ids):
            return None
    return emoji


def _project_emoji(page: _Page, emoji: Any) -> Any:
    if not isinstance(emoji, dict) or not emoji.get("id"):
        return _clean(emoji)
    value = _clean(emoji, drop_urls=True)
    emoji_id = str(emoji["id"])
    record = _known_emoji(page, emoji_id)
    if record is None:
        value.update({"custom": True, "available": False, "asset_id": None})
        return value
    extension = "gif" if record.animated else "png"
    content_type = "image/gif" if record.animated else "image/png"
    url = f"{CDN_BASE}/emojis/{emoji_id}.{extension}"
    asset_id = page.asset_id(
        f"emoji:{emoji_id}",
        {"url": url, "filename": f"{emoji_id}.{extension}", "content_type": content_type},
        source=("emoji", emoji_id),
    )
    value.update(
        {
            "name": record.name,
            "animated": bool(record.animated),
            "custom": True,
            "asset_id": asset_id,
            "available": _asset_available(page, asset_id),
        }
    )
    return value


def _decorate_markdown_emoji(value: Any, page: _Page) -> Any:
    if isinstance(value, dict):
        if value.get("type") == "emoji":
            value["emoji"] = _project_emoji(page, value["emoji"])
        else:
            for item in value.values():
                _decorate_markdown_emoji(item, page)
    elif isinstance(value, list):
        for item in value:
            _decorate_markdown_emoji(item, page)
    return value


def _decorate_emoji(value: Any, page: _Page) -> Any:
    if isinstance(value, dict):
        return {
            key: _project_emoji(page, item) if key == "emoji" else _decorate_emoji(item, page)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_decorate_emoji(item, page) for item in value]
    return value


def _control_key(scope: str, component: Mapping[str, Any], path: str) -> str | None:
    kind = component.get("type")
    interactive = {
        int(ComponentType.BUTTON),
        int(ComponentType.STRING_SELECT),
        int(ComponentType.USER_SELECT),
        int(ComponentType.ROLE_SELECT),
        int(ComponentType.MENTIONABLE_SELECT),
        int(ComponentType.CHANNEL_SELECT),
        int(ComponentType.TEXT_INPUT),
        int(ComponentType.RADIO_GROUP),
        int(ComponentType.CHECKBOX_GROUP),
        int(ComponentType.CHECKBOX),
        int(ComponentType.FILE_UPLOAD),
    }
    if kind not in interactive:
        return None
    wire_id = component.get("id")
    identity = (
        str(wire_id) if isinstance(wire_id, int) and not isinstance(wire_id, bool) and wire_id > 0 else path
    )
    return f"{scope}:component:{identity}"


def _annotate_control_keys(value: Any, scope: str, path: str = "0") -> None:
    if not isinstance(value, dict):
        return
    key = _control_key(scope, value, path)
    if key is not None:
        value["control_key"] = key
    children = value.get("components")
    if isinstance(children, list):
        for index, child in enumerate(children):
            _annotate_control_keys(child, scope, f"{path}.components.{index}")
    for child_name in ("accessory", "component"):
        child = value.get(child_name)
        if isinstance(child, dict):
            _annotate_control_keys(child, scope, f"{path}.{child_name}")


def _annotate_tree(value: Any, scope: str) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _annotate_control_keys(item, scope, str(index))
    else:
        _annotate_control_keys(value, scope)


def _attachment_index(
    attachments: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_url = {str(item.get("url")): item for item in attachments if isinstance(item.get("url"), str)}
    by_name = {str(item.get("filename")): item for item in attachments if item.get("filename") is not None}
    return by_url, by_name


def _decorate_components(
    preview: Preview,
    page: _Page,
    message: Message,
    components: Any,
    attachments: list[dict[str, Any]],
    context: Mapping[str, Any],
) -> Any:
    rows = deepcopy(components)
    _annotate_tree(rows, f"message:{message.id}")
    by_url, by_name = _attachment_index(attachments)

    for node in walk_components(rows):
        typ = int(node["type"])
        if typ == int(ComponentType.BUTTON) and int(node.get("style", 0)) == 5:
            link = _safe_link(node.get("url"))
            if link is None:
                node.pop("url", None)
            else:
                node["url"] = link
        if typ == int(ComponentType.BUTTON) and int(node.get("style", 0)) == 6:
            node.pop("sku_presentation", None)
            sku_id = str(node.get("sku_id", ""))
            presentation = preview._sku_presentations.get(sku_id)
            if presentation is not None:
                value: dict[str, Any] = {
                    "sku_id": sku_id,
                    "name": presentation["name"],
                    "price_text": presentation["price_text"],
                    "locale": presentation["locale"],
                }
                icon_url = presentation.get("icon_url")
                if icon_url is not None:
                    asset_id = _asset_meta(page, icon_url, message=message)
                    if asset_id:
                        value["icon_asset_id"] = asset_id
                        value["icon_available"] = _asset_available(page, asset_id)
                node["sku_presentation"] = value
        media_nodes: list[dict[str, Any]] = []
        if typ == int(ComponentType.THUMBNAIL):
            media_nodes.append(node["media"])
        elif typ == int(ComponentType.MEDIA_GALLERY):
            media_nodes.extend(item["media"] for item in node["items"])
        elif typ == int(ComponentType.FILE):
            media_nodes.append(node["file"])
        for media in media_nodes:  # pragma: no branch - component schemas bound this collection
            url = media["url"]
            attachment = by_url.get(url)
            if attachment is None and url.startswith("attachment://"):
                attachment = by_name.get(url.removeprefix("attachment://"))
            asset_id = cast(str, _asset_meta(page, url, attachment, message))
            media["asset_id"] = asset_id
            media["available"] = _asset_available(page, asset_id)
            if attachment is not None:
                media.update(
                    {
                        key: attachment[key]
                        for key in ("content_type", "description", "filename", "height", "size", "width")
                        if attachment.get(key) is not None
                    }
                )
                media["attachment_id"] = str(attachment.get("id", ""))
            media.pop("url", None)
        if typ == int(ComponentType.TEXT_DISPLAY):
            node["markdown_tokens"] = markdown_tokens(node["content"], "text_display", context=context)
    return _clean(_decorate_emoji(rows, page))


def _embed_projection(
    page: _Page,
    message: Message,
    embed: dict[str, Any],
    attachments: list[dict[str, Any]],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    value = _clean(_decorate_emoji(deepcopy(embed), page), drop_urls=True)
    link = _safe_link(embed.get("url"))
    if link:
        value["url"] = link
    provider = embed.get("provider")
    if isinstance(provider, dict):
        provider_link = _safe_link(provider.get("url"))
        if provider_link:
            value.setdefault("provider", {})["url"] = provider_link
    for owner_key in ("author", "footer"):
        owner = embed.get(owner_key)
        if not isinstance(owner, dict):
            continue
        owner_value = value.setdefault(owner_key, {})
        owner_link = _safe_link(owner.get("url"))
        if owner_link:
            owner_value["url"] = owner_link
        icon_url = owner.get("icon_url") or owner.get("icon_proxy_url")
        if isinstance(icon_url, str):
            icon_id = _asset_meta(page, icon_url, message=message)
            if icon_id:
                owner_value["icon_asset_id"] = icon_id
                owner_value["icon_available"] = _asset_available(page, icon_id)
    by_url, by_name = _attachment_index(attachments)
    for key in ("image", "thumbnail", "video"):
        media = embed.get(key)
        target = value.get(key)
        if not isinstance(media, dict) or not isinstance(target, dict):
            continue
        url = media.get("url")
        if not isinstance(url, str) or not url:
            target["available"] = False
            continue
        item = by_url.get(url)
        if item is None and url.startswith("attachment://"):
            item = by_name.get(url.removeprefix("attachment://"))
        asset_id = _asset_meta(page, url, item, message)
        if asset_id:
            target["asset_id"] = asset_id
            target["available"] = _asset_available(page, asset_id)
            if item is not None:
                target["attachment_id"] = str(item.get("id", ""))
    if isinstance(embed.get("title"), str):
        value["title_tokens"] = markdown_tokens(embed["title"], "embed_title", context=context)
    if isinstance(embed.get("description"), str):
        value["description_tokens"] = markdown_tokens(
            embed["description"], "embed_description", context=context
        )
    if isinstance(embed.get("footer"), dict) and isinstance(embed["footer"].get("text"), str):
        value["footer_tokens"] = markdown_tokens(embed["footer"]["text"], "embed_footer")
    fields = embed.get("fields")
    projected_fields = value.get("fields")
    if isinstance(fields, list) and isinstance(projected_fields, list):
        for index, field in enumerate(fields):
            if not isinstance(field, dict) or index >= len(projected_fields):
                continue
            target = projected_fields[index]
            if not isinstance(target, dict):
                continue
            if isinstance(field.get("name"), str):
                target["name_tokens"] = markdown_tokens(field["name"], "embed_field_name", context=context)
            if isinstance(field.get("value"), str):
                target["value_tokens"] = markdown_tokens(field["value"], "embed_field_value", context=context)
    return _clean(_decorate_emoji(value, page))
