"""Viewer-authorized, token-free preview projections."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import discord

from ..backend.errors import BackendError, SetupError
from ..backend.models import EPHEMERAL_FLAG, Message
from ..components import COMPONENTS_V2_FLAG
from ..enums import ChannelType, ComponentType
from ._markdown import markdown_tokens

if TYPE_CHECKING:
    from ..env import Env
    from . import Preview, _Page

_PROTOCOL_VERSION = 1
_ENTITY_TYPES = {
    int(ComponentType.USER_SELECT): "users",
    int(ComponentType.ROLE_SELECT): "roles",
    int(ComponentType.CHANNEL_SELECT): "channels",
    int(ComponentType.MENTIONABLE_SELECT): "mentionables",
}


def _viewer_id(viewer: Any) -> int:
    try:
        value = viewer.id
    except AttributeError as exc:  # pragma: no cover - validated by Preview construction
        raise SetupError("preview viewers must be UserHandle or MemberActor handles") from exc
    if not isinstance(value, int) or isinstance(value, bool):  # pragma: no cover - handle invariant
        raise SetupError("preview viewer id must be an integer")
    return value


def can_access_channel(env: Env, channel_id: int, viewer: Any, *, history: bool = False) -> bool:
    """The one current access predicate shared by preview and actor message access."""
    if getattr(viewer, "_env", None) is not env:  # pragma: no cover - validated by Preview construction
        return False
    try:
        channel = env.backend.get_channel(channel_id)
    except BackendError:  # pragma: no cover - callers resolve the channel first
        return False
    viewer_id = _viewer_id(viewer)
    if channel.guild_id is None:
        return viewer_id in channel.recipient_ids and env.backend.dm_channels.get(viewer_id) == channel.id
    guild = env.backend.guilds.get(channel.guild_id)
    if guild is None or viewer_id not in guild.members:  # pragma: no cover - handle invariant
        return False
    try:
        permissions = env.backend.compute_permissions(channel.guild_id, viewer_id, channel.id)
    except BackendError:  # pragma: no cover - validated guild/channel pair
        return False
    if not permissions & discord.Permissions.view_channel.flag:
        return False
    if history and not permissions & discord.Permissions.read_message_history.flag:
        return False
    if channel.type == ChannelType.PRIVATE_THREAD:
        privileged = bool(permissions & discord.Permissions.administrator.flag) or viewer_id == guild.owner_id
        if viewer_id not in channel.thread_members and not privileged:
            return False
    return True


def can_access_message(
    env: Env, channel_id: int, message: Message, viewer: Any, *, history: bool = False
) -> bool:
    if message.channel_id != channel_id:  # pragma: no cover - messages are loaded from this channel
        return False
    if not can_access_channel(env, channel_id, viewer, history=history):
        return False
    return message.visible_to(_viewer_id(viewer))


def _clean(value: Any, *, drop_urls: bool = False) -> Any:
    """Copy allowlisted-ish backend data without recursive private payloads."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"token", "webhook_token", "proxy_url", "referenced_message", "resolved"}:
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


def _author(env: Env, user_id: int, *, override: str | None = None) -> dict[str, Any]:
    user = env.backend.get_user(user_id)
    name = override if override is not None else user.global_name or user.name
    return {"id": str(user.id), "name": name, "username": user.name, "bot": bool(user.bot)}


def _attachment(env: Env, message: Message, attachment: dict[str, Any], page: _Page) -> dict[str, Any]:
    attachment_id = str(attachment.get("id", ""))
    url = attachment.get("url")
    content_type = str(attachment.get("content_type") or "application/octet-stream")
    filename = str(attachment.get("filename", "attachment"))
    key = (
        f"url:{url}"
        if isinstance(url, str)
        else f"attachment:{message.channel_id}:{message.id}:{attachment_id}"
    )
    asset_id = page.asset_id(key, attachment)
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
        "inline": content_type.startswith("image/"),
        "spoiler": bool(attachment.get("spoiler", False)) or filename.startswith("SPOILER_"),
        "width": attachment.get("width"),
        "height": attachment.get("height"),
        "duration_secs": attachment.get("duration_secs"),
        "asset_id": asset_id,
        "available": bool(page.assets.get(asset_id, {}).get("available", False)),
    }


def _asset_meta(page: _Page, url: Any, fallback: dict[str, Any] | None = None) -> str | None:
    if not isinstance(url, str) or not url:  # pragma: no cover - component schema requires a URL
        return None
    metadata = dict(fallback or {})
    metadata.setdefault("url", url)
    return page.asset_id(f"url:{metadata['url']}", metadata)


def _decorate_components(page: _Page, components: Any, attachments: list[dict[str, Any]]) -> Any:
    rows = deepcopy(components)
    by_url = {str(item.get("url")): item for item in attachments if isinstance(item.get("url"), str)}
    by_name = {str(item.get("filename")): item for item in attachments if item.get("filename") is not None}

    def visit(node: Any) -> None:
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
            asset_id = cast(str, _asset_meta(page, url, attachment))
            media["asset_id"] = asset_id
            media["available"] = bool(page.assets.get(asset_id, {}).get("available", False))
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
            node["markdown_tokens"] = markdown_tokens(node["content"], "text_display")
        for key in ("components", "component", "accessory"):
            child = node.get(key)
            if isinstance(child, dict):
                visit(child)
            elif isinstance(child, list):
                for item in child:
                    visit(item)

    for row in rows:
        visit(row)
    return _clean(rows)


def _embed_projection(
    page: _Page, embed: dict[str, Any], attachments: list[dict[str, Any]]
) -> dict[str, Any]:
    value = _clean(deepcopy(embed), drop_urls=True)
    link = _safe_link(embed.get("url"))
    if link:
        value["url"] = link
    by_url = {str(item.get("url")): item for item in attachments if isinstance(item.get("url"), str)}
    by_name = {str(item.get("filename")): item for item in attachments if item.get("filename") is not None}
    for key in ("image", "thumbnail", "video"):
        media = embed.get(key)
        if not isinstance(media, dict):
            continue
        url = media["url"]
        item = by_url.get(url)
        if item is None and url.startswith("attachment://"):
            item = by_name.get(url.removeprefix("attachment://"))
        asset_id = _asset_meta(page, url, item)
        if asset_id:
            value.setdefault(key, {})["asset_id"] = asset_id
            value[key]["available"] = bool(page.assets.get(asset_id, {}).get("available", False))
            if item is not None:
                value[key]["attachment_id"] = str(item.get("id", ""))
    for key in ("title", "description"):
        if isinstance(embed.get(key), str):
            value[f"{key}_tokens"] = markdown_tokens(
                embed[key], "embed_title" if key == "title" else "embed_description"
            )
    if isinstance(embed.get("footer"), dict) and isinstance(embed["footer"].get("text"), str):
        value["footer_tokens"] = markdown_tokens(embed["footer"]["text"], "embed_footer")
    for index, field in enumerate(embed.get("fields", [])):
        target = value["fields"][index]
        target["name_tokens"] = markdown_tokens(field["name"], "embed_field")
        target["value_tokens"] = markdown_tokens(field["value"], "embed_field")
    return value


def _is_compact_message(preview: Preview, message: Message) -> bool:
    previous = max(
        (item for item in preview.env.backend.messages.get(message.channel_id, {}).values() if item.id < message.id),
        key=lambda item: item.id,
        default=None,
    )
    return (
        previous is not None
        and previous.author_id == message.author_id
        and message.id - previous.id < 7 * 60 * 1000 * (1 << 22)
    )


def _message_projection(preview: Preview, page: _Page, message: Message) -> dict[str, Any]:
    env = preview.env
    attachments = list(message.attachments)
    data = {
        "id": str(message.id),
        "channel_id": str(message.channel_id),
        "author": _author(env, message.author_id, override=message.author_name),
        "timestamp": message.timestamp,
        "edited_timestamp": message.edited_timestamp,
        "content": message.content,
        "content_tokens": markdown_tokens(message.content, "message"),
        "embeds": [_embed_projection(page, item, attachments) for item in message.embeds],
        "components": _decorate_components(page, message.components, attachments),
        "flags": int(message.flags),
        "ephemeral": bool(message.flags & EPHEMERAL_FLAG),
        "components_v2": bool(int(message.flags) & COMPONENTS_V2_FLAG),
        "compact": _is_compact_message(preview, message),
        "attachments": [_attachment(env, message, item, page) for item in attachments],
        "mention_user_ids": [
            str(uid) for uid in message.mention_user_ids if _mention_allowed(preview, page, uid)
        ],
        "mention_role_ids": [
            str(rid) for rid in message.mention_role_ids if _role_allowed(preview, page, rid)
        ],
    }
    data["mention_names"] = {}
    for uid in data["mention_user_ids"]:
        data["mention_names"][uid] = _author(env, int(uid))["name"]
    reference = message.reference
    if reference:
        try:
            reference_id = reference.get("message_id")
            if reference_id is None:
                raise ValueError
            referenced_id = int(reference_id)
            referenced = env.backend.get_message(message.channel_id, referenced_id)
        except (BackendError, TypeError, ValueError):
            referenced = None
        if referenced is not None and can_access_message(env, message.channel_id, referenced, page.viewer):
            data["reference"] = {"message_id": str(referenced.id), "channel_id": str(referenced.channel_id)}
    return data


def _mention_allowed(preview: Preview, page: _Page, user_id: int) -> bool:
    return _user_allowed(preview, page, user_id)


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


def _walk(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(row)
        for key in ("components", "component", "accessory"):
            child = row.get(key)
            if isinstance(child, dict):
                out.extend(_walk([child]))
            elif isinstance(child, list):
                out.extend(_walk(child))
    return out


def _candidates(
    preview: Preview, page: _Page, components: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    env = preview.env
    channel = env.backend.get_channel(page.channel_id)
    result: dict[str, list[dict[str, Any]]] = {}
    for component in _walk(components):
        kind = _ENTITY_TYPES.get(int(component.get("type", -1)))
        custom_id = component.get("custom_id")
        if kind is None or not isinstance(custom_id, str):
            continue
        entries: list[dict[str, Any]] = []
        if channel.guild_id is None:
            if kind in {"users", "mentionables"}:
                for uid in channel.recipient_ids:  # pragma: no branch - bounded fixture collection
                    if _user_allowed(preview, page, uid):
                        user = env.backend.get_user(uid)
                        entries.append(
                            {
                                "id": str(uid),
                                "label": user.global_name or user.name,
                                "kind": "user",
                                "username": f"{user.name}#{user.discriminator}"
                                if user.discriminator not in ("0", "")
                                else user.name,
                                "bot": user.bot,
                            }
                        )
        else:
            guild = env.backend.guilds[channel.guild_id]
            if kind in {"users", "mentionables"}:
                for uid, member in guild.members.items():  # pragma: no branch - bounded fixture collection
                    if _user_allowed(preview, page, uid):
                        user = env.backend.get_user(uid)
                        entries.append(
                            {
                                "id": str(uid),
                                "label": member.nick or user.global_name or user.name,
                                "kind": "user",
                                "username": f"{user.name}#{user.discriminator}"
                                if user.discriminator not in ("0", "")
                                else user.name,
                                "bot": user.bot,
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
                                "color": int(getattr(role, "color", 0) or 0),
                                "icon_color": int(getattr(role, "icon_color", 0) or 0),
                                "members": sum(
                                    1 for m in guild.members.values() if rid in m.role_ids
                                ),
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
        result[custom_id] = entries
    return result


def build_snapshot(preview: Preview, page: _Page) -> dict[str, Any]:
    """Build a detached projection for one page; no backend dictionaries escape."""
    env = preview.env
    channel = env.backend.get_channel(page.channel_id)
    allowed = can_access_channel(env, channel.id, page.viewer, history=True)
    messages: list[Message] = []
    if allowed:
        # ponytail: explicit refresh rebuilds the small in-memory world; add an
        # index only when previews routinely exceed fixture-sized histories.
        for message in sorted(env.backend.messages.get(channel.id, {}).values(), key=lambda item: item.id):
            if can_access_message(env, channel.id, message, page.viewer, history=True):
                messages.append(message)
    selected = None
    if page.target_id is not None:
        selected_message = next((item for item in messages if item.id == page.target_id), None)
        if selected_message is not None:
            selected = _message_projection(preview, page, selected_message)
    modal = None
    if page.modal is not None:
        payload = _clean(deepcopy(page.modal.modal), drop_urls=True)
        for component in _walk(payload.get("components", [])):
            if isinstance(component.get("content"), str):
                component["markdown_tokens"] = markdown_tokens(component["content"], "text_display")
        payload["application_name"] = _author(preview.env, preview.env.backend.bot_user.id)["name"]
        modal = {"handle": page.modal_handle, "payload": payload}
    candidate_components = list((selected or {}).get("components", []))
    if modal is not None:
        candidate_components.extend(modal["payload"].get("components", []))
    return {
        "protocolVersion": _PROTOCOL_VERSION,
        "publishedRevision": page.revision,
        "context": {"id": page.id, "generation": page.generation},
        "botGeneration": env._generation,
        "viewers": [_author(env, _viewer_id(viewer)) for viewer in preview.viewers],
        "viewerId": str(_viewer_id(page.viewer)),
        "channelId": str(channel.id),
        "channel": {
            "id": str(channel.id),
            "name": channel.name,
            "guildId": str(channel.guild_id) if channel.guild_id else None,
        },
        "targetId": str(page.target_id) if page.target_id is not None else None,
        "messages": [_message_projection(preview, page, item) for item in messages],
        "selected": selected,
        "modal": modal,
        "candidates": _candidates(preview, page, candidate_components),
        "assets": dict(page.assets),
        "profile": {
            "theme": preview.theme,
            "width": preview.width,
            "height": preview.height,
            "locale": preview.locale,
            "timezone": preview.timezone,
        },
        "status": page.status,
        "diagnostics": list(page.diagnostics),
        "lastAction": page.last_action,
    }


__all__ = ["build_snapshot", "can_access_channel", "can_access_message"]
