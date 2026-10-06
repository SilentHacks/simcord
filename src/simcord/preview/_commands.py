"""Command catalog projection and current command resolution for Preview."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import discord

from ..backend.access import can_access_channel
from ..backend.errors import BackendError, SetupError
from ..enums import AppCommandType, OptionType
from ..interactions import command_leaves
from ._identity import resolve_identity

if TYPE_CHECKING:
    from . import Preview
    from ._pages import _Page

MAX_CATALOG_LEAVES = 1_000
MAX_CATALOG_BYTES = 2 * 1024 * 1024

_OPTION_TYPES = {
    int(OptionType.STRING): "string",
    int(OptionType.INTEGER): "integer",
    int(OptionType.NUMBER): "number",
    int(OptionType.BOOLEAN): "boolean",
    int(OptionType.USER): "user",
    int(OptionType.CHANNEL): "channel",
    int(OptionType.ROLE): "role",
    int(OptionType.MENTIONABLE): "mentionable",
    int(OptionType.ATTACHMENT): "attachment",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def unavailable_catalog() -> dict[str, Any]:
    fingerprint = "cf_" + hashlib.sha256(_canonical([])).hexdigest()[:32]
    return {
        "protocolVersion": 3,
        "fingerprint": fingerprint,
        "state": "unavailable",
        "truncated": False,
        "application": None,
        "entries": [],
    }


def command_permission(preview: Preview, page: _Page) -> bool:
    """Whether this viewer may use application commands in the bound channel."""
    if not can_access_channel(preview.env, page.channel_id, page.viewer, history=True):
        return False
    channel = preview.env.backend.channels.get(page.channel_id)
    if channel is None:
        return False
    if channel.guild_id is None:
        return True
    permissions = discord.Permissions(
        preview.env.backend.compute_permissions(channel.guild_id, page.viewer.id, page.channel_id)
    )
    return permissions.administrator or permissions.use_application_commands


def _option_wire(option: Mapping[str, Any]) -> dict[str, Any]:
    option_type = _OPTION_TYPES.get(int(option.get("type", -1)), "string")
    return {
        "name": str(option.get("name", "")),
        "description": str(option.get("description", "")),
        "type": option_type,
        "required": bool(option.get("required", False)),
        "choices": [
            {"name": str(choice.get("name", "")), "value": choice.get("value")}
            for choice in option.get("choices") or []
            if isinstance(choice, Mapping)
        ],
        "autocomplete": bool(option.get("autocomplete", False)),
        "minLength": option.get("min_length"),
        "maxLength": option.get("max_length"),
        "minValue": option.get("min_value"),
        "maxValue": option.get("max_value"),
        "channelTypes": list(option["channel_types"]) if option.get("channel_types") is not None else None,
        "fileTypes": list(option["file_types"]) if option.get("file_types") is not None else None,
    }


def visible_leaf(
    preview: Preview,
    page: _Page,
    command_id: Any,
    path: Any,
) -> tuple[dict[str, Any], list[str], dict[str, Any]] | None:
    """Re-resolve an ID/path against commands visible to this page's viewer."""
    if (
        not isinstance(command_id, str)
        or not command_id.isascii()
        or not command_id.isdecimal()
        or not isinstance(path, list)
        or not path
        or any(not isinstance(part, str) or not part for part in path)
    ):
        return None
    try:
        visible = preview.env.backend.visible_commands(user_id=page.viewer.id, channel_id=page.channel_id)
    except (BackendError, KeyError, SetupError):
        return None
    for command in visible:
        if str(command.get("id")) != command_id:
            continue
        for leaf_path, leaf in command_leaves(command):
            if leaf_path == path:
                return command, leaf_path, leaf
    return None


def entry_for_leaf(command: Mapping[str, Any], path: list[str], leaf: Mapping[str, Any]) -> dict[str, Any]:
    root_id = str(command["id"])
    fingerprint = (
        "sf_" + hashlib.sha256(_canonical({"id": root_id, "path": path, "leaf": leaf})).hexdigest()[:32]
    )
    return {
        "key": f"{root_id}:{'.'.join(path)}",
        "commandId": root_id,
        "path": list(path),
        "invocation": " ".join(path),
        "description": str(leaf.get("description", "")),
        "scope": "guild" if command.get("guild_id") is not None else "global",
        "schemaFingerprint": fingerprint,
        "options": [_option_wire(option) for option in leaf.get("options") or []],
    }


def build_catalog(preview: Preview, page: _Page) -> dict[str, Any]:
    """Build a viewer-authorized, bounded wire catalog for one published page."""
    if page.status == "access_denied" or not can_access_channel(
        preview.env, page.channel_id, page.viewer, history=True
    ):
        return unavailable_catalog()

    identity = resolve_identity(preview, page, preview.env.backend.bot_user.id)
    application = {
        "id": str(identity.id),
        "name": str(identity.name),
        "avatarAssetId": identity.avatar,
    }
    commands = preview.env.backend.visible_commands(user_id=page.viewer.id, channel_id=page.channel_id)
    entries: list[dict[str, Any]] = []
    truncated = False
    empty_catalog = _available_catalog(application, [], truncated=False)
    serialized_size = len(_canonical(empty_catalog)) + len("available") - len("empty")
    for command in commands:
        for path, leaf in command_leaves(command):
            if len(entries) >= MAX_CATALOG_LEAVES:
                truncated = True
                break
            entry = entry_for_leaf(command, path, leaf)
            entry_size = len(_canonical(entry)) + (1 if entries else 0)
            if serialized_size + entry_size > MAX_CATALOG_BYTES:
                truncated = True
                break
            entries.append(entry)
            serialized_size += entry_size
        if truncated:
            break
    return _available_catalog(application, entries, truncated=truncated)


def _available_catalog(
    application: dict[str, Any], entries: list[dict[str, Any]], *, truncated: bool
) -> dict[str, Any]:
    fingerprints = [entry["schemaFingerprint"] for entry in entries]
    fingerprint = "cf_" + hashlib.sha256(_canonical(fingerprints)).hexdigest()[:32]
    return {
        "protocolVersion": 3,
        "fingerprint": fingerprint,
        "state": "available" if entries else "empty",
        "truncated": truncated,
        "application": application,
        "entries": entries,
    }


def unsynced_commands(preview: Preview, page: _Page) -> bool:
    """Whether the tree has chat-input roots missing from their backend scope."""
    tree = getattr(preview.env.bot, "tree", None)
    if tree is None:
        return False
    channel = preview.env.backend.channels.get(page.channel_id)
    scopes: tuple[int | None, ...] = (None,)
    if channel is not None and channel.guild_id is not None:
        scopes = (channel.guild_id, None)
    for guild_id in scopes:
        scope = discord.Object(guild_id) if guild_id is not None else None
        for command in tree.get_commands(guild=scope, type=discord.AppCommandType.chat_input):
            registered = preview.env.backend.commands.get(guild_id, {})
            if (command.name, int(AppCommandType.CHAT_INPUT)) not in registered:
                return True
    return False


__all__ = [
    "MAX_CATALOG_BYTES",
    "MAX_CATALOG_LEAVES",
    "build_catalog",
    "command_permission",
    "entry_for_leaf",
    "unavailable_catalog",
    "unsynced_commands",
    "visible_leaf",
]
