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
    PUBLIC_THREAD = 11
    PRIVATE_THREAD = 12


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
