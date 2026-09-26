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

import re
from bisect import bisect_right
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from ..backend.access import _viewer_id, can_access_channel, can_access_message
from ..backend.cdn import CDN_BASE, sticker_url
from ..backend.errors import BackendError
from ..backend.models import EPHEMERAL_FLAG, Message
from ..components import COMPONENTS_V2_FLAG, walk_components
from ..enums import AppCommandType, ComponentType, InteractionType, MessageType
from ._markdown import markdown_tokens

if TYPE_CHECKING:
    from ..env import Env
    from . import Preview
    from ._pages import _Page

_PROTOCOL_VERSION = 2
_ENTITY_TYPES = {
    int(ComponentType.USER_SELECT): "users",
    int(ComponentType.ROLE_SELECT): "roles",
    int(ComponentType.CHANNEL_SELECT): "channels",
    int(ComponentType.MENTIONABLE_SELECT): "mentionables",
}
_FONT_PROFILE = (
    {
        "family": "Noto Sans",
        "style": "normal",
        "weight": "100 900",
        "filename": "noto-sans-latin-v2.015.ttf",
        "sha256": "bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d",
        "scripts": ["Latin", "Greek", "Cyrillic", "Vietnamese"],
    },
    {
        "family": "Noto Sans",
        "style": "italic",
        "weight": "100 900",
        "filename": "noto-sans-latin-italic-v2.015.ttf",
        "sha256": "58e6e0ebd1931b29a365aa2d3e2ee9a9e831a3af7cf3ad1462d4e72154f0b291",
        "scripts": ["Latin", "Greek", "Cyrillic", "Vietnamese"],
    },
    {
        "family": "Noto Sans Mono",
        "style": "normal",
        "weight": "100 900",
        "filename": "noto-sans-mono-v2.014.ttf",
        "sha256": "2cb2adb378a8f574213e23df697050b83c54c27df465a2015552740b2769a081",
        "scripts": ["Latin", "Greek", "Cyrillic", "Vietnamese"],
    },
    {
        "family": "Noto Color Emoji",
        "style": "normal",
        "weight": "400",
        "filename": "noto-color-emoji-v2.051.ttf",
        "sha256": "741815c198323b067670a1cfe6660ad8463ffcb8733314af30868eab9c4eb18b",
        "scripts": ["Emoji ZWJ", "skin-tone modifiers", "variation selectors"],
    },
    {
        "family": "Noto Sans Arabic",
        "style": "normal",
        "weight": "100 900",
        "filename": "noto-sans-arabic-v2.012.ttf",
        "sha256": "63111b5b2e074dd48cc67692e0a2726d86ee94c1c37fe8598257b7b4e87e869e",
        "scripts": ["Arabic"],
    },
    {
        "family": "Noto Sans Hebrew",
        "style": "normal",
        "weight": "100 900",
        "filename": "noto-sans-hebrew-v3.001.ttf",
        "sha256": "7ef36a2c3593758cdb622e1bdef4f84523e92fbc3ccc667438dd80ff54c2de88",
        "scripts": ["Hebrew"],
    },
    {
        "family": "Noto Sans Devanagari",
        "style": "normal",
        "weight": "100 900",
        "filename": "noto-sans-devanagari-v2.007.ttf",
        "sha256": "14ec4af41f27482216d1c2229f417ff9b1425e1babb014e57d1d40d03229853e",
        "scripts": ["Devanagari"],
    },
    {
        "family": "Noto Sans SC",
        "style": "normal",
        "weight": "100 900",
        "filename": "noto-sans-sc-v2.004.ttf",
        "sha256": "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da",
        "scripts": ["Simplified Chinese (Han)"],
    },
)


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
        "preview": preview,
        "size": int(attachment.get("size", 0) or 0),
        "content_type": content_type,
        "inline": (
            content_type.startswith("image/") and not filename.lower().endswith((".svg", ".html", ".htm"))
        ),
        "spoiler": bool(attachment.get("spoiler", False)) or filename.startswith("SPOILER_"),
        "width": attachment.get("width"),
        "height": attachment.get("height"),
        "duration_secs": attachment.get("duration_secs"),
        "asset_id": asset_id,
        "available": _asset_available(page, asset_id),
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
            icon_id = _asset_meta(page, icon_url, None, None)
            if icon_id:
                owner_value["icon_asset_id"] = icon_id
                owner_value["icon_available"] = _asset_available(page, icon_id)
    by_url, by_name = _attachment_index(attachments)
    for key in ("image", "thumbnail", "video"):
        media = embed.get(key)
        if not isinstance(media, dict):
            continue
        url = media["url"]
        item = by_url.get(url)
        if item is None and url.startswith("attachment://"):
            item = by_name.get(url.removeprefix("attachment://"))
        asset_id = _asset_meta(page, url, item, message)
        if asset_id:
            value.setdefault(key, {})["asset_id"] = asset_id
            value[key]["available"] = _asset_available(page, asset_id)
            if item is not None:
                value[key]["attachment_id"] = str(item.get("id", ""))
    if isinstance(embed.get("title"), str):
        value["title_tokens"] = markdown_tokens(embed["title"], "embed_title", context=context)
    if isinstance(embed.get("description"), str):
        value["description_tokens"] = markdown_tokens(
            embed["description"], "embed_description", context=context
        )
    if isinstance(embed.get("footer"), dict) and isinstance(embed["footer"].get("text"), str):
        value["footer_tokens"] = markdown_tokens(embed["footer"]["text"], "embed_footer")
    for index, field in enumerate(embed.get("fields", [])):
        target = value["fields"][index]
        target["name_tokens"] = markdown_tokens(field["name"], "embed_field_name", context=context)
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
            "filename": sticker.filename or f"{sticker.id}.{suffix}",
            "content_type": sticker.content_type or content_type,
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
    if preview.layout == "channel":
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
        "components": _decorate_components(page, message, message.components, attachments, context),
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
                users[match.group(1)] = _identity_wire(resolve_identity(preview, page, user_id))["name"]

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


def _message_summary(preview: Preview, page: _Page, message: Message) -> dict[str, Any]:
    identity = _identity_wire(
        resolve_identity(preview, page, message.author_id, message=message, override=message.author_name)
    )
    return {
        "id": str(message.id),
        "author_name": identity["name"],
        "excerpt": message.content[:100],
    }


def _candidates(
    preview: Preview, page: _Page, components: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    env = preview.env
    channel = env.backend.get_channel(page.channel_id)
    result: dict[str, list[dict[str, Any]]] = {}
    for component in walk_components(components):
        kind = _ENTITY_TYPES.get(int(component.get("type", -1)))
        control_key = component.get("control_key")
        if kind is None or not isinstance(control_key, str):
            continue
        entries: list[dict[str, Any]] = []
        if channel.guild_id is None:
            user_ids = channel.recipient_ids if kind in {"users", "mentionables"} else ()
            for uid in user_ids:
                if not _user_allowed(preview, page, uid):
                    continue
                identity = _identity_wire(resolve_identity(preview, page, uid))
                user = env.backend.get_user(uid)
                entries.append(
                    {
                        **identity,
                        "id": str(uid),
                        "label": identity["name"],
                        "username": (
                            f"{user.name}#{user.discriminator}"
                            if user.discriminator not in ("0", "")
                            else user.name
                        ),
                        "kind": "user",
                    }
                )
        else:
            guild = env.backend.guilds[channel.guild_id]
            if kind in {"users", "mentionables"}:
                for uid in guild.members:
                    if not _user_allowed(preview, page, uid):
                        continue
                    identity = _identity_wire(resolve_identity(preview, page, uid))
                    user = env.backend.get_user(uid)
                    entries.append(
                        {
                            **identity,
                            "id": str(uid),
                            "label": identity["name"],
                            "username": (
                                f"{user.name}#{user.discriminator}"
                                if user.discriminator not in ("0", "")
                                else user.name
                            ),
                            "kind": "user",
                        }
                    )
            if kind in {"roles", "mentionables"}:
                for rid, role in guild.roles.items():
                    if rid != guild.id:
                        entries.append(
                            {
                                "id": str(rid),
                                "label": role.name,
                                "kind": "role",
                                "color": int(role.color or 0),
                                "icon_color": int(role.color or 0),
                                "members": sum(1 for m in guild.members.values() if rid in m.role_ids),
                            }
                        )
            if kind == "channels":
                allowed_types = component.get("channel_types")
                for candidate in sorted(
                    env.backend.channels.values(), key=lambda item: (item.position, item.id)
                ):
                    if candidate.guild_id != channel.guild_id or not can_access_channel(
                        env, candidate.id, page.viewer
                    ):
                        continue
                    if (
                        isinstance(allowed_types, list)
                        and allowed_types
                        and candidate.type not in allowed_types
                    ):
                        continue
                    entries.append(
                        {
                            "id": str(candidate.id),
                            "label": candidate.name or str(candidate.id),
                            "kind": "channel",
                            "type": candidate.type,
                        }
                    )
        result[control_key] = entries
    return result


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
    if preview.layout == "channel":
        end = len(visible) if page.window_end_id is None else bisect_right(ids, page.window_end_id)
        start = max(0, end - 50)
        if target_index is not None and not start <= target_index < end:
            page.window_end_id = ids[target_index]
            end = target_index + 1
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
        compact = preview.layout == "channel" and _is_compact_message(previous, item, preview.timezone, env)
        value = _message_projection(preview, page, item, compact=compact, channel=channel)
        projected[str(item.id)] = value
        candidate_components.extend(value.get("components", []))
        previous = item

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
        payload["application_name"] = _identity_wire(
            resolve_identity(preview, page, preview.env.backend.bot_user.id)
        )["name"]
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
        value = dict(item)
        value.setdefault("code", "preview")
        value.setdefault("severity", "warning")
        value.setdefault("message", "")
        value.setdefault("complete", False)
        diagnostics.append(value)
    for value in projected.values():
        if value["type_info"]["kind"] == "unknown":
            diagnostics.append(
                {
                    "code": "message_type_unknown",
                    "severity": "warning",
                    "message": f"Message type {value['type']} is not classified in this preview.",
                    "message_id": value["id"],
                    "complete": False,
                }
            )
        for sticker in value["stickers"]:
            if sticker["format_type"] != 1:
                diagnostics.append(
                    {
                        "code": "sticker_animation_unavailable",
                        "severity": "warning",
                        "message": f"Sticker {sticker['name']} uses an animated format not rendered in this preview.",
                        "message_id": value["id"],
                        "complete": False,
                    }
                )
            elif not sticker["available"]:
                diagnostics.append(
                    {
                        "code": "sticker_asset_unavailable",
                        "severity": "warning",
                        "message": f"Sticker {sticker['name']} has no available image asset.",
                        "message_id": value["id"],
                        "complete": False,
                    }
                )
    can_send = channel is not None and allowed and _can_send_message(preview, page, channel)
    snapshot = {
        "protocolVersion": _PROTOCOL_VERSION,
        "publishedRevision": page.revision,
        "context": {"id": page.id, "generation": page.generation},
        "botGeneration": env._generation,
        "viewers": [
            _identity_wire(resolve_identity(preview, page, _viewer_id(viewer))) for viewer in preview.viewers
        ],
        "viewerId": str(_viewer_id(page.viewer)),
        "channelId": str(page.channel_id),
        "layout": preview.layout,
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
        "messageIndex": [_message_summary(preview, page, item) for item in visible],
        "history": history,
        "modal": modal,
        "entities": entities,
        "candidates": _candidates(preview, page, candidate_components) if allowed else {},
        "profile": {
            "theme": "dark",
            "scope": "desktop-dark",
            "width": preview.width,
            "height": preview.height,
            "locale": preview.locale,
            "timezone": preview.timezone,
            "presentationTime": preview.capture_time.isoformat(),
            "fontStackConfigured": '"Noto Sans", "Noto Sans Arabic", "Noto Sans Hebrew", "Noto Sans Devanagari", "Noto Sans SC", sans-serif',
            "fontAssets": [dict(face) for face in _FONT_PROFILE],
            "fontResolution": "unavailable until Chromium platform-font inspection",
        },
        "status": page.status,
        "diagnostics": diagnostics,
        "lastAction": page.last_action,
    }
    preview._reconcile_assets(page)
    snapshot["assets"] = {asset_id: record.to_wire() for asset_id, record in page.assets.items()}
    return snapshot


__all__ = ["IdentityRecord", "build_snapshot", "resolve_identity"]
