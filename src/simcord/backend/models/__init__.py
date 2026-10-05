"""Plain dataclass models for the virtual backend's state.

These are deliberately independent of discord.py's model classes: the backend
plays the role of Discord's servers, and only ever speaks to the bot through
wire-format payloads produced by :mod:`simcord.backend.serializers`.
"""

from .auditlog import AuditLogEntry
from .automod import AutoModRule
from .channel import Channel, Overwrite, ThreadMetadata
from .expression import GuildEmoji, Sticker
from .guild import Guild
from .interaction import Interaction, ResponseKind
from .invite import Invite
from .member import Member
from .message import (
    EPHEMERAL_FLAG,
    AllowedMentions,
    Message,
    MessageSticker,
    Poll,
    PollAnswer,
    Reaction,
    SystemMessageMetadata,
)
from .role import Role
from .scheduled_event import ScheduledEvent
from .stage_instance import StageInstance
from .user import User
from .voice import VoiceState
from .webhook import Webhook

__all__ = (
    "EPHEMERAL_FLAG",
    "AllowedMentions",
    "AuditLogEntry",
    "AutoModRule",
    "Channel",
    "Guild",
    "GuildEmoji",
    "Interaction",
    "Invite",
    "Member",
    "Message",
    "MessageSticker",
    "Overwrite",
    "Poll",
    "PollAnswer",
    "Reaction",
    "ResponseKind",
    "Role",
    "ScheduledEvent",
    "StageInstance",
    "Sticker",
    "SystemMessageMetadata",
    "ThreadMetadata",
    "User",
    "VoiceState",
    "Webhook",
)
