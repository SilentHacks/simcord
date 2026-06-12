"""Plain dataclass models for the virtual backend's state.

These are deliberately independent of discord.py's model classes: the backend
plays the role of Discord's servers, and only ever speaks to the bot through
wire-format payloads produced by :mod:`discord_py_test.backend.serializers`.
"""

from .channel import Channel, Overwrite, ThreadMetadata
from .guild import Guild
from .interaction import Interaction, ResponseKind
from .member import Member
from .message import EPHEMERAL_FLAG, Message, Reaction
from .role import Role
from .user import User
from .webhook import Webhook

__all__ = (
    "EPHEMERAL_FLAG",
    "Channel",
    "Guild",
    "Interaction",
    "Member",
    "Message",
    "Overwrite",
    "Reaction",
    "ResponseKind",
    "Role",
    "ThreadMetadata",
    "User",
    "Webhook",
)
