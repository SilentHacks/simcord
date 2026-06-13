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
from .message import EPHEMERAL_FLAG, Message, Poll, PollAnswer, Reaction
from .role import Role
from .scheduled_event import ScheduledEvent
from .user import User
from .voice import VoiceState
from .webhook import Webhook

__all__ = (
    "EPHEMERAL_FLAG",
    "AuditLogEntry",
    "AutoModRule",
    "Channel",
    "Guild",
    "GuildEmoji",
    "Interaction",
    "Invite",
    "Member",
    "Message",
    "Overwrite",
    "Poll",
    "PollAnswer",
    "Reaction",
    "ResponseKind",
    "Role",
    "ScheduledEvent",
    "Sticker",
    "ThreadMetadata",
    "User",
    "VoiceState",
    "Webhook",
)
