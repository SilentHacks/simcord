"""Discord wire-protocol enumerations used when building/parsing payloads.

These mirror the integer values Discord sends so the framework reads as the
protocol does, instead of scattering bare magic numbers across the actors,
payload builders and route handlers. They are ``IntEnum`` so they compare and
serialize transparently as the ints discord.py expects.
"""

from __future__ import annotations

from enum import IntEnum


class OptionType(IntEnum):
    """Application-command option types."""

    SUBCOMMAND = 1
    SUBCOMMAND_GROUP = 2
    STRING = 3
    INTEGER = 4
    BOOLEAN = 5
    USER = 6
    CHANNEL = 7
    ROLE = 8
    MENTIONABLE = 9
    NUMBER = 10
    ATTACHMENT = 11


class AppCommandType(IntEnum):
    """Application-command types (slash, user context menu, message context menu)."""

    CHAT_INPUT = 1
    USER = 2
    MESSAGE = 3


class InteractionType(IntEnum):
    """Incoming interaction types."""

    PING = 1
    APPLICATION_COMMAND = 2
    MESSAGE_COMPONENT = 3
    APPLICATION_COMMAND_AUTOCOMPLETE = 4
    MODAL_SUBMIT = 5


class ComponentType(IntEnum):
    """Message-component types."""

    ACTION_ROW = 1
    BUTTON = 2
    STRING_SELECT = 3
    TEXT_INPUT = 4
    USER_SELECT = 5
    ROLE_SELECT = 6
    MENTIONABLE_SELECT = 7
    CHANNEL_SELECT = 8


#: All select-menu component types (string + entity selects).
SELECT_TYPES = (
    ComponentType.STRING_SELECT,
    ComponentType.USER_SELECT,
    ComponentType.ROLE_SELECT,
    ComponentType.MENTIONABLE_SELECT,
    ComponentType.CHANNEL_SELECT,
)


class ChannelType(IntEnum):
    """Channel types (the subset the backend models).

    Partial by design — add members here as the backend grows to support more
    channel kinds; ``discord.ChannelType`` is the full reference. Do not fall
    back to bare integer literals for an unlisted type.
    """

    TEXT = 0
    DM = 1
    VOICE = 2
    CATEGORY = 4
    NEWS = 5
    PUBLIC_THREAD = 11
    PRIVATE_THREAD = 12
    STAGE_VOICE = 13
    FORUM = 15


#: Channel kinds members can be voice-connected to.
VOICE_CHANNEL_TYPES = (ChannelType.VOICE, ChannelType.STAGE_VOICE)


class AuditLogAction(IntEnum):
    """Audit-log action types (the subset SimCord records).

    Only actions the backend actually performs are listed — SimCord never
    fabricates audit entries for things it cannot do. ``discord.AuditLogAction``
    is the full reference.
    """

    CHANNEL_CREATE = 10
    CHANNEL_UPDATE = 11
    CHANNEL_DELETE = 12
    MEMBER_KICK = 20
    MEMBER_BAN = 22
    MEMBER_UNBAN = 23
    MEMBER_UPDATE = 24
    MEMBER_ROLE_UPDATE = 25
    MEMBER_MOVE = 26
    MEMBER_DISCONNECT = 27
    ROLE_CREATE = 30
    ROLE_UPDATE = 31
    ROLE_DELETE = 32
    SCHEDULED_EVENT_CREATE = 100
    SCHEDULED_EVENT_UPDATE = 101
    SCHEDULED_EVENT_DELETE = 102


class MessageType(IntEnum):
    """Message types."""

    DEFAULT = 0
    REPLY = 19


class OverwriteType(IntEnum):
    """Permission-overwrite target types."""

    ROLE = 0
    MEMBER = 1


class CallbackType(IntEnum):
    """Interaction-response callback types."""

    PONG = 1
    CHANNEL_MESSAGE_WITH_SOURCE = 4
    DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5
    DEFERRED_UPDATE_MESSAGE = 6
    UPDATE_MESSAGE = 7
    APPLICATION_COMMAND_AUTOCOMPLETE_RESULT = 8
    MODAL = 9
