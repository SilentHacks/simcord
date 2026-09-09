"""Viewer-authorized, token-free preview projections."""
from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

import discord

from ..backend.errors import BackendError, SetupError
from ..backend.models import EPHEMERAL_FLAG, Message
from ..enums import ChannelType, ComponentType

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
    except AttributeError as exc:
        raise SetupError("preview viewers must be UserHandle or MemberActor handles") from exc
    if not isinstance(value, int) or isinstance(value, bool):
        raise SetupError("preview viewer id must be an integer")
    return value


def can_access_channel(env: Env, channel_id: int, viewer: Any, *, history: bool = False) -> bool:
    """The one current access predicate shared by preview and actor message access."""
    if getattr(viewer, "_env", None) is not env:
        return False
    try:
        channel = env.backend.get_channel(channel_id)
    except BackendError:
        return False
    viewer_id = _viewer_id(viewer)
    if channel.guild_id is None:
        return viewer_id in channel.recipient_ids and env.backend.dm_channels.get(viewer_id) == channel.id
    guild = env.backend.guilds.get(channel.guild_id)
    if guild is None or viewer_id not in guild.members:
        return False
    try:
        permissions = env.backend.compute_permissions(channel.guild_id, viewer_id, channel.id)
    except BackendError:
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


def can_access_message(env: Env, channel_id: int, message: Message, viewer: Any, *, history: bool = False) -> bool:
    if message.channel_id != channel_id:
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


def _author(env: Env, user_id: int, *, override: str | None = None) -> dict[str, Any]:
    user = env.backend.get_user(user_id)
    name = override if override is not None else user.global_name or user.name
    return {"id": str(user.id), "name": name, "username": user.name, "bot": bool(user.bot)}


def _attachment(env: Env, message: Message, attachment: dict[str, Any], page: _Page) -> dict[str, Any]:
    attachment_id = str(attachment.get("id", ""))
    asset_id = page.asset_id(f"attachment:{message.channel_id}:{message.id}:{attachment_id}", attachment)
    return {
        "id": attachment_id,
        "filename": str(attachment.get("filename", "attachment")),
        "size": int(attachment.get("size", 0) or 0),
        "content_type": attachment.get("content_type"),
        "asset_id": asset_id,
    }


def _message_projection(preview: Preview, page: _Page, message: Message) -> dict[str, Any]:
    env = preview.env
    data = {
        "id": str(message.id),
        "channel_id": str(message.channel_id),
        "author": _author(env, message.author_id, override=message.author_name),
        "timestamp": message.timestamp,
        "edited_timestamp": message.edited_timestamp,
        "content": message.content,
        "embeds": _clean(deepcopy(message.embeds), drop_urls=True),
        "components": _clean(deepcopy(message.components), drop_urls=True),
        "flags": int(message.flags),
        "ephemeral": bool(message.flags & EPHEMERAL_FLAG),
        "attachments": [_attachment(env, message, item, page) for item in message.attachments],
        "mention_user_ids": [str(uid) for uid in message.mention_user_ids if _mention_allowed(preview, page, uid)],
        "mention_role_ids": [str(rid) for rid in message.mention_role_ids if _role_allowed(preview, page, rid)],
    }
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
    return channel.guild_id is not None and role_id in env.backend.guilds[channel.guild_id].roles


def _components(page: _Page) -> list[dict[str, Any]]:
    if page.target_id is None:
        return []
    try:
        message = page.preview.env.backend.get_message(page.channel_id, page.target_id)
    except BackendError:
        return []
    return message.components


def _walk(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(row)
        for key in ("components", "component"):
            child = row.get(key)
            if isinstance(child, dict):
                out.extend(_walk([child]))
            elif isinstance(child, list):
                out.extend(_walk(child))
    return out


def _candidates(preview: Preview, page: _Page, components: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
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
                for uid in channel.recipient_ids:
                    if _user_allowed(preview, page, uid):
                        user = env.backend.get_user(uid)
                        entries.append({"id": str(uid), "label": user.global_name or user.name, "kind": "user"})
        else:
            guild = env.backend.guilds.get(channel.guild_id)
            if guild is None:
                continue
            if kind in {"users", "mentionables"}:
                for uid, member in guild.members.items():
                    if can_access_channel(env, channel.id, page.viewer) and _user_allowed(preview, page, uid):
                        user = env.backend.get_user(uid)
                        entries.append({"id": str(uid), "label": member.nick or user.global_name or user.name, "kind": "user"})
            if kind in {"roles", "mentionables"}:
                for rid, role in guild.roles.items():
                    entries.append({"id": str(rid), "label": role.name, "kind": "role"})
            if kind == "channels":
                for candidate in env.backend.channels.values():
                    if candidate.guild_id == channel.guild_id and can_access_channel(env, candidate.id, page.viewer):
                        entries.append({"id": str(candidate.id), "label": candidate.name or str(candidate.id), "kind": "channel"})
        result[custom_id] = entries
    return result


def build_snapshot(preview: Preview, page: _Page) -> dict[str, Any]:
    """Build a detached projection for one page; no backend dictionaries escape."""
    env = preview.env
    channel = env.backend.get_channel(page.channel_id)
    allowed = can_access_channel(env, channel.id, page.viewer, history=True)
    messages: list[Message] = []
    if allowed:
        for message in sorted(env.backend.messages.get(channel.id, {}).values(), key=lambda item: item.id):
            if can_access_message(env, channel.id, message, page.viewer, history=True):
                messages.append(message)
    selected = None
    if page.target_id is not None:
        selected_message = next((item for item in messages if item.id == page.target_id), None)
        if selected_message is not None:
            selected = _message_projection(preview, page, selected_message)
    modal = None
    if page.modal is not None and page.modal._interaction.user_id == _viewer_id(page.viewer):
        modal = {"handle": page.modal_handle, "payload": _clean(deepcopy(page.modal.modal), drop_urls=True)}
    return {
        "protocolVersion": _PROTOCOL_VERSION,
        "publishedRevision": page.revision,
        "context": {"id": page.id, "generation": page.generation},
        "botGeneration": env._generation,
        "viewers": [_author(env, _viewer_id(viewer)) for viewer in preview.viewers],
        "viewerId": str(_viewer_id(page.viewer)),
        "channelId": str(channel.id),
        "channel": {"id": str(channel.id), "name": channel.name, "guildId": str(channel.guild_id) if channel.guild_id else None},
        "targetId": str(page.target_id) if page.target_id is not None else None,
        "messages": [_message_projection(preview, page, item) for item in messages],
        "selected": selected,
        "modal": modal,
        "candidates": _candidates(preview, page, (selected or {}).get("components", [])) if selected else {},
        "assets": dict(page.assets),
        "status": page.status,
        "diagnostics": list(page.diagnostics),
        "lastAction": page.last_action,
    }


__all__ = ["build_snapshot", "can_access_channel", "can_access_message"]
