"""The Backend kernel: constructor, clock, event emitter, getters, permissions.

Split out from the historical monolithic ``Backend`` class. Every subsystem
mixin in this package inherits :class:`BackendBase`, so the genuinely
cross-cutting members defined here (the instance state, the virtual clock, the
gateway emitter, the universal getters and ``compute_permissions``) are
resolvable from all of them.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .. import errors, permissions
from ..cdn import CdnStore
from ..models import (
    Channel,
    Guild,
    GuildEmoji,
    Interaction,
    Invite,
    Message,
    Role,
    StageInstance,
    User,
    Webhook,
)

# Fixed virtual clock epoch (2026-01-01 UTC): reproducible snowflakes/timestamps.
_VIRTUAL_EPOCH_MS = 1767225600000
_DISCORD_EPOCH_MS = 1420070400000

EventListener = Callable[[str, Mapping[str, Any]], None]


class BackendBase:
    def __init__(self) -> None:
        self._counter = 0
        self._clock_offset_ms = 0
        self.users: dict[int, User] = {}
        self.guilds: dict[int, Guild] = {}
        self.channels: dict[int, Channel] = {}
        self.messages: dict[int, dict[int, Message]] = {}
        self.webhooks: dict[int, Webhook] = {}
        self.webhook_tokens: dict[str, int] = {}
        self.dm_channels: dict[int, int] = {}  # user id -> channel id
        self.invites: dict[str, Invite] = {}  # code -> invite
        self.application_emojis: dict[int, GuildEmoji] = {}  # app-owned emojis (no guild)
        self.stage_instances: dict[int, StageInstance] = {}  # keyed by stage channel id
        self.commands: dict[
            int | None, dict[tuple[str, int], dict[str, Any]]
        ] = {}  # scope -> (name, type) -> command
        self.interactions: dict[int, Interaction] = {}
        self.interaction_tokens: dict[str, int] = {}
        # Per-(guild, command) application-command permission overrides.
        self.command_permissions: dict[tuple[int, int], list[dict[str, Any]]] = {}
        self.cdn = CdnStore()
        self.subscribers: list[EventListener] = []
        self.http_log: list[tuple[str, str, dict[str, Any] | None]] = []
        #: Interleaved record of everything that crossed either seam, in order:
        #: ("HTTP", "METHOD /path", body) and ("GATEWAY", event, payload).
        self.transcript: list[tuple[str, str, Any]] = []
        self.faults: list[dict[str, Any]] = []
        self.application_id: int = self.snowflake()
        self.bot_user: User = self.make_user("TestBot", bot=True)

    # ------------------------------------------------------------------ core

    def snowflake(self) -> int:
        """Deterministic, monotonic snowflakes with valid embedded timestamps."""
        self._counter += 1
        ms = _VIRTUAL_EPOCH_MS - _DISCORD_EPOCH_MS + self._clock_offset_ms + self._counter
        return (ms << 22) | (self._counter % 4096)

    def now_iso(self) -> str:
        """The current virtual time as an ISO timestamp.

        Driven by the same virtual clock as :meth:`snowflake` (epoch + counter
        ms) rather than the wall clock, so a message's ``created_at`` (which
        discord.py derives from its snowflake) and its serialized ``timestamp``
        agree, and timestamps stay deterministic across runs.
        """
        ms = _VIRTUAL_EPOCH_MS + self._clock_offset_ms + self._counter
        return datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC).isoformat()

    def iso_after(self, seconds: float) -> str:
        """An ISO timestamp ``seconds`` into the virtual future (for expiries)."""
        ms = _VIRTUAL_EPOCH_MS + self._clock_offset_ms + self._counter + int(seconds * 1000)
        return datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC).isoformat()

    def advance_clock(self, seconds: float) -> None:
        """Advance the virtual wall clock (snowflake timestamps, now_iso).

        Cooldowns and other age math in discord.py are computed from message/
        interaction timestamps, so fast-forwarding time must move this clock as
        well as the event loop's (:meth:`Env.advance_time` does both).
        """
        self._clock_offset_ms += int(seconds * 1000)

    def emit(self, event: str, payload: Mapping[str, Any]) -> None:
        self.transcript.append(("GATEWAY", event, payload))
        for listener in self.subscribers:
            listener(event, payload)

    # ----------------------------------------------------------------- users

    def make_user(self, name: str, **fields: Any) -> User:
        """Create and register a user. ``fields`` are :class:`User` attributes
        (``bot``, ``system``, ``global_name``, ``discriminator``,
        ``public_flags``); their defaults live on the model, not here."""
        user = User(id=self.snowflake(), name=name, **fields)
        self.users[user.id] = user
        return user

    def store_attachments(
        self, channel_id: int, attachments: Sequence[tuple[str, bytes]]
    ) -> list[dict[str, Any]]:
        """Store ``(filename, bytes)`` uploads and return their attachment payloads.

        The shared path for the test-driver send surfaces (a member or a webhook
        posting files); the HTTP upload route stores its own, since those carry
        per-file descriptions from the multipart body.
        """
        return [
            self.cdn.store_attachment(self.snowflake(), channel_id, filename, data, None)
            for filename, data in attachments
        ]

    def get_user(self, user_id: int) -> User:
        try:
            return self.users[user_id]
        except KeyError:
            raise errors.unknown_user() from None

    # --------------------------------------------------------------- getters

    def get_guild(self, guild_id: int) -> Guild:
        try:
            return self.guilds[guild_id]
        except KeyError:
            raise errors.unknown_guild() from None

    def get_channel(self, channel_id: int) -> Channel:
        try:
            return self.channels[channel_id]
        except KeyError:
            raise errors.unknown_channel() from None

    def get_message(self, channel_id: int, message_id: int) -> Message:
        message = self.messages.get(channel_id, {}).get(message_id)
        if message is None:
            raise errors.unknown_message()
        return message

    def get_role(self, guild_id: int, role_id: int) -> Role:
        role = self.get_guild(guild_id).roles.get(role_id)
        if role is None:
            raise errors.unknown_role()
        return role

    # ------------------------------------------------------------ permissions

    def compute_permissions(self, guild_id: int, user_id: int, channel_id: int | None = None) -> int:
        guild = self.get_guild(guild_id)
        channel = None
        if channel_id is not None:
            channel = self.get_channel(channel_id)
            # Threads inherit their parent channel's overwrites; resolve to it.
            parent_id = channel.permission_channel_id()
            if parent_id != channel_id:
                channel = self.get_channel(parent_id)
        return permissions.compute(guild, user_id, channel)
