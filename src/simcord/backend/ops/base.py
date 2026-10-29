"""The Backend kernel: constructor, clock, event emitter, getters, permissions.

Split out from the historical monolithic ``Backend`` class. Every subsystem
mixin in this package inherits :class:`BackendBase`, so the genuinely
cross-cutting members defined here (the instance state, the virtual clock, the
gateway emitter, the universal getters and ``compute_permissions``) are
resolvable from all of them.
"""

from __future__ import annotations

import datetime
import heapq
from collections.abc import Callable, Iterator, Mapping, Sequence
from itertools import islice
from typing import Any

from sortedcontainers import SortedList

from ..._clock import Clock
from ...types import HttpLogEntry
from .. import errors, permissions, serializers
from .._deadlines import Deadlines, parse_deadline
from ..access import channel_accessible
from ..cdn import CdnStore
from ..models import (
    SKU,
    Channel,
    Entitlement,
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

# Snowflakes embed milliseconds since Discord's epoch; allocation never moves UTC.
_DISCORD_EPOCH_MS = 1420070400000

EventListener = Callable[[str, Mapping[str, Any]], None]


class BackendBase:
    def __init__(self, *, clock: Clock | None = None) -> None:
        self.clock = clock if clock is not None else Clock()
        self._reserved_ids: set[int] = set()
        self._sequences: dict[int, int] = {}
        self.skus: dict[int, SKU] = {}
        self.entitlements: dict[int, Entitlement] = {}
        self.users: dict[int, User] = {}
        self.guilds: dict[int, Guild] = {}
        self.channels: dict[int, Channel] = {}
        self.messages: dict[int, dict[int, Message]] = {}
        # Canonical mutation methods own these indexes; raw model/dict writes
        # to membership and scheduling fields are unsupported.
        self._message_ids: dict[tuple[int, int | None, bool], SortedList] = {}
        self._entitlement_users: dict[int, dict[int, Entitlement]] = {}
        self._entitlement_guilds: dict[int, dict[int, Entitlement]] = {}
        self._entitlement_ids: SortedList = SortedList()
        self._poll_deadlines = Deadlines()
        self._event_deadlines = Deadlines()
        self._entitlement_deadlines = Deadlines()
        self._channel_order: dict[int, int] = {}
        self._guild_order: dict[int, int] = {}
        self._message_order: dict[int, int] = {}
        self._event_order: dict[int, int] = {}
        self._entitlement_order: dict[int, int] = {}
        self._order_serial = 0
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
        self.http_requests: list[HttpLogEntry] = []
        #: Interleaved record of everything that crossed either seam, in order:
        #: ("HTTP", "METHOD /path", body) and ("GATEWAY", event, payload).
        self.transcript: list[tuple[str, str, Any]] = []
        self.faults: list[dict[str, Any]] = []
        self.application_id: int = self.snowflake()
        self.bot_user: User = self.make_user("TestBot", bot=True)

    # ------------------------------------------------------------------ core

    def reserve_id(self, value: int) -> int:
        """Reserve an explicit identifier before any fixture mutation."""
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 1 << 64:
            raise errors.SetupError("id must be a positive 64-bit snowflake")
        if value in self._reserved_ids:
            raise errors.SetupError(f"id {value} is already reserved")
        self._reserved_ids.add(value)
        return value

    def _allocate_at(self, milliseconds: int) -> int:
        sequence = self._sequences.get(milliseconds, 0)
        while sequence < 1 << 22:
            value = (milliseconds << 22) | sequence
            sequence += 1
            if value not in self._reserved_ids:
                self._sequences[milliseconds] = sequence
                self._reserved_ids.add(value)
                return value
        raise errors.SetupError("snowflake sequence exhausted at the current millisecond")

    def snowflake(self) -> int:
        """Allocate at current UTC, using all 22 low bits without advancing time."""
        milliseconds = int(self.clock.now().timestamp() * 1000) - _DISCORD_EPOCH_MS
        return self._allocate_at(milliseconds)

    def snowflake_for_shard(self, shard_id: int, shard_count: int) -> int:
        """Allocate at the latest historical millisecond belonging to this shard."""
        milliseconds = int(self.clock.now().timestamp() * 1000) - _DISCORD_EPOCH_MS
        milliseconds -= (milliseconds - shard_id) % shard_count
        return self._allocate_at(milliseconds)

    def now_iso(self) -> str:
        return self.clock.now().isoformat()

    def iso_after(self, seconds: float) -> str:
        return (self.clock.now() + datetime.timedelta(seconds=seconds)).isoformat()

    def advance_clock(self, seconds: float) -> None:
        self.clock.advance(seconds)

    def emit(self, event: str, payload: Mapping[str, Any]) -> None:
        self.transcript.append(("GATEWAY", event, payload))
        for listener in self.subscribers:
            listener(event, payload)

    # ----------------------------------------------------------------- users

    def make_user(self, name: str, **fields: Any) -> User:
        """Create and register a user. ``fields`` are :class:`User` attributes
        (``bot``, ``system``, ``global_name``, ``discriminator``,
        ``public_flags``, ``avatar``); their defaults live on the model, not here."""
        user = User(id=self.snowflake(), name=name, **fields)
        self.users[user.id] = user
        return user

    def edit_bot_profile(self, username: str) -> User:
        """Update the bot account and publish its new identity."""
        self.bot_user.name = username
        self.emit("USER_UPDATE", dict(serializers.user_payload(self.bot_user)))
        return self.bot_user

    def store_attachments(
        self, channel_id: int, attachments: Sequence[tuple[str, bytes]]
    ) -> list[dict[str, Any]]:
        """Store one fully staged batch; message sends defer installation until validation."""
        uploads = self.stage_attachments(channel_id, attachments)
        self.cdn.install_attachments(uploads)
        return [attachment for attachment, _ in uploads]

    def stage_attachments(
        self, channel_id: int, attachments: Sequence[tuple[str, bytes]]
    ) -> list[tuple[dict[str, Any], bytes]]:
        return [
            (self.cdn.prepare_attachment(self.snowflake(), channel_id, filename, data, None), data)
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

    def _schedule_poll(self, message: Message) -> None:
        poll = message.poll
        self._poll_deadlines.set(
            (message.channel_id, message.id),
            parse_deadline(poll.expiry) if poll is not None and not poll.finalized else None,
            (self._channel_order[message.channel_id], self._message_order[message.id]),
        )

    def _registration_order(self) -> int:
        self._order_serial += 1
        return self._order_serial

    @staticmethod
    def _message_owner(message: Message) -> int | None:
        if not message.is_ephemeral:
            return None
        value = ((message.interaction_metadata or {}).get("user") or {}).get("id")
        if value is None:
            return -1
        try:
            return int(value) if str(int(value)) == str(value) else -1
        except (TypeError, ValueError):
            return -1

    def _index_message(self, message: Message, *, remove: bool = False) -> None:
        owner = self._message_owner(message)
        for pinned in (False, True) if message.pinned else (False,):
            key = (message.channel_id, owner, pinned)
            if remove:
                values = self._message_ids.get(key)
                if values is not None:
                    values.discard(message.id)
                    if not values:
                        del self._message_ids[key]
            else:
                self._message_ids.setdefault(key, SortedList()).add(message.id)

    def _message_buckets(self, channel_id: int, viewer_id: int | None, pinned: bool) -> list[SortedList]:
        owners = (None,) if viewer_id is None else (None, viewer_id)
        return [
            bucket
            for owner in owners
            if (bucket := self._message_ids.get((channel_id, owner, pinned))) is not None
        ]

    def iter_messages(
        self,
        channel_id: int,
        *,
        viewer_id: int | None = None,
        before: int | None = None,
        after: int | None = None,
        limit: int | None = None,
        newest_first: bool = False,
        pinned: bool = False,
    ) -> Iterator[Message]:
        """Select live numeric order; callers retain live channel authorization."""
        streams = [
            bucket.irange(after, before, inclusive=(False, False), reverse=newest_first)
            for bucket in self._message_buckets(channel_id, viewer_id, pinned)
        ]
        ids = heapq.merge(*streams, reverse=newest_first)
        selected = ids if limit is None else islice(ids, max(0, limit))
        messages = self.messages.get(channel_id, {})
        return (messages[message_id] for message_id in selected)

    def select_messages(
        self,
        channel_id: int,
        *,
        viewer_id: int | None = None,
        before: int | None = None,
        after: int | None = None,
        limit: int | None = None,
        newest_first: bool = False,
        pinned: bool = False,
    ) -> list[Message]:
        return list(
            self.iter_messages(
                channel_id,
                viewer_id=viewer_id,
                before=before,
                after=after,
                limit=limit,
                newest_first=newest_first,
                pinned=pinned,
            )
        )

    def message_count(self, channel_id: int, *, viewer_id: int | None = None, pinned: bool = False) -> int:
        return sum(len(bucket) for bucket in self._message_buckets(channel_id, viewer_id, pinned))

    def message_position(
        self, channel_id: int, message_id: int, *, viewer_id: int | None = None, pinned: bool = False
    ) -> int:
        return sum(
            bucket.bisect_left(message_id) for bucket in self._message_buckets(channel_id, viewer_id, pinned)
        )

    def _drop_channel_messages(self, channel_id: int) -> None:
        for message in self.messages.pop(channel_id, {}).values():
            self._index_message(message, remove=True)
            self._poll_deadlines.set((channel_id, message.id), None, ())
            self._message_order.pop(message.id, None)
        self._channel_order.pop(channel_id, None)

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
            if channel.guild_id != guild_id:
                raise errors.missing_access()
            # Threads inherit their parent channel's overwrites; resolve to it.
            parent_id = channel.permission_channel_id()
            if parent_id != channel_id:
                channel = self.get_channel(parent_id)
        return permissions.compute(guild, user_id, channel, now=self.clock.now())

    def require_permissions(
        self, guild_id: int | None, user_id: int, channel_id: int | None, *names: str
    ) -> None:
        if channel_id is not None and self.get_channel(channel_id).guild_id != guild_id:
            raise errors.missing_access()
        perms = self.compute_permissions(guild_id, user_id, channel_id) if guild_id is not None else 0
        if channel_id is not None and not channel_accessible(
            self, channel_id, user_id, permissions_value=perms
        ):
            raise errors.missing_access()
        if guild_id is None:  # DMs: no guild permissions apply
            return
        if user_id not in self.get_guild(guild_id).members:
            raise errors.missing_access()
        for name in names:
            if not perms & permissions.flag(name):
                raise errors.missing_permissions()
