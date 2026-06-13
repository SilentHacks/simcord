"""Test environment lifecycle: attach a real bot to the virtual backend."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import discord

from . import _dpy_internals
from . import intents as _intents
from .backend import Backend, serializers
from .backend.errors import SetupError
from .builders import GuildHandle, UserHandle
from .gateway import FakeGateway, FakeWebSocket
from .http import FakeHTTPClient, FakeWebhookAdapter


class Env:
    """A running test environment around a single bot.

    Use via :func:`simcord.run`::

        async with simcord.run(bot) as env:
            guild = env.create_guild()
            ...

    Only one ``Env`` may be live per event loop at a time: ``start()``
    monkeypatches ``loop.create_task`` to track the bot's tasks, and nesting two
    environments on one loop would corrupt each other's task bookkeeping.
    """

    def __init__(
        self,
        bot: discord.Client,
        *,
        strict_sync: bool = True,
        check_errors: bool = True,
        approved_intents: discord.Intents | None = None,
    ) -> None:
        self.bot = bot
        self.strict_sync = strict_sync
        self.check_errors = check_errors
        #: Simulates the developer-portal privileged-intent toggles. ``None``
        #: (the default) means everything is approved; pass an Intents with
        #: e.g. ``members=False`` to make start() fail with
        #: :class:`discord.PrivilegedIntentsRequired`, as a real connect would.
        self.approved_intents = approved_intents
        self.backend = Backend()
        self._errors: list[BaseException] = []
        self._errors_inspected = False
        self._guilds: list[GuildHandle] = []
        self._tasks: list[asyncio.Task[Any]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._orig_create_task: Any = None
        self._orig_monotonic: Any = None
        self._time_offset = 0.0
        self._adapter_token: Any = None
        self._gateway_feed: Any = None
        self._started = False

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        if self._started:
            raise SetupError("Env already started")
        self._started = True
        self._loop = asyncio.get_running_loop()
        await self._attach_bot(self.bot)

    async def restart_bot(self, bot: discord.Client | None = None) -> None:
        """Simulate a bot restart while the virtual world persists.

        Detaches the current bot, attaches ``bot`` (or the same instance if
        omitted), and replays ``GUILD_CREATE`` for every existing guild so the
        new client's cache repopulates exactly as on a first run — letting tests
        prove that persistent views (``bot.add_view`` in ``setup_hook``)
        re-attach to messages they never saw created.

        Pass a freshly built client for a faithful restart: re-running the same
        instance re-executes ``setup_hook`` (which typically reloads extensions
        and would fail). The virtual clock is preserved — the world does not
        rewind. Errors the old bot raised are preserved too: a restart does not
        launder away un-inspected bot bugs.
        """
        if not self._started:
            raise SetupError("Env not started; use restart_bot() only inside simcord.run()")
        await self.settle()
        await self._detach_bot()
        await self._attach_bot(bot or self.bot)
        for handle in self._guilds:
            guild = self.backend.get_guild(handle.id)
            self.backend.emit("GUILD_CREATE", serializers.guild_create_payload(self.backend, guild))
        await self.settle()

    async def _attach_bot(self, bot: discord.Client) -> None:
        """Install the fakes onto ``bot`` and bring it to READY. Shared by
        :meth:`start` and :meth:`restart_bot`."""
        self.bot = bot
        loop = self._loop
        assert loop is not None

        # Track every task spawned while the env is live so settle() can wait
        # for the bot to finish reacting without guessing with sleeps.
        self._orig_create_task = loop.create_task

        def tracking_create_task(coro: Any, **kwargs: Any) -> asyncio.Task[Any]:
            task = self._orig_create_task(coro, **kwargs)
            self._tasks.append(task)
            return task

        loop.create_task = tracking_create_task  # type: ignore[method-assign]

        # Virtual clock: time.monotonic() = real monotonic + offset, so
        # advance_time() can fast-forward view timeouts, cooldown buckets and
        # asyncio timers without real waiting. One patch covers everything:
        # BaseEventLoop.time() and discord.py's deadline checks both read
        # time.monotonic at call time. Restored on shutdown. The offset survives
        # restarts so the world's clock does not rewind.
        self._orig_monotonic = time.monotonic
        time.monotonic = lambda: self._orig_monotonic() + self._time_offset

        _dpy_internals.install_http(bot, FakeHTTPClient(self.backend, loop))
        _dpy_internals.set_guild_ready_timeout(bot, 0.0)
        self._adapter_token = _dpy_internals.set_webhook_adapter(FakeWebhookAdapter(self.backend))

        gateway = FakeGateway(self.backend, _dpy_internals.get_state(bot))
        self._gateway_feed = gateway.feed
        self.backend.subscribers.append(gateway.feed)
        # The upstream half of the gateway: answers REQUEST_GUILD_MEMBERS so
        # member chunking (startup, Guild.chunk, query_members) works.
        _dpy_internals.install_websocket(bot, FakeWebSocket(self.backend, gateway))

        try:
            self._capture_errors()
            # Simulated developer-portal check: a real IDENTIFY with an
            # unapproved privileged intent is rejected with close code 4014,
            # which discord.py surfaces as PrivilegedIntentsRequired.
            if self.approved_intents is not None and _intents.missing_privileged_intents(
                bot.intents, self.approved_intents
            ):
                raise discord.PrivilegedIntentsRequired(shard_id=None)
            # Runs the real login flow: identity, application info, setup_hook
            # (where bots typically load extensions and sync their command tree).
            await bot.login("simcord.fake.token")
            gateway.feed(
                "READY",
                {
                    "v": 10,
                    "user": dict(serializers.user_payload(self.backend.bot_user)),
                    "guilds": [],
                    "session_id": "simcord-session",
                    "resume_gateway_url": "wss://simcord.invalid",
                    "shard": [0, 1],
                    "application": {"id": str(self.backend.application_id), "flags": 0},
                },
            )
            await self.settle()
        except BaseException:
            # Setup (e.g. setup_hook) blew up: undo the global monkeypatches so
            # we don't leak the patched loop.create_task / webhook adapter into
            # whatever runs next on this loop.
            await self._detach_bot()
            raise

    async def shutdown(self) -> None:
        await self._detach_bot()

    async def _detach_bot(self) -> None:
        """Undo the current bot's patches and stop tracking it. Leaves the
        backend (the virtual world) intact so a restart can re-attach to it."""
        if self._gateway_feed is not None:
            try:
                self.backend.subscribers.remove(self._gateway_feed)
            except ValueError:
                pass
            self._gateway_feed = None
        if self._adapter_token is not None:
            _dpy_internals.reset_webhook_adapter(self._adapter_token)
            self._adapter_token = None
        if self._loop is not None and self._orig_create_task is not None:
            self._loop.create_task = self._orig_create_task  # type: ignore[method-assign]
            self._orig_create_task = None
        if self._orig_monotonic is not None:
            time.monotonic = self._orig_monotonic
            self._orig_monotonic = None
        current = asyncio.current_task()
        to_cancel = [t for t in self._tasks if t is not current and not t.done()]
        for task in to_cancel:
            task.cancel()
        await asyncio.gather(*to_cancel, return_exceptions=True)
        self._tasks = []

    async def settle(self, timeout: float = 5.0, idle: float = 0.05) -> None:  # noqa: ASYNC109
        # `timeout` is deliberate public API: settle() polls for quiescence and
        # decides between "parked on a future" and "still working", so it can't
        # be replaced by wrapping the body in asyncio.timeout().
        """Wait until the bot has finished reacting to injected events.

        Waits for all tracked tasks to complete. A task that completes no work
        within an ``idle`` window is only abandoned if it is genuinely parked on
        a future (e.g. blocked in ``wait_for`` for a later user action) — if the
        loop still has timers scheduled to fire before ``timeout`` (e.g. an
        ``asyncio.sleep`` in a cooldown or backoff), we keep waiting for them.
        If pending tasks neither finish nor park before ``timeout``, a
        ``TimeoutError`` with the pending tasks is raised.
        """
        assert self._loop is not None
        deadline = self._loop.time() + timeout
        # Give freshly-scheduled callbacks a chance to run first.
        for _ in range(3):
            await asyncio.sleep(0)
        while True:
            self._tasks = [t for t in self._tasks if not t.done()]
            pending = [
                t
                for t in self._tasks
                if getattr(t.get_coro(), "__qualname__", "").split(".")[-1]
                not in _dpy_internals.BACKGROUND_CORO_NAMES
            ]
            if not pending:
                return
            done, _ = await asyncio.wait(pending, timeout=idle, return_when=asyncio.FIRST_COMPLETED)
            if done:
                continue  # progress made — re-evaluate what is still pending
            if self._loop.time() > deadline:
                raise TimeoutError(f"bot did not settle; pending tasks: {pending}")
            next_timer = self._next_scheduled_timer()
            if next_timer is None or next_timer > deadline:
                # No imminent timer: the remaining tasks are parked on futures
                # waiting for input we will never deliver. Leave them running.
                return
            # A timer (e.g. asyncio.sleep) is due before the deadline; loop and
            # wait for the work it will wake up.

    def _next_scheduled_timer(self) -> float | None:
        """The earliest live ``call_later`` deadline on the loop, if any.

        Used by :meth:`settle` to tell ``asyncio.sleep``-style pauses (which
        schedule a timer) apart from tasks parked indefinitely on a future
        (which do not). Best-effort: relies on the standard loop's internals
        and degrades to ``None`` if they are unavailable.
        """
        scheduled = getattr(self._loop, "_scheduled", None)
        if not scheduled:
            return None
        times = [h.when() for h in scheduled if not h.cancelled()]
        return min(times) if times else None

    async def advance_time(self, seconds: float) -> None:
        """Fast-forward the virtual clock, firing every timer that becomes due.

        View timeouts, cooldown resets, ``asyncio.sleep`` chains — anything the
        bot scheduled against the loop's clock — fire as if ``seconds`` of real
        time had passed, without waiting. Timers are consumed in order (a chain
        of three 60s sleeps completes within ``advance_time(180)``), and the
        bot's reactions are settled after each step.
        """
        assert self._loop is not None
        await self.settle()
        # Cooldowns and age math derive from message/interaction timestamps, so
        # the backend's virtual wall clock must advance in step with the loop's.
        self.backend.advance_clock(seconds)
        # Polls finalize on a wall-clock deadline rather than a loop timer, so
        # fast-forwarding time must finalize any that just expired.
        self.backend.expire_due_polls()
        remaining = float(seconds)
        while remaining > 0:
            next_timer = self._next_scheduled_timer()
            now = self._loop.time()
            if next_timer is None or next_timer - now > remaining:
                self._time_offset += remaining
                break
            step = max(next_timer - now, 0.0)
            self._time_offset += step
            remaining -= step
            # The earliest timer is now due: let it fire and the bot react.
            await asyncio.sleep(0)
            await self.settle()

    # -------------------------------------------------------- error capture

    @property
    def errors(self) -> list[BaseException]:
        """Errors the bot raised (command handlers, app commands, listeners).

        Reading this marks the errors as inspected: ``simcord.run`` then trusts the
        test's own assertions instead of failing it at teardown.
        """
        self._errors_inspected = True
        return self._errors

    def _capture_errors(self) -> None:
        from discord.ext import commands

        async def on_command_error(_ctx: Any, error: BaseException) -> None:
            # CommandNotFound just means the message wasn't a command — that is
            # not a bot bug, so don't pollute env.errors with it.
            if not isinstance(error, commands.CommandNotFound):
                self._errors.append(error)

        add_listener = getattr(self.bot, "add_listener", None)
        if add_listener is not None:
            add_listener(on_command_error, "on_command_error")

        # Exceptions raised inside plain event listeners (e.g. on_member_join)
        # go to Client.on_error, which by default only logs them. Capture them
        # too so listener bugs surface in env.errors instead of vanishing.
        original_on_error = self.bot.on_error

        async def on_error(event_method: str, /, *args: Any, **kwargs: Any) -> None:
            import sys

            exc = sys.exc_info()[1]
            if exc is not None:
                self._errors.append(exc)
            await original_on_error(event_method, *args, **kwargs)

        self.bot.on_error = on_error  # type: ignore[method-assign]

        tree = getattr(self.bot, "tree", None)
        if tree is not None:
            original = tree.on_error

            async def on_tree_error(interaction: Any, error: BaseException) -> None:
                self._errors.append(error)
                await original(interaction, error)

            tree.on_error = on_tree_error

    # -------------------------------------------------------------- builders

    def create_user(self, name: str) -> UserHandle:
        return UserHandle(self, self.backend.make_user(name))

    def create_guild(self, name: str = "Test Guild", *, id: int | None = None) -> GuildHandle:
        """Create a guild. Pass ``id`` to pin a known id — e.g. to match a bot that
        syncs its commands to a hardcoded guild id, so ``strict_sync`` can stay on."""
        handle = GuildHandle(self, self.backend.create_guild(name, id=id))
        self._guilds.append(handle)
        return handle

    @property
    def guild(self) -> GuildHandle:
        """The first created guild, for the common single-guild case."""
        if not self._guilds:
            raise SetupError("No guild created yet; call env.create_guild() first")
        return self._guilds[0]

    # ----------------------------------------------------------- diagnostics

    @property
    def http_log(self) -> list[tuple[str, str, dict[str, Any] | None]]:
        """Every REST call the bot made: (method, path, json body)."""
        return self.backend.http_log

    def transcript(self) -> str:
        """Human-readable record of everything that happened, in order.

        One line per gateway event injected and REST call the bot made — the
        "what did the bot actually do" dump, including events DROPPED (missing
        intent) or CENSORED (missing message_content) by intent simulation.
        The pytest plugin attaches this to failing tests automatically.
        """
        lines = []
        for kind, name, payload in self.backend.transcript:
            lines.append(f"{kind:<8} {name:<28} {_summarize(payload)}")
        return "\n".join(lines)

    def raise_errors(self) -> None:
        """Re-raise everything the bot raised during the test, as a group.

        Exceptions from command handlers, app-command callbacks and event
        listeners are captured into :attr:`errors` rather than propagating into
        your test (that is what lets a bot keep running after one handler
        fails). Call this to assert the bot ran cleanly: it raises an
        ``ExceptionGroup`` of everything captured — even a single error — and
        does nothing if there were none.
        """
        self._errors_inspected = True
        captured = list(self._errors)
        if not captured:
            return
        message = f"bot raised {len(captured)} error(s) during the test"
        if all(isinstance(exc, Exception) for exc in captured):
            raise ExceptionGroup(message, captured)  # type: ignore[arg-type]
        raise BaseExceptionGroup(message, captured)

    def inject_error(
        self,
        method: str,
        path: str,
        *,
        status: int = 500,
        code: int = 0,
        message: str = "Internal Server Error (injected by test)",
        times: int | None = 1,
    ) -> None:
        """Make matching REST calls fail, to test the bot's error handling.

        ``path`` is an fnmatch pattern against the API path, e.g.
        ``"/channels/*/messages"``; ``method`` may be ``"*"``. ``times=None``
        keeps the fault active for the rest of the test.
        """
        self.backend.faults.append(
            {
                "method": method,
                "path": path,
                "status": status,
                "code": code,
                "message": message,
                "times": times,
            }
        )


def _summarize(payload: Any, limit: int = 140) -> str:
    """One-line gist of a payload: author/content for messages, else trimmed repr."""
    if not isinstance(payload, dict):
        return "" if payload is None else repr(payload)[:limit]
    parts = []
    author = payload.get("author")
    if isinstance(author, dict) and author.get("username"):
        parts.append(f"author={author['username']}")
    for key in ("content", "name", "custom_id", "user_id", "channel_id"):
        if payload.get(key):
            parts.append(f"{key}={payload[key]!r}")
    data = payload.get("data")
    if isinstance(data, dict) and data.get("name"):
        parts.append(f"command={data['name']!r}")
    text = " ".join(parts) or repr(payload)
    return text[:limit]


class run:
    """``async with simcord.run(bot) as env:`` — attach, fake-login, READY.

    On exit, if the bot raised errors the test never inspected (via
    ``env.errors`` or ``env.raise_errors()``), they are re-raised as an
    ``ExceptionGroup`` so bot bugs cannot pass silently. Opt out with
    ``simcord.run(bot, check_errors=False)``.
    """

    def __init__(self, bot: discord.Client, **options: Any) -> None:
        self._env = Env(bot, **options)

    async def __aenter__(self) -> Env:
        await self._env.start()
        return self._env

    async def __aexit__(self, exc_type: Any, *exc_info: Any) -> None:
        env = self._env
        await env.shutdown()
        # Don't mask an exception already propagating out of the test body.
        if exc_type is None and env.check_errors and not env._errors_inspected:
            env.raise_errors()
