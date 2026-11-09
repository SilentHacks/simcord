"""Current Backend/ID access facts and Env-bound viewer adapters.

Core simulation never imports the optional preview package. These predicates
are read-only; ordinary access denial is reported as ``False``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord

from ..enums import ChannelType
from .errors import BackendError, SetupError
from .models import Message

if TYPE_CHECKING:
    from ..env import Env
    from .ops.base import BackendBase


def _viewer_id(viewer: Any) -> int:
    try:
        value = viewer.id
    except AttributeError as exc:  # pragma: no cover - validated by handle construction
        raise SetupError("preview viewers must be UserHandle or MemberActor handles") from exc
    if not isinstance(value, int) or isinstance(value, bool):  # pragma: no cover - handle invariant
        raise SetupError("preview viewer id must be an integer")
    return value


def channel_accessible(
    backend: BackendBase,
    channel_id: int,
    viewer_id: int,
    *,
    history: bool = False,
    permissions_value: int | None = None,
) -> bool:
    """Current channel access facts, independent of Env-bound handle adapters."""
    channel = backend.channels.get(channel_id)
    if channel is None:
        return False
    if channel.guild_id is None:
        return viewer_id == backend.bot_user.id or (
            viewer_id in channel.recipient_ids and backend.dm_channels.get(viewer_id) == channel.id
        )
    guild = backend.guilds.get(channel.guild_id)
    if guild is None or viewer_id not in guild.members:
        return False
    try:
        permissions = (
            backend.compute_permissions(channel.guild_id, viewer_id, channel.id)
            if permissions_value is None
            else permissions_value
        )
    except BackendError:  # pragma: no cover - validated guild/channel pair
        return False
    if not permissions & discord.Permissions.view_channel.flag:
        return False
    if history and not permissions & discord.Permissions.read_message_history.flag:
        return False
    if channel.type == ChannelType.PRIVATE_THREAD:
        privileged = (
            bool(
                permissions
                & (discord.Permissions.administrator.flag | discord.Permissions.manage_threads.flag)
            )
            or viewer_id == guild.owner_id
        )
        if viewer_id not in channel.thread_members and not privileged:
            return False
    return True


def can_access_channel(env: Env, channel_id: int, viewer: Any, *, history: bool = False) -> bool:
    """Env-bound adapter used by public actors and the optional preview bridge."""
    if getattr(viewer, "_env", None) is not env:
        return False
    return channel_accessible(env.backend, channel_id, _viewer_id(viewer), history=history)


def message_accessible(
    backend: BackendBase, channel_id: int, message: Message, viewer_id: int, *, history: bool = False
) -> bool:
    return (
        message.channel_id == channel_id
        and channel_accessible(backend, channel_id, viewer_id, history=history)
        and message.visible_to(viewer_id)
    )


def can_access_message(
    env: Env, channel_id: int, message: Message, viewer: Any, *, history: bool = False
) -> bool:
    if getattr(viewer, "_env", None) is not env:
        return False
    return message_accessible(env.backend, channel_id, message, _viewer_id(viewer), history=history)


def reaction_permissions(message: Message, emoji: str, *, reacted: bool = True) -> tuple[str, ...]:
    """Joining an existing emoji or removing oneself only requires history."""
    if reacted and message.reaction_for(emoji) is None:
        return ("read_message_history", "add_reactions")
    return ("read_message_history",)


__all__ = [
    "can_access_channel",
    "can_access_message",
    "channel_accessible",
    "message_accessible",
    "reaction_permissions",
]
