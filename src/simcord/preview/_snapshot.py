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

import hashlib
from bisect import bisect_right
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .. import __version__ as _RUNTIME_VERSION
from ..backend.access import _viewer_id, can_access_channel, can_access_message
from ..backend.errors import BackendError
from ..backend.models import Message
from ..components import walk_components
from ..enums import ComponentType
from ._component_projection import _annotate_tree, _clean, _decorate_emoji, _decorate_markdown_emoji
from ._diagnostics import make_diagnostic
from ._identity import _identity_wire, resolve_identity
from ._markdown import markdown_tokens
from ._messages import (
    _can_send_message,
    _is_compact_message,
    _markdown_context,
    _message_projection,
    _user_allowed,
)
from ._pages import HISTORY_WINDOW_SIZE
from ._queries import _candidates, _message_navigation

if TYPE_CHECKING:
    from . import Preview
    from ._pages import _Page

_PROTOCOL_VERSION = 3


def _history_window(
    page: _Page, visible: list[Message]
) -> tuple[Message | None, list[Message], dict[str, Any]]:
    ids = [item.id for item in visible]
    target_index = next((index for index, item in enumerate(visible) if item.id == page.target_id), None)
    target = visible[target_index] if target_index is not None else None
    if page.layout == "channel":
        if target_index is not None and page.window_end_id == page.target_id:
            start = max(0, target_index - 24)
            end = min(len(visible), start + HISTORY_WINDOW_SIZE)
            start = max(0, end - HISTORY_WINDOW_SIZE)
            page.window_end_id = ids[end - 1] if end and end < len(visible) else None
        else:
            end = len(visible) if page.window_end_id is None else bisect_right(ids, page.window_end_id)
            start = max(0, end - HISTORY_WINDOW_SIZE)
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
    return target, window, history


def _modal_projection(preview: Preview, page: _Page, channel: Any, allowed: bool) -> dict[str, Any] | None:
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
        modal = {"handle": page.modal_handle, "payload": payload}
    return modal


def _entity_projection(
    preview: Preview, page: _Page, channel: Any, target: Message | None, allowed: bool
) -> dict[str, dict[str, dict[str, Any]]]:
    env = preview.env
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
    return entities


def _snapshot_diagnostics(
    preview: Preview, page: _Page, projected: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
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
    return diagnostics


def build_snapshot(preview: Preview, page: _Page) -> dict[str, Any]:
    """Build a detached projection for one page; no backend dictionaries escape."""
    env = preview.env
    try:
        channel = env.backend.get_channel(page.channel_id)
    except BackendError:
        channel = None
    allowed = channel is not None and can_access_channel(env, channel.id, page.viewer, history=True)
    page.referenced_assets.clear()
    application = page.command_catalog.get("application")
    if isinstance(application, Mapping) and isinstance(application.get("avatarAssetId"), str):
        if application["avatarAssetId"] in page.assets:
            page.referenced_assets.add(application["avatarAssetId"])
    page.referenced_messages.clear()
    visible: list[Message] = []
    if channel is not None and allowed:
        # Rebuild fixture-sized history; an index would need its own
        # invalidation and authorization model.
        visible = [
            item
            for item in sorted(env.backend.messages.get(channel.id, {}).values(), key=lambda item: item.id)
            if can_access_message(env, channel.id, item, page.viewer, history=True)
        ]
    target, window, history = _history_window(page, visible)
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

    modal = _modal_projection(preview, page, channel, allowed)
    if modal is not None:
        candidate_components.extend(modal["payload"].get("components", []))
    entities = _entity_projection(preview, page, channel, target, allowed)
    diagnostics = _snapshot_diagnostics(preview, page, projected)
    candidates = _candidates(preview, page, candidate_components, diagnostics) if allowed else {}
    if not allowed:
        for key in tuple(page.candidate_queries):
            if key.startswith("command:"):
                page.candidate_queries.pop(key, None)
                diagnostics.append(make_diagnostic("command-unavailable", state="recovered"))
    diagnostics = list({item["id"]: item for item in diagnostics}.values())
    can_send = channel is not None and allowed and _can_send_message(preview, page, channel)
    from ._commands import command_permission

    can_use_commands = channel is not None and allowed and command_permission(preview, page)
    catalog = page.command_catalog
    command_manifest = {
        "state": catalog.get("state", "unavailable" if not allowed else "empty"),
        "fingerprint": catalog.get("fingerprint", "cf_" + hashlib.sha256(b"[]").hexdigest()[:32]),
        "count": len(catalog.get("entries", [])) if allowed else 0,
    }
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
        "commands": command_manifest,
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
            "recipient": (
                _identity_wire(resolve_identity(preview, page, env.backend.bot_user.id))
                if channel is not None and channel.guild_id is None and allowed
                else None
            ),
            "guildId": (
                str(channel.guild_id)
                if channel is not None and channel.guild_id is not None and allowed
                else None
            ),
            "type": int(channel.type) if channel is not None and allowed else None,
            "topic": channel.topic if channel is not None and allowed else None,
            "canSendMessages": can_send,
            "canUseApplicationCommands": can_use_commands,
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


__all__ = ["build_snapshot"]
