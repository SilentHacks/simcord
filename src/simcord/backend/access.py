"""Viewer access predicates shared by actors and the optional preview bridge.

These live in the core backend so core simulation never imports the optional
preview package. Both predicates are read-only and raise ``SetupError`` only
for malformed viewers; ordinary denial is reported as ``False``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord

from ..enums import ChannelType
from .errors import BackendError, SetupError
from .models import Message

if TYPE_CHECKING:
    from ..env import Env


def _viewer_id(viewer: Any) -> int:
    try:
        value = viewer.id
    except AttributeError as exc:  # pragma: no cover - validated by handle construction
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


__all__ = ["can_access_channel", "can_access_message"]
