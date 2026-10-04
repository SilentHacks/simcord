"""Viewer-authorized, token-free preview projections.

Projection contract (implemented jointly with the bundled client): every
snapshot is a detached JSON-safe copy — no backend dicts, tokens, signed URLs,
or internal asset bookkeeping escape. ``messageIndex`` carries authorized
picker summaries; ``messages`` is the focused target or the authorized channel
window (at most 50), and ``timeline`` identifies their visible order. ``assets``
records expose only public identity, file metadata, byte availability, display
readiness, validated oriented dimensions, byte size, and diagnostics; internal
keys such as ``key``, ``url``, ``digest``, and ``source`` are stripped here.
"""

from __future__ import annotations

import base64
import hashlib
import heapq
import hmac
import json
import re
import unicodedata
from bisect import bisect_right
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .. import __version__ as _RUNTIME_VERSION
from ..backend.access import _viewer_id, can_access_channel, can_access_message
from ..backend.cdn import CDN_BASE, sticker_url
from ..backend.errors import BackendError, SetupError
from ..backend.models import EPHEMERAL_FLAG, Message
from ..components import COMPONENTS_V2_FLAG, walk_components
from ..enums import AppCommandType, ComponentType, InteractionType, MessageType
from ._diagnostics import make_diagnostic
from ._markdown import markdown_summary, markdown_tokens

if TYPE_CHECKING:
    from ..env import Env
    from . import Preview
    from ._pages import _Page

_PROTOCOL_VERSION = 3
_ENTITY_TYPES = {
    int(ComponentType.USER_SELECT): "users",
    int(ComponentType.ROLE_SELECT): "roles",
    int(ComponentType.CHANNEL_SELECT): "channels",
    int(ComponentType.MENTIONABLE_SELECT): "mentionables",
}


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


@dataclass(frozen=True, slots=True)
class IdentityRecord:
    id: int
    kind: str
    name: str
    username: str
    global_name: str | None
    nickname: str | None
    bot: bool
    system: bool
    application: bool
    webhook: bool
    avatar: str | None
    avatar_kind: str
    avatar_available: bool
    role_color: int | None
    role_id: int | None
    presence: str | None


def _identity_wire(identity: IdentityRecord) -> dict[str, Any]:
    return {
        "id": str(identity.id),
        "kind": identity.kind,
        "name": identity.name,
        "username": identity.username,
        "global_name": identity.global_name,
        "nickname": identity.nickname,
        "bot": identity.bot,
        "system": identity.system,
        "application": identity.application,
        "webhook": identity.webhook,
        "avatar": identity.avatar,
        "avatar_kind": identity.avatar_kind,
        "avatar_available": identity.avatar_available,
        "role_color": identity.role_color,
        "role_id": str(identity.role_id) if identity.role_id is not None else None,
        "presence": identity.presence,
    }


def resolve_identity(
    preview: Preview,
    page: _Page,
    user_id: int,
    *,
    message: Message | None = None,
    override: str | None = None,
) -> IdentityRecord:
    """Resolve one authorized user/member identity for every preview surface."""
    env = preview.env
    user = env.backend.get_user(user_id)
    try:
        channel = env.backend.get_channel(message.channel_id if message is not None else page.channel_id)
    except BackendError:
        channel = None
    guild = (
        env.backend.guilds.get(channel.guild_id)
        if channel is not None and channel.guild_id is not None
        else None
    )
    member = guild.members.get(user_id) if guild is not None else None
    nickname = member.nick if member is not None else None
    display = override if override is not None else nickname or user.global_name or user.name
    role_id: int | None = None
    role_color: int | None = None
    if guild is not None and member is not None:
        colored = [
            role
            for rid in member.role_ids
            if (role := guild.roles.get(rid)) is not None and int(role.color or 0) != 0
        ]
        if colored:
            role = max(colored, key=lambda item: (int(item.position), int(item.id)))
            role_id = role.id
            role_color = int(role.color)
    asset_id: str | None = None
    record = None
    if channel is None:
        avatar_kind = "custom" if user.avatar else "default"
    else:
        if message is not None and message.author_avatar:
            avatar_kind = "webhook"
            avatar_url = message.author_avatar
            source = ("message", message.channel_id, message.id)
            asset_key = f"webhook-avatar:{message.channel_id}:{message.id}:{avatar_url}"
        elif member is not None and member.avatar:
            avatar_kind = "guild"
            avatar_url = f"{CDN_BASE}/guilds/{channel.guild_id}/users/{user.id}/avatars/{member.avatar}.png"
            source = ("member_avatar", channel.guild_id, user.id, member.avatar)
            asset_key = f"member-avatar:{channel.guild_id}:{user.id}:{member.avatar}"
        elif user.avatar:
            avatar_kind = "custom"
            avatar_url = f"{CDN_BASE}/avatars/{user.id}/{user.avatar}.png"
            source = ("user_avatar", user.id, user.avatar)
            asset_key = f"avatar:{user.id}:{user.avatar}"
        else:
            avatar_kind = "default"
            avatar_index = (user.id >> 22) % 6
            avatar_url = f"{CDN_BASE}/embed/avatars/{avatar_index}.png"
            source = ("default_avatar", user.id, avatar_index)
            asset_key = f"default-avatar:{user.id}:{avatar_index}"
        asset_id = page.asset_id(
            asset_key,
            {"url": avatar_url, "filename": f"avatar-{user.id}.png", "content_type": "image/png"},
            source=source,
        )
        record = page.assets.get(asset_id)
    return IdentityRecord(
        id=user.id,
        kind="webhook"
        if message is not None and message.webhook_id is not None
        else "application"
        if user.bot and user.id == env.backend.bot_user.id
        else "user",
        name=display,
        username=user.name,
        global_name=user.global_name,
        nickname=nickname,
        bot=bool(user.bot),
        system=bool(user.system),
        application=bool(user.bot),
        webhook=bool(message is not None and message.webhook_id is not None),
        avatar=asset_id,
        avatar_kind=avatar_kind,
        avatar_available=bool(record is not None and record.available),
        role_color=role_color,
        role_id=role_id,
        presence=getattr(member, "presence", None),
    )


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


def _message_day(timestamp: str, timezone: str) -> date | None:
    try:
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(ZoneInfo(timezone)).date()
    except (ValueError, OSError):
        return None


def _is_compact_message(previous: Message | None, message: Message, timezone: str, env: Env) -> bool:
    if (
        previous is None
        or previous.author_id != message.author_id
        or previous.type != 0
        or message.type != 0
        or previous.reference
        or message.reference
        or previous.interaction_metadata
        or message.interaction_metadata
        or previous.flags & EPHEMERAL_FLAG
        or message.flags & EPHEMERAL_FLAG
    ):
        return False
    for item in (previous, message):
        thread = env.backend.channels.get(item.id)
        if thread is not None and thread.is_thread and thread.parent_id == item.channel_id:
            return False
    previous_day = _message_day(previous.timestamp, timezone)
    if previous_day is None or previous_day != _message_day(message.timestamp, timezone):
        return False
    try:
        first = datetime.fromisoformat(previous.timestamp.replace("Z", "+00:00"))
        second = datetime.fromisoformat(message.timestamp.replace("Z", "+00:00"))
        return 0 <= (second - first).total_seconds() < 7 * 60
    except ValueError:
        return False


def _message_type_info(value: int) -> dict[str, Any]:
    try:
        kind = MessageType(value)
    except ValueError:
        return {"kind": "unknown", "known": False}
    if kind == MessageType.DEFAULT:
        return {"kind": "default", "known": True}
    if kind == MessageType.REPLY:
        return {"kind": "reply", "known": True}
    if kind == MessageType.CHAT_INPUT_COMMAND:
        return {"kind": "application_command", "known": True}
    if kind == MessageType.CONTEXT_MENU_COMMAND:
        return {"kind": "context_menu_command", "known": True}
    return {"kind": "system", "known": True, "name": kind.name.lower()}


def _sticker_projection(page: _Page, sticker: Any) -> dict[str, Any]:
    suffix, content_type = {
        1: ("png", "image/png"),
        2: ("png", "image/png"),
        3: ("json", "application/json"),
        4: ("gif", "image/gif"),
    }[sticker.format_type]
    asset_id = page.asset_id(
        f"sticker:{sticker.guild_id}:{sticker.id}",
        {
            "url": sticker.url or sticker_url(sticker.id, sticker.format_type),
            "filename": f"{sticker.id}.{suffix}",
            "content_type": content_type,
        },
        source=("sticker", sticker.guild_id, sticker.id),
    )
    return {
        "id": str(sticker.id),
        "name": sticker.name,
        "format_type": sticker.format_type,
        "asset_id": asset_id,
        "available": _asset_available(page, asset_id),
    }


def _discord_message_link(guild_id: int, channel_id: int, message_id: int | None = None) -> str:
    path = f"https://discord.com/channels/{guild_id}/{channel_id}"
    return f"{path}/{message_id}" if message_id is not None else path


def _has_channel_permission(preview: Preview, page: _Page, channel: Any, permission: str) -> bool:
    if channel.guild_id is None:
        return False
    try:
        preview.env.backend.require_permissions(channel.guild_id, page.viewer.id, channel.id, permission)
    except BackendError:
        return False
    return True


def _can_send_message(preview: Preview, page: _Page, channel: Any) -> bool:
    if channel.guild_id is None:
        return page.viewer.id in channel.recipient_ids
    permission = "send_messages_in_threads" if channel.is_thread else "send_messages"
    return _has_channel_permission(preview, page, channel, permission)


def _poll_expired(preview: Preview, poll: Any) -> bool:
    try:
        expiry = datetime.fromisoformat(poll.expiry.replace("Z", "+00:00"))
        now = datetime.fromisoformat(preview.env.backend.now_iso())
    except (AttributeError, TypeError, ValueError):
        return True
    return expiry <= now


def _message_allowed_actions(preview: Preview, page: _Page, channel: Any, message: Message) -> list[str]:
    actions: list[str] = []
    if page.layout == "channel":
        if _can_send_message(preview, page, channel):
            actions.append("reply")
        if message.author_id == page.viewer.id:
            actions.extend(("edit_message", "delete_message"))
        elif _has_channel_permission(preview, page, channel, "manage_messages"):
            actions.append("delete_message")
        if channel.guild_id is not None and _has_channel_permission(
            preview, page, channel, "manage_messages"
        ):
            actions.append("set_pinned")
    if channel.guild_id is None or _has_channel_permission(preview, page, channel, "add_reactions"):
        actions.append("set_reaction")
    poll = message.poll
    if poll is not None and not poll.finalized and not _poll_expired(preview, poll):
        actions.append("set_poll_votes")
    return actions


def _reaction_emoji_projection(page: _Page, emoji: str) -> dict[str, Any]:
    name, separator, emoji_id = emoji.partition(":")
    if not separator or not emoji_id.isdigit():
        return {"name": emoji}
    record = _known_emoji(page, emoji_id)
    if record is None:
        return {"name": name, "id": emoji_id, "custom": True, "available": False, "asset_id": None}
    return _project_emoji(
        page,
        {"name": record.name, "id": emoji_id, "animated": bool(record.animated)},
    )


def _message_projection(
    preview: Preview, page: _Page, message: Message, *, compact: bool, channel: Any
) -> dict[str, Any]:
    env = preview.env
    context = _markdown_context(preview, page, message, channel)
    type_info = _message_type_info(int(message.type))
    attachments = list(message.attachments)
    can_add_reactions = channel.guild_id is None or _has_channel_permission(
        preview, page, channel, "add_reactions"
    )
    author = _identity_wire(
        resolve_identity(preview, page, message.author_id, message=message, override=message.author_name)
    )
    data = {
        "id": str(message.id),
        "channel_id": str(message.channel_id),
        "author": author,
        "author_ref": {"kind": author["kind"], "id": str(message.author_id)},
        "timestamp": message.timestamp,
        "edited_timestamp": message.edited_timestamp,
        "type": int(message.type),
        "type_info": type_info,
        "pinned": bool(message.pinned),
        "tts": bool(message.tts),
        "content": message.content,
        "content_tokens": (
            _decorate_markdown_emoji(markdown_tokens(message.content, "message", context=context), page)
            if type_info["kind"] not in {"system", "unknown"}
            else []
        ),
        "embeds": [_embed_projection(page, message, item, attachments, context) for item in message.embeds],
        "components": _decorate_components(preview, page, message, message.components, attachments, context),
        "flags": int(message.flags),
        "ephemeral": bool(message.flags & EPHEMERAL_FLAG),
        "components_v2": bool(message.flags & COMPONENTS_V2_FLAG),
        "compact": compact,
        "attachments": [_attachment(env, message, item, page) for item in attachments],
        "mention_user_ids": [
            str(uid) for uid in message.mention_user_ids if _user_allowed(preview, page, uid)
        ],
        "mention_role_ids": [
            str(rid) for rid in message.mention_role_ids if _role_allowed(preview, page, rid)
        ],
        "mention_everyone": bool(message.mention_everyone),
        "mentions": {"users": [], "roles": [], "channels": [], "everyone": bool(message.mention_everyone)},
        "reactions": [
            {
                "emoji": _reaction_emoji_projection(page, reaction.emoji),
                "count": len(reaction.user_ids),
                "viewer_reacted": page.viewer.id in reaction.user_ids,
                "can_toggle": can_add_reactions or page.viewer.id in reaction.user_ids,
            }
            for reaction in message.reactions
        ],
        "poll": None,
        "stickers": [_sticker_projection(page, sticker) for sticker in message.stickers],
        "thread": None,
        "system": None,
        "allowed_actions": _message_allowed_actions(preview, page, channel, message),
        "reply": {"state": "unavailable"},
        "interaction_header": None,
    }
    data["mentions"]["users"] = list(data["mention_user_ids"])
    if type_info["kind"] == "system":
        message_kind = MessageType(int(message.type))
        icon = {
            MessageType.CHANNEL_NAME_CHANGE: "channel",
            MessageType.CHANNEL_ICON_CHANGE: "channel",
            MessageType.PINS_ADD: "pin",
            MessageType.NEW_MEMBER: "member",
            MessageType.RECIPIENT_ADD: "member",
            MessageType.RECIPIENT_REMOVE: "member",
            MessageType.THREAD_CREATED: "thread",
            MessageType.THREAD_STARTER_MESSAGE: "thread",
        }.get(message_kind, "system")
        system: dict[str, Any] = {
            "kind": type_info["name"],
            "icon": icon,
            "text": message.content or "",
            "text_tokens": markdown_tokens(message.content or "", "system"),
            "author": author,
        }
        metadata = message.system_metadata
        if metadata is not None:
            if metadata.recipient_id is not None and _user_allowed(preview, page, metadata.recipient_id):
                system["recipient"] = _identity_wire(resolve_identity(preview, page, metadata.recipient_id))
            if metadata.channel_id is not None:
                try:
                    target_channel = env.backend.get_channel(metadata.channel_id)
                except BackendError:
                    target_channel = None
                if (
                    target_channel is not None
                    and target_channel.guild_id == channel.guild_id
                    and target_channel.guild_id is not None
                    and can_access_channel(env, target_channel.id, page.viewer, history=True)
                ):
                    system["channel"] = {
                        "id": str(target_channel.id),
                        "name": target_channel.name or str(target_channel.id),
                        "url": _discord_message_link(target_channel.guild_id, target_channel.id),
                    }
            if metadata.referenced_message_id is not None:
                reference_channel_id = metadata.referenced_channel_id or message.channel_id
                try:
                    referenced = env.backend.get_message(reference_channel_id, metadata.referenced_message_id)
                except BackendError:
                    referenced = None
                if referenced is not None and can_access_message(
                    env, reference_channel_id, referenced, page.viewer, history=True
                ):
                    try:
                        reference_channel = env.backend.get_channel(reference_channel_id)
                    except BackendError:
                        reference_channel = None
                    if (
                        reference_channel is not None
                        and reference_channel.guild_id is not None
                        and reference_channel.guild_id == channel.guild_id
                    ):
                        system["reference"] = {
                            "id": str(referenced.id),
                            "author": _identity_wire(
                                resolve_identity(
                                    preview,
                                    page,
                                    referenced.author_id,
                                    message=referenced,
                                    override=referenced.author_name,
                                )
                            ),
                            "url": _discord_message_link(
                                reference_channel.guild_id, reference_channel_id, referenced.id
                            ),
                        }
        data["system"] = system
    data["mentions"]["roles"] = list(data["mention_role_ids"])
    thread = env.backend.channels.get(message.id)
    if (
        thread is not None
        and thread.is_thread
        and thread.parent_id == message.channel_id
        and can_access_channel(env, thread.id, page.viewer, history=True)
    ):
        data["thread"] = {
            "id": str(thread.id),
            "name": thread.name or "",
            "message_count": thread.message_count,
            "archived": bool(thread.thread_metadata and thread.thread_metadata.archived),
        }
    metadata = message.interaction_metadata or {}
    try:
        is_application_command = int(metadata.get("type", -1)) == int(InteractionType.APPLICATION_COMMAND)
    except (TypeError, ValueError):
        is_application_command = False
    if is_application_command:
        try:
            command_type = AppCommandType(int(metadata["command_type"]))
        except (KeyError, TypeError, ValueError):
            command_type = None
        interaction_header: dict[str, Any] = {
            "kind": (
                "context_menu_command"
                if type_info["kind"] == "context_menu_command"
                else "application_command"
            )
        }
        if isinstance(metadata.get("name"), str):
            interaction_header["name"] = metadata["name"]
        if command_type is not None:
            interaction_header["command_type"] = command_type.name.lower()
        user = metadata.get("user")
        if isinstance(user, Mapping):
            user_id = user.get("id")
            if isinstance(user_id, (int, str)) and not isinstance(user_id, bool):
                try:
                    invoker_id = int(user_id)
                except ValueError:
                    invoker_id = None
                if invoker_id is not None and _user_allowed(preview, page, invoker_id):
                    interaction_header["user"] = _identity_wire(resolve_identity(preview, page, invoker_id))
        target_id_value = metadata.get("target_id")
        target_type_value = metadata.get("target_type")
        try:
            target_id = int(target_id_value) if target_id_value is not None else None
            target_type = AppCommandType(int(target_type_value)) if target_type_value is not None else None
        except (TypeError, ValueError):
            target_id = None
            target_type = None
        if target_id is not None and target_type == AppCommandType.USER:
            if _user_allowed(preview, page, target_id):
                interaction_header["target_user"] = _identity_wire(resolve_identity(preview, page, target_id))
        elif target_id is not None and target_type == AppCommandType.MESSAGE:
            try:
                target_channel_id = int(metadata["target_channel_id"])
                target_message = env.backend.get_message(target_channel_id, target_id)
            except (BackendError, KeyError, TypeError, ValueError):
                target_message = None
                target_channel_id = 0
            if target_message is not None and can_access_message(
                env, target_channel_id, target_message, page.viewer, history=True
            ):
                try:
                    target_channel = env.backend.get_channel(target_channel_id)
                except BackendError:
                    target_channel = None
                if target_channel is not None and target_channel.guild_id is not None:
                    interaction_header["target_message"] = {
                        "id": str(target_message.id),
                        "url": _discord_message_link(
                            target_channel.guild_id, target_channel_id, target_message.id
                        ),
                    }
        data["interaction_header"] = interaction_header
    if message.poll is not None:
        poll = message.poll
        total_votes = sum(len(voters) for voters in poll.votes.values())
        data["poll"] = {
            "question": poll.question,
            "answers": [
                {
                    "id": str(answer.answer_id),
                    "text": answer.text,
                    "emoji": _reaction_emoji_projection(page, answer.emoji) if answer.emoji else None,
                    "count": len(poll.votes.get(answer.answer_id, set())),
                    "percentage": (
                        round(len(poll.votes.get(answer.answer_id, set())) * 100 / total_votes)
                        if total_votes
                        else 0
                    ),
                    "viewer_selected": page.viewer.id in poll.votes.get(answer.answer_id, set()),
                }
                for answer in poll.answers
            ],
            "total_votes": total_votes,
            "expiry": poll.expiry,
            "expired": _poll_expired(preview, poll),
            "finalized": bool(poll.finalized),
            "multiselect": bool(poll.allow_multiselect),
            "layout_type": int(poll.layout_type),
        }
    data["mention_names"] = {}
    data["mention_entities"] = {}
    for uid in data["mention_user_ids"]:
        identity = _identity_wire(resolve_identity(preview, page, int(uid)))
        data["mention_names"][uid] = identity["name"]
        data["mention_entities"][uid] = identity
    data["mention_channel_ids"] = list(context["channel_ids"])
    data["mention_channel_names"] = dict(context["channels"])
    data["mentions"]["channels"] = list(context["channel_ids"])
    reference = message.reference
    if reference:
        try:
            reference_message_id = reference.get("message_id")
            if reference_message_id is None:
                raise TypeError("reference message id is missing")
            reference_id = int(reference_message_id)
            reference_channel = reference.get("channel_id", message.channel_id)
            if reference_channel is None:
                raise TypeError("reference channel id is missing")
            reference_channel_id = int(reference_channel)
            referenced = env.backend.get_message(reference_channel_id, reference_id)
        except (BackendError, TypeError, ValueError):
            referenced = None
        if referenced is not None and can_access_message(
            env, reference_channel_id, referenced, page.viewer, history=True
        ):
            referenced_identity = _identity_wire(
                resolve_identity(
                    preview,
                    page,
                    referenced.author_id,
                    message=referenced,
                    override=referenced.author_name,
                )
            )
            reply_channel = env.backend.get_channel(referenced.channel_id)
            reply_context = _markdown_context(preview, page, referenced, reply_channel)
            data["reply"] = {
                "state": "resolved",
                "message_id": str(referenced.id),
                "channel_id": str(referenced.channel_id),
                "channel_name": (
                    env.backend.get_channel(referenced.channel_id).name
                    if referenced.channel_id != message.channel_id
                    else None
                ),
                "author": referenced_identity,
                "excerpt_tokens": _decorate_markdown_emoji(
                    markdown_tokens(referenced.content[:100], "message", context=reply_context),
                    page,
                ),
                "preview_kind": "message",
            }
    return data


def _user_allowed(preview: Preview, page: _Page, user_id: int) -> bool:
    env = preview.env
    channel = env.backend.get_channel(page.channel_id)
    if channel.guild_id is None:
        return user_id in channel.recipient_ids
    guild = env.backend.guilds.get(channel.guild_id)
    return guild is not None and user_id in guild.members


def _role_allowed(preview: Preview, page: _Page, role_id: int) -> bool:
    env = preview.env
    channel = env.backend.get_channel(page.channel_id)
    guild = env.backend.guilds.get(channel.guild_id) if channel.guild_id is not None else None
    return guild is not None and role_id in guild.roles and role_id != guild.id


def _identity_name(
    preview: Preview,
    page: _Page,
    user_id: int,
    *,
    message: Message | None = None,
    override: str | None = None,
) -> str:
    user = preview.env.backend.get_user(user_id)
    channel_id = message.channel_id if message is not None else page.channel_id
    try:
        channel = preview.env.backend.get_channel(channel_id)
    except BackendError:
        channel = None
    guild = (
        preview.env.backend.guilds.get(channel.guild_id)
        if channel is not None and channel.guild_id is not None
        else None
    )
    member = guild.members.get(user_id) if guild is not None else None
    return (
        override
        if override is not None
        else (member.nick if member is not None and member.nick else user.global_name or user.name)
    )


def _markdown_context(
    preview: Preview,
    page: _Page,
    message: Message | None,
    channel: Any,
    *,
    content: str = "",
) -> dict[str, Any]:
    backend = preview.env.backend
    texts = [message.content or ""] if message is not None else [content]
    if message is not None:
        for embed in message.embeds:
            for key in ("title", "description"):
                if isinstance(embed.get(key), str):
                    texts.append(embed[key])
            for owner, key in (("author", "name"), ("footer", "text")):
                value = embed.get(owner)
                if isinstance(value, dict) and isinstance(value.get(key), str):
                    texts.append(value[key])
            fields = embed.get("fields", [])
            if isinstance(fields, list):
                for field in fields:
                    if isinstance(field, dict):
                        for key in ("name", "value"):
                            if isinstance(field.get(key), str):
                                texts.append(field[key])
        texts.extend(
            component["content"]
            for component in walk_components(message.components)
            if int(component.get("type", -1)) == int(ComponentType.TEXT_DISPLAY)
            and isinstance(component.get("content"), str)
        )
    users: dict[str, str] = {}
    for text in texts:
        for match in re.finditer(r"<@!?([0-9]{1,20})>", text):
            user_id = int(match.group(1))
            if _user_allowed(preview, page, user_id):
                users[match.group(1)] = _identity_name(preview, page, user_id)

    roles: dict[str, str] = {}
    for text in texts:
        for match in re.finditer(r"<@&([0-9]{1,20})>", text):
            role_id = int(match.group(1))
            if _role_allowed(preview, page, role_id):
                guild = backend.guilds.get(channel.guild_id)
                if guild is not None and (role := guild.roles.get(role_id)) is not None:
                    roles[match.group(1)] = role.name

    channels: dict[str, str] = {}
    if channel.guild_id is not None:
        for text in texts:
            for match in re.finditer(r"<#([0-9]{1,20})>", text):
                channel_id = int(match.group(1))
                try:
                    mentioned = backend.get_channel(channel_id)
                except BackendError:
                    continue
                if mentioned.guild_id == channel.guild_id and can_access_channel(
                    preview.env, channel_id, page.viewer
                ):
                    channels[match.group(1)] = mentioned.name or match.group(1)

    emojis: dict[str, dict[str, Any]] = {}
    for text in texts:
        for match in re.finditer(r"<(a?):([A-Za-z0-9_]{2,32}):([0-9]{1,20})>", text):
            record = _known_emoji(page, match.group(3))
            if (
                record is not None
                and record.name == match.group(2)
                and bool(record.animated) == bool(match.group(1))
            ):
                emojis[match.group(3)] = {
                    "id": match.group(3),
                    "name": record.name,
                    "animated": bool(record.animated),
                    "custom": True,
                }

    commands: dict[str, str] = {}
    for scope in (channel.guild_id, None):
        for command in backend.commands.get(scope, {}).values():
            command_id = command.get("id")
            name = command.get("name")
            if (
                command_id is not None
                and isinstance(name, str)
                and int(command.get("type", AppCommandType.CHAT_INPUT)) == int(AppCommandType.CHAT_INPUT)
            ):
                commands.setdefault(str(command_id), name)

    return {
        "users": users,
        "roles": roles,
        "channels": channels,
        "channel_ids": list(channels),
        "emojis": emojis,
        "commands": commands,
        "everyone": bool(message.mention_everyone) if message is not None else False,
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
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


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

        matches = heapq.nsmallest(51, after_pivot(), key=key)
        rows = matches[:50]
        has_next = len(matches) > 50
    elif direction == "before":

        def before_pivot() -> Iterable[Any]:
            nonlocal has_next
            assert pivot is not None
            for item in source():
                if key(item) >= pivot:
                    has_next = True
                else:
                    yield item

        matches = heapq.nlargest(51, before_pivot(), key=key)
        rows = list(reversed(matches[:50]))
        has_previous = len(matches) > 50
    else:
        matches = heapq.nsmallest(51, source(), key=key)
        rows = matches[:50]
        has_next = len(matches) > 50
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
    page.candidate_queries = {key: value for key, value in page.candidate_queries.items() if key in result}
    return result


def candidate_control(
    preview: Preview, page: _Page, control_key: str, modal_handle: str | None
) -> tuple[dict[str, Any], str | None]:
    if not can_access_channel(preview.env, page.channel_id, page.viewer, history=True):
        raise _QueryError("control-unavailable")
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


def build_snapshot(preview: Preview, page: _Page) -> dict[str, Any]:
    """Build a detached projection for one page; no backend dictionaries escape."""
    env = preview.env
    try:
        channel = env.backend.get_channel(page.channel_id)
    except BackendError:
        channel = None
    allowed = channel is not None and can_access_channel(env, channel.id, page.viewer, history=True)
    page.referenced_assets.clear()
    visible: list[Message] = []
    if channel is not None and allowed:
        # ponytail: rebuild the bounded fixture-sized history instead of adding
        # an index that would need its own invalidation and authorization model.
        visible = [
            item
            for item in sorted(env.backend.messages.get(channel.id, {}).values(), key=lambda item: item.id)
            if can_access_message(env, channel.id, item, page.viewer, history=True)
        ]
    ids = [item.id for item in visible]
    target_index = next((index for index, item in enumerate(visible) if item.id == page.target_id), None)
    target = visible[target_index] if target_index is not None else None
    if page.layout == "channel":
        if target_index is not None and page.window_end_id == page.target_id:
            start = max(0, target_index - 24)
            end = min(len(visible), start + 50)
            start = max(0, end - 50)
            page.window_end_id = ids[end - 1] if end and end < len(visible) else None
        else:
            end = len(visible) if page.window_end_id is None else bisect_right(ids, page.window_end_id)
            start = max(0, end - 50)
        if page.window_end_id is not None and page.window_end_id not in ids:
            page.window_end_id = ids[end - 1] if end else None
        window = visible[start:end]
        history = {
            "hasBefore": start > 0,
            "hasAfter": end < len(visible),
            "windowStartId": str(window[0].id) if window else None,
            "windowEndId": str(window[-1].id) if window else None,
        }
    else:
        window = [target] if target is not None else []
        history = {"hasBefore": False, "hasAfter": False, "windowStartId": None, "windowEndId": None}
    target_id = str(target.id) if target is not None else None
    projected: dict[str, dict[str, Any]] = {}
    candidate_components: list[dict[str, Any]] = []
    previous = None
    for item in window:
        compact = page.layout == "channel" and _is_compact_message(previous, item, preview.timezone, env)
        value = _message_projection(preview, page, item, compact=compact, channel=channel)
        projected[str(item.id)] = value
        candidate_components.extend(value.get("components", []))
        previous = item
    message_index, navigation = _message_navigation(preview, page, visible)

    modal = None
    if allowed and page.modal is not None:
        payload = _decorate_emoji(deepcopy(page.modal.modal), page)
        payload = _clean(payload, drop_urls=True)
        handle = page.modal_handle or ""
        _annotate_tree(payload.get("components", []), f"modal:{handle}")
        for component in walk_components(payload.get("components", [])):
            if isinstance(component.get("content"), str):
                context = _markdown_context(preview, page, None, channel, content=component["content"])
                component["markdown_tokens"] = _decorate_markdown_emoji(
                    markdown_tokens(component["content"], "text_display", context=context), page
                )
        application = _identity_wire(resolve_identity(preview, page, preview.env.backend.bot_user.id))
        payload["application_identity"] = application
        payload["application_name"] = application["name"]
        candidate_components.extend(payload.get("components", []))
        modal = {"handle": page.modal_handle, "payload": payload}
    entities: dict[str, dict[str, dict[str, Any]]] = {
        "users": {},
        "members": {},
        "roles": {},
        "channels": {},
        "applications": {},
    }
    if channel is not None and allowed:
        entities["channels"][str(channel.id)] = {
            "id": str(channel.id),
            "name": channel.name,
            "type": int(channel.type),
            "guild_id": str(channel.guild_id) if channel.guild_id is not None else None,
        }
        bot_identity = _identity_wire(resolve_identity(preview, page, env.backend.bot_user.id))
        entities["applications"][str(env.backend.application_id)] = {
            **bot_identity,
            "id": str(env.backend.application_id),
            "kind": "application",
            "name": bot_identity["name"],
        }
        if target is not None:
            identity = _identity_wire(
                resolve_identity(
                    preview,
                    page,
                    target.author_id,
                    message=target,
                    override=target.author_name,
                )
            )
            if channel.guild_id is not None:
                guild = env.backend.guilds.get(channel.guild_id)
                if guild is not None:
                    member = guild.members.get(target.author_id)
                    if member is not None:
                        entities["members"][str(target.author_id)] = identity
                    for rid in target.mention_role_ids:
                        role = guild.roles.get(rid)
                        if role is not None and rid != guild.id:
                            entities["roles"][str(rid)] = {
                                "id": str(rid),
                                "name": role.name,
                                "color": int(role.color or 0),
                                "position": int(role.position),
                            }
            entities["users"][str(target.author_id)] = identity
            for uid in target.mention_user_ids:
                if _user_allowed(preview, page, uid):
                    entities["users"][str(uid)] = _identity_wire(resolve_identity(preview, page, uid))
            if target.reference:
                reference_id = target.reference.get("message_id")
                reference_channel_id = target.reference.get("channel_id", target.channel_id)
                reference_channel = target.channel_id
                try:
                    if reference_id is None:
                        raise ValueError
                    reference_channel = int(reference_channel_id)
                    referenced = env.backend.get_message(reference_channel, int(reference_id))
                except (BackendError, TypeError, ValueError):
                    referenced = None
                if referenced is not None and can_access_message(
                    env, reference_channel, referenced, page.viewer, history=True
                ):
                    entities["users"][str(referenced.author_id)] = _identity_wire(
                        resolve_identity(
                            preview,
                            page,
                            referenced.author_id,
                            message=referenced,
                            override=referenced.author_name,
                        )
                    )
    diagnostics: list[dict[str, Any]] = []
    for item in page.diagnostics:
        if isinstance(item, Mapping):
            subject = None
            message_id = item.get("message_id")
            if isinstance(message_id, str) and message_id in projected:
                subject = {"messageId": message_id}
            diagnostics.append(make_diagnostic(str(item.get("code", "internal-error")), subject=subject))
    for value in projected.values():
        message_id = value["id"]
        if value["type_info"]["kind"] == "unknown":
            diagnostics.append(make_diagnostic("message-type-unknown", subject={"messageId": message_id}))
        missing_sku = any(
            component.get("type") == int(ComponentType.BUTTON)
            and component.get("style") == 6
            and str(component.get("sku_id", "unknown")) not in preview._sku_presentations
            for component in walk_components(value["components"])
        )
        if missing_sku:
            diagnostics.append(
                make_diagnostic("premium-sku-metadata-missing", subject={"messageId": message_id})
            )
        if any(not sticker["available"] for sticker in value["stickers"]):
            diagnostics.append(
                make_diagnostic("sticker-asset-unavailable", subject={"messageId": message_id})
            )
    candidates = _candidates(preview, page, candidate_components, diagnostics) if allowed else {}
    diagnostics = list({item["id"]: item for item in diagnostics}.values())
    can_send = channel is not None and allowed and _can_send_message(preview, page, channel)
    exact_profile = {"width": page.width, "height": page.height}
    host = {"width": page.host_width, "height": page.host_height}
    viewport = (
        {"width": max(240, page.host_width), "height": max(180, page.host_height)}
        if page.display == "responsive"
        else exact_profile
    )
    constraint = None
    if page.display == "responsive" and (page.host_width < 240 or page.host_height < 180):
        constraint = "host_below_minimum"
    elif page.display == "fixed" and (page.host_width < page.width or page.host_height < page.height):
        constraint = "host_smaller_than_viewport"
    from . import _require_preview_runtime

    font_assets = [{**face, "scripts": list(face["scripts"])} for face in _require_preview_runtime()]
    snapshot = {
        "runtimeVersion": _RUNTIME_VERSION,
        "publication": {
            "publishedAt": page.published_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "publishedRevision": page.revision,
            "reason": page.publication_reason,
        },
        "presentation": {
            "display": page.display,
            "layout": page.layout,
            "viewport": viewport,
            "exactProfile": exact_profile,
            "host": host,
            "constraint": constraint,
        },
        "protocolVersion": _PROTOCOL_VERSION,
        "publishedRevision": page.revision,
        "context": {"id": page.id, "generation": page.generation},
        "botGeneration": env._generation,
        "viewers": [
            _identity_wire(resolve_identity(preview, page, _viewer_id(viewer))) for viewer in preview.viewers
        ],
        "viewerId": str(_viewer_id(page.viewer)),
        "channelId": str(page.channel_id),
        "layout": page.layout,
        "channel": {
            "id": str(page.channel_id),
            "name": channel.name if channel is not None and allowed else None,
            "guildId": (
                str(channel.guild_id)
                if channel is not None and channel.guild_id is not None and allowed
                else None
            ),
            "type": int(channel.type) if channel is not None and allowed else None,
            "topic": channel.topic if channel is not None and allowed else None,
            "canSendMessages": can_send,
        },
        "targetId": target_id,
        "messages": projected,
        "timeline": list(projected),
        "messageIndex": message_index,
        "navigation": navigation,
        "history": history,
        "modal": modal,
        "entities": entities,
        "candidates": candidates,
        "profile": {
            "theme": "dark",
            "scope": "desktop-dark",
            "width": viewport["width"],
            "height": viewport["height"],
            "locale": preview.locale,
            "timezone": preview.timezone,
            "presentationTime": preview.capture_time.isoformat(),
            "fontStackConfigured": '"Noto Sans", "Noto Color Emoji", "Noto Sans Arabic", "Noto Sans Hebrew", "Noto Sans Devanagari", "Noto Sans SC", sans-serif',
            "fontAssets": font_assets,
            "fontResolution": "unavailable until Chromium platform-font inspection",
        },
        "status": page.status,
        "diagnostics": diagnostics,
        "lastAction": (
            {key: value for key, value in page.last_action.items() if key != "result"}
            if page.last_action is not None
            else None
        ),
        "activity": page.activity,
    }
    preview._reconcile_assets(page)
    snapshot["assets"] = {}
    for asset_id, record in page.assets.items():
        item = record.to_wire()
        item.pop("diagnostic", None)
        snapshot["assets"][asset_id] = item
    if not allowed:
        from ._pages import _PageOps

        snapshot["status"] = "access_denied"
        _PageOps._denied_payload(snapshot)
    return snapshot


__all__ = [
    "IdentityRecord",
    "build_snapshot",
    "candidate_control",
    "resolve_identity",
    "validate_candidate_query",
    "validate_message_query",
]
