"""The Backend: single in-memory source of truth for the virtual Discord.

REST handlers and gateway events are two projections of this one store: every
mutation that real Discord would announce over the gateway is broadcast to all
attached clients, so the bot's cache stays consistent with REST responses —
including for the bot's own actions.

The implementation lives in the ``ops`` subpackage: a shared
:class:`~simcord.backend.ops.base.BackendBase` kernel (constructor, clock, event
emitter, getters, ``compute_permissions``) plus one mixin per subsystem. This
module is the assembler — it composes those mixins into the concrete ``Backend``
and re-exports the names other modules import from here.
"""

from __future__ import annotations

from .ops.audit import AuditLogMixin
from .ops.base import BackendBase, EventListener
from .ops.channels import ChannelMixin
from .ops.commands import CommandsMixin
from .ops.expressions import ExpressionsMixin
from .ops.guilds import DEFAULT_EVERYONE_PERMISSIONS, GuildMixin
from .ops.invites import InviteMixin
from .ops.messages import MessageMixin
from .ops.monetization import MonetizationMixin
from .ops.permissions import PermissionsMixin
from .ops.polls import PollMixin
from .ops.reactions import ReactionMixin
from .ops.scheduled_events import ScheduledEventMixin
from .ops.voice import VoiceStateMixin
from .ops.webhooks import WebhookMixin

__all__ = ["DEFAULT_EVERYONE_PERMISSIONS", "Backend", "BackendBase", "EventListener"]


class Backend(
    PermissionsMixin,
    GuildMixin,
    ChannelMixin,
    MessageMixin,
    ReactionMixin,
    PollMixin,
    WebhookMixin,
    AuditLogMixin,
    ScheduledEventMixin,
    VoiceStateMixin,
    InviteMixin,
    ExpressionsMixin,
    CommandsMixin,
    MonetizationMixin,
):
    """Assembles the subsystem mixins; all behaviour lives in them + BackendBase."""
