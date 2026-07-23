"""Feeds raw gateway payloads into discord.py's real event parsers.

This mirrors what ``DiscordWebSocket.received_message`` does after the
transport layer (decompression, sequence tracking): look up the parser for the
event type on the connection state and call it. Everything downstream —
parsing, caching, dispatching to user handlers — is real discord.py code.

Like the real gateway, delivery is gated on the bot's declared intents
(:mod:`simcord.intents`): events the bot did not subscribe to are dropped
(and recorded in the transcript), message content is censored without the
``message_content`` intent, and GUILD_CREATE carries only the members the
real gateway would inline — the rest arrives via member chunking, served by
:class:`FakeWebSocket`.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import discord

from . import _dpy_internals
from . import intents as _intents
from .backend import serializers

if TYPE_CHECKING:
    from .backend import Backend

_log = logging.getLogger(__name__)

#: Chunk size of GUILD_MEMBERS_CHUNK on the real gateway.
_CHUNK_SIZE = 1000


class FakeGateway:
    def __init__(self, backend: Backend, connection_state: Any) -> None:
        self._backend = backend
        self._state = connection_state
        self._intents_cache: discord.Intents | None = None

    @property
    def intents(self) -> discord.Intents:
        # Intents are fixed at client construction; cache the copy the
        # ConnectionState property hands out.
        cached = self._intents_cache
        if cached is None:
            cached = self._intents_cache = self._state.intents
        return cached

    def feed(self, event: str, payload: Mapping[str, Any]) -> None:
        gate = _intents.required_intent(event, payload)
        if gate is not None and not getattr(self.intents, gate):
            # Real Discord never sends events outside the declared intents.
            # Record the drop so a confused test can explain itself.
            self._backend.transcript.append(("DROPPED", event, f"requires the {gate} intent"))
            _log.debug("Dropping %s: bot lacks the %s intent", event, gate)
            return
        try:
            parser = self._state.parsers[event]
        except KeyError:
            _log.debug("Ignoring unknown gateway event %s", event)
            return
        # Deep copy: discord.py may mutate the payload, the original is
        # backend state shared with other subscribers — and the intent
        # shaping below must not leak back into the backend either.
        mutable: dict[str, Any] = copy.deepcopy(dict(payload))
        bot_id = self._backend.bot_user.id
        if event in ("MESSAGE_CREATE", "MESSAGE_UPDATE") and not self.intents.message_content:
            if _intents.censor_message(mutable, bot_id):
                self._backend.transcript.append(
                    ("CENSORED", event, "content hidden: requires the message_content intent")
                )
        if event == "GUILD_CREATE":
            _intents.prune_guild_create(mutable, self.intents, bot_id)
        parser(mutable)

    def deliver(self, event: str, payload: Mapping[str, Any]) -> None:
        """Deliver a connection-targeted event (e.g. a chunk response) ungated.

        Unlike :meth:`Backend.emit`, this reaches only this client — matching
        the real gateway, where REQUEST_GUILD_MEMBERS responses go to the
        requesting shard alone — but is still recorded in the transcript.
        """
        self._backend.transcript.append(("GATEWAY", event, payload))
        parser = self._state.parsers.get(event)
        if parser is not None:
            parser(copy.deepcopy(dict(payload)))


class FakeWebSocket:
    """The upstream half of the gateway: commands the bot sends to Discord.

    Installed as ``client.ws`` so discord.py's chunking machinery
    (``Guild.chunk()``, ``Guild.query_members()``, startup chunking with the
    ``members`` intent) works against the backend instead of hanging.
    """

    def __init__(self, backend: Backend, gateway: FakeGateway, *, shard_id: int = 0) -> None:
        self._backend = backend
        self._gateway = gateway
        self.shard_id = shard_id
        self.open = True
        self._chunk_task: asyncio.Task[None] | None = None

    @property
    def latency(self) -> float:
        return 0.0

    def is_ratelimited(self) -> bool:
        return False

    async def close(self, code: int = 1000) -> None:
        self.open = False

    async def change_presence(self, *, activity: Any = None, status: Any = None, since: float = 0.0) -> None:
        # Presence is cosmetic for an offline bot under test; accept and drop.
        return

    async def request_chunks(
        self,
        guild_id: int,
        query: str | None = None,
        *,
        limit: int,
        user_ids: list[int] | None = None,
        presences: bool = False,
        nonce: str | None = None,
    ) -> None:
        """Answer REQUEST_GUILD_MEMBERS with GUILD_MEMBERS_CHUNK events.

        Implements the documented matching rules: ``user_ids`` lookup (with
        ``not_found`` for unknown ids), case-insensitive username/nick prefix
        ``query``, ``limit`` (0 = no limit), 1000 members per chunk, and
        ``presences`` when requested. The nonce is echoed so discord.py can
        resolve the matching ChunkRequest future.
        """
        guild = self._backend.guilds.get(guild_id)
        if guild is None:
            # The guild was removed before its chunk could be served (created and
            # then immediately left/deleted, racing discord.py's startup chunk).
            # Real Discord just never answers; deliver nothing, but still wake the
            # parked request below so the bot's chunk wrapper doesn't hang.
            self._chunk_task = asyncio.ensure_future(self._deliver_chunks([], guild_id, nonce))
            return
        members = list(guild.members.values())
        not_found: list[str] = []
        if user_ids:
            wanted = {int(uid) for uid in user_ids}
            members = [m for m in members if m.user_id in wanted]
            not_found = [str(uid) for uid in sorted(wanted - {m.user_id for m in members})]
        elif query:
            prefix = query.lower()
            members = [
                m
                for m in members
                if self._backend.users[m.user_id].name.lower().startswith(prefix)
                or (m.nick or "").lower().startswith(prefix)
            ]
        if limit:
            members = members[:limit]
        chunks = [members[i : i + _CHUNK_SIZE] for i in range(0, len(members), _CHUNK_SIZE)] or [[]]
        payloads: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks):
            payload: dict[str, Any] = {
                "guild_id": str(guild_id),
                "members": [serializers.member_payload(self._backend, guild, m) for m in chunk],
                "chunk_index": index,
                "chunk_count": len(chunks),
            }
            if presences:
                # No presence model in the backend: everyone is offline, which
                # is also what discord.py assumes when no presence is known.
                payload["presences"] = [
                    {
                        "user": {"id": str(m.user_id)},
                        "guild_id": str(guild_id),
                        "status": "offline",
                        "activities": [],
                        "client_status": {},
                    }
                    for m in chunk
                ]
            if index == 0 and not_found:
                payload["not_found"] = not_found
            if nonce:
                payload["nonce"] = nonce
            payloads.append(payload)
        # Deliver asynchronously, like a real gateway round-trip. discord.py
        # registers its ChunkRequest waiter only *after* request_chunks returns
        # — and under asyncio.wait_for (used by query_members/chunk_guild) that
        # registration is itself deferred to a freshly scheduled task. A plain
        # call_soon can therefore fire *before* the waiter exists, resolving
        # into thin air and hanging the caller (observed on Python 3.11, whose
        # wait_for schedules the inner task one tick later than 3.12+). Yielding
        # one loop iteration before delivering guarantees the waiter is in place
        # regardless of interpreter version.
        self._chunk_task = asyncio.ensure_future(self._deliver_chunks(payloads, guild_id, nonce))

    async def _deliver_chunks(self, payloads: list[dict[str, Any]], guild_id: int, nonce: str | None) -> None:
        # Let the caller's ChunkRequest waiter register before we answer.
        await asyncio.sleep(0)
        # The payloads were snapshotted when the request was answered; the
        # deferred delivery may run a tick later than a member's departure
        # (whether before or after is interpreter-dependent — see request_chunks).
        # Reconcile against live membership so a since-departed member isn't
        # resurrected in the bot's cache by a stale chunk, the way a startup
        # chunk would re-add a member the test removed before settle ran.
        guild = self._backend.guilds.get(guild_id)
        if guild is not None:
            self._drop_departed_members(payloads, guild.members.keys())
        for payload in payloads:
            self._gateway.deliver("GUILD_MEMBERS_CHUNK", payload)
        # If the guild was removed mid-flight, discord.py dropped the chunk we
        # just delivered (its cache no longer has the guild) and left the request
        # parked; resolve it directly. A no-op on the normal path, where
        # discord.py has already resolved and removed the request itself.
        _dpy_internals.resolve_pending_chunk(self._gateway._state, guild_id, nonce)

    @staticmethod
    def _drop_departed_members(payloads: list[dict[str, Any]], present: Any) -> None:
        """Strip members no longer in ``present`` (live user ids) from each chunk.

        Filtering never adds members, so a query/user_ids/limit snapshot stays
        a subset of itself; it only removes anyone who left between snapshot and
        delivery. Presences are kept in lockstep with the members they describe.
        """
        for payload in payloads:
            members = payload["members"]
            kept = [m for m in members if int(m["user"]["id"]) in present]
            if len(kept) == len(members):
                continue
            payload["members"] = kept
            if "presences" in payload:
                payload["presences"] = [p for p in payload["presences"] if int(p["user"]["id"]) in present]


class FakeShard:
    """The small interface consumed by discord.py's public ``ShardInfo``."""

    def __init__(self, websocket: FakeWebSocket, dispatch: Any) -> None:
        self.ws = websocket
        self._dispatch = dispatch

    @property
    def id(self) -> int:
        return self.ws.shard_id

    async def close(self) -> None:
        await self.ws.close()

    async def disconnect(self) -> None:
        if not self.ws.open:
            return
        await self.close()
        self._dispatch("shard_disconnect", self.id)

    async def reconnect(self) -> None:
        if self.ws.open:
            return
        self.ws.open = True
        self._dispatch("shard_connect", self.id)
        self._dispatch("shard_ready", self.id)


class ShardRouter:
    """Own fake shard connections and route each gateway event exactly once."""

    def __init__(
        self, backend: Backend, state: Any, dispatch: Any, shard_count: int, shard_ids: tuple[int, ...]
    ):
        self.shard_count = shard_count
        self.shard_ids = shard_ids
        self.gateways = {shard_id: FakeGateway(backend, state) for shard_id in shard_ids}
        self.websockets = {
            shard_id: FakeWebSocket(backend, self.gateways[shard_id], shard_id=shard_id)
            for shard_id in shard_ids
        }
        self.shards = {shard_id: FakeShard(self.websockets[shard_id], dispatch) for shard_id in shard_ids}

    def feed(self, event: str, payload: Mapping[str, Any]) -> None:
        shard_id = self._event_shard_id(event, payload)
        gateway = self.gateways.get(shard_id)
        if gateway is not None and gateway._state is not None:
            gateway.feed(event, payload)

    def _event_shard_id(self, event: str, payload: Mapping[str, Any]) -> int:
        guild_id = payload.get("guild_id")
        if guild_id is None and event in {"GUILD_CREATE", "GUILD_UPDATE", "GUILD_DELETE"}:
            guild_id = payload.get("id")
        if guild_id is None:
            return 0
        return (int(guild_id) >> 22) % self.shard_count
