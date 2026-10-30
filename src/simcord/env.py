"""Test environment lifecycle: attach a real bot to the virtual backend."""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import math
import weakref
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import discord

from . import _dpy_internals
from ._runtime import (
    _BOT_SCOPE,
    _CLOCK_SCOPE,
    _LIVE_LOOPS,
    _attach_bot,
    _current_task,
    _restore_loop,
    _Runtime,
    _WorkState,
)
from ._settlement import (
    _active_callbacks,
    _is_parked,
    _next_virtual_timer,
    _owned_tasks,
    _run_due_virtual_callbacks,
    _settle_timeout_message,
    _SettlementState,
    _virtualize_recognized_waits,
    _wakes_recognized_wait,
)
from .backend import Backend
from .backend.errors import SetupError
from .builders import GuildHandle, UserHandle
from .monetization import EntitlementHandle, SKUHandle, _fixture_id
from .types import HttpLogEntry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ._runtime import _CallbackRecord
    from .actors import MemberActor
    from .builders import ChannelHandle
    from .preview import Preview


_C = TypeVar("_C", bound=Callable[..., Any])


class Env:
    """A running test environment around a single bot."""

    def __init__(
        self,
        bot: discord.Client,
        *,
        strict_sync: bool = True,
        check_errors: bool = True,
        approved_intents: discord.Intents | None = None,
        shard_count: int | None = None,
        settle_timeout: float = 5.0,
    ) -> None:
        self.bot = bot
        self.strict_sync = strict_sync
        self.check_errors = check_errors
        self.approved_intents = approved_intents
        self.requested_shard_count = shard_count
        self.settle_timeout = self._validate_timeout(settle_timeout, "settle_timeout")
        self.backend = Backend()
        self._errors: list[BaseException] = []
        self._error_ids: set[int] = set()
        self._errors_acknowledged = 0
        self._guilds: list[GuildHandle] = []
        self._work = _WorkState()
        self._generation = 0
        # Old SDK handles remain valid across bot restarts without retaining old caches.
        self._message_states: weakref.WeakSet[Any] = weakref.WeakSet()
        self._operation_task: asyncio.Task[Any] | None = None
        self._operation_depth = 0
        self._operation_label: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runtime: _Runtime | None = None
        self._advancing = False
        self._advance_task: asyncio.Task[Any] | None = None
        self._real_advance_task: asyncio.Task[None] | None = None
        self._pre_shutdown_hooks: list[Callable[[], Any]] = []
        self._pre_shutdown_task: asyncio.Task[Any] | None = None
        self._pre_shutdown_complete = False
        self._dispatch_observers: list[Callable[[Any], Any]] = []
        self._preview: Preview | None = None
        self._adapter_token: Any = None
        self._webhook_adapter: Any = None
        self._gateway_feed: Any = None
        self._shard_count, self._shard_ids = self._resolve_shards(bot)
        self._started = False
        self._last_dispatch: str | None = None

    @staticmethod
    def _validate_timeout(value: float, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SetupError(f"{name} must be a finite non-negative number")
        if value < 0:
            raise SetupError(f"{name} must be a finite non-negative number")
        return float(value)

    @staticmethod
    def _validate_idle(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SetupError("idle must be a finite positive number")
        if value <= 0:
            raise SetupError("idle must be a finite positive number")
        return float(value)

    @property
    def _virtual_time(self) -> float:
        return self.backend.clock.elapsed

    def now(self) -> datetime:
        """Return the shared aware UTC simulation instant."""
        return self.backend.clock.now()

    @contextmanager
    def time_context(self) -> Iterator[None]:
        """Select SDK UTC helpers for driver code without claiming bot work."""
        token = _CLOCK_SCOPE.set(self)
        adapter = self._webhook_adapter
        previous = _dpy_internals.set_webhook_adapter(adapter) if adapter is not None else None
        try:
            yield
        finally:
            _CLOCK_SCOPE.reset(token)
            if adapter is not None:
                _dpy_internals.reset_webhook_adapter(previous)

    @contextmanager
    def _bot_scope(self) -> Iterator[None]:
        token = _BOT_SCOPE.set((self, self._generation))
        adapter = self._webhook_adapter
        previous = _dpy_internals.set_webhook_adapter(adapter) if adapter is not None else None
        try:
            yield
        finally:
            _BOT_SCOPE.reset(token)
            if adapter is not None:
                _dpy_internals.reset_webhook_adapter(previous)

    def _register(self, registry: list[_C], item: _C, *, kind: str) -> Callable[[], None]:
        if not callable(item):
            raise SetupError(f"{kind} must be callable")
        registry.append(item)

        def unregister() -> None:
            try:
                registry.remove(item)
            except ValueError:
                pass

        return unregister

    def _register_pre_shutdown(self, cleanup: Callable[[], Any]) -> Callable[[], None]:
        """Register Preview-owned cleanup that runs before shutdown takes the guard."""
        return self._register(self._pre_shutdown_hooks, cleanup, kind="pre-shutdown cleanup")

    def _register_dispatch_observer(self, observer: Callable[[Any], Any]) -> Callable[[], None]:
        """Observe backend interactions immediately before gateway emission."""
        return self._register(self._dispatch_observers, observer, kind="dispatch observer")

    def _notify_dispatch_observers(self, interaction: Any) -> None:
        for observer in tuple(self._dispatch_observers):
            observer(interaction)

    async def _run_pre_shutdown(self) -> None:
        if self._pre_shutdown_complete:
            return
        task = self._pre_shutdown_task
        if task is None:

            async def drain() -> None:
                for cleanup in tuple(self._pre_shutdown_hooks):
                    result = cleanup()
                    if inspect.isawaitable(result):
                        await result
                self._pre_shutdown_hooks.clear()
                self._pre_shutdown_complete = True

            task = asyncio.create_task(drain())
            self._pre_shutdown_task = task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        finally:
            if task.done() and self._pre_shutdown_task is task:
                self._pre_shutdown_task = None

    def _begin_operation(self, label: str) -> None:
        scope = _BOT_SCOPE.get()
        if scope is not None and scope[0] is self:
            raise SetupError(f"bot-owned work cannot call simcord operation {label}")
        current = _current_task()
        if self._operation_task is not None and self._operation_task is not current:
            active = self._operation_label or "another operation"
            raise SetupError(f"operation overlaps active {active}")
        if self._operation_task is None:
            self._work.reconcile(self._loop.time() if self._loop is not None else 0.0)
            self._operation_task = current
            self._operation_label = label
        self._operation_depth += 1

    def _end_operation(self) -> None:
        if self._operation_depth:
            self._operation_depth -= 1
        if not self._operation_depth:
            self._operation_task = None
            self._operation_label = None

    def _mark_mutation(self) -> None:
        if self._operation_task is not None and self._operation_task is _current_task():
            if self._preview is not None:
                self._preview._mark_action_dispatched()

    async def start(self) -> None:
        self._begin_operation("start")
        try:
            if self._started:
                raise SetupError("Env already started")
            loop = asyncio.get_running_loop()
            if loop in _LIVE_LOOPS:
                raise SetupError("Only one live Env is supported per event loop")
            _LIVE_LOOPS[loop] = self
            self._loop = loop
            self._started = True
            try:
                await _attach_bot(self, self.bot)
            except BaseException:
                await self._detach_bot()
                self._started = False
                _LIVE_LOOPS.pop(loop, None)
                raise
        finally:
            self._end_operation()

    async def restart_bot(self, bot: discord.Client | None = None) -> None:
        """Drain the old generation, detach it, and attach a fresh bot."""
        self._begin_operation("restart_bot")
        try:
            if not self._started:
                raise SetupError("Env not started; use restart_bot() only inside simcord.run()")
            await self._settle_internal()
            await self._detach_bot()
            await _attach_bot(self, bot or self.bot)
        except BaseException:
            if self._work.executors:
                # A failed drain cannot terminate a native worker or retire
                # its generation. Keep hooks installed for its descendants.
                raise
            await self._detach_bot()
            self._started = False
            if self._loop is not None and _LIVE_LOOPS.get(self._loop) is self:
                _LIVE_LOOPS.pop(self._loop, None)
            raise
        finally:
            self._end_operation()

    def _resolve_shards(self, bot: discord.Client) -> tuple[int, tuple[int, ...]]:
        requested = self.requested_shard_count
        if not isinstance(bot, discord.AutoShardedClient):
            if requested is not None:
                raise SetupError("shard_count is only valid for discord.AutoShardedClient")
            return 1, (0,)
        configured = bot.shard_count
        if configured is not None and requested is not None and configured != requested:
            raise SetupError(
                f"shard_count={requested} conflicts with the client's configured shard_count={configured}"
            )
        shard_count = configured if configured is not None else requested
        if shard_count is None:
            raise SetupError(
                "AutoShardedClient has no shard_count; pass shard_count to the client "
                "or simcord.run(bot, shard_count=...)"
            )
        if not isinstance(shard_count, int) or isinstance(shard_count, bool) or shard_count < 1:
            raise SetupError("shard_count must be a positive integer")
        shard_ids = tuple(bot.shard_ids) if bot.shard_ids is not None else tuple(range(shard_count))
        if not shard_ids:
            raise SetupError("AutoShardedClient must have at least one active shard")
        if len(set(shard_ids)) != len(shard_ids) or any(
            not isinstance(shard_id, int) or isinstance(shard_id, bool) or not 0 <= shard_id < shard_count
            for shard_id in shard_ids
        ):
            raise SetupError(f"shard_ids must be unique integers between 0 and {shard_count - 1}")
        return shard_count, shard_ids

    def _context_for_scope(
        self,
        context: contextvars.Context,
        scope: tuple[Any, int] | None,
    ) -> contextvars.Context:
        context = context.copy()
        context.run(_BOT_SCOPE.set, scope if scope is not None and scope[0] is self else None)
        return context

    def _settlement_state(self) -> _SettlementState:
        return _SettlementState(
            self._work,
            self.bot,
            self._loop,
            self._virtual_time,
            self._advancing,
            self._generation,
            self._last_dispatch,
        )

    def _schedule_timer(
        self,
        record: _CallbackRecord,
        callback: Any,
        args: tuple[Any, ...],
        schedule_context: contextvars.Context | None,
    ) -> None:
        assert self._runtime is not None
        when = record.when
        assert when is not None
        real_call_at = self._runtime.call_at
        real_monotonic = _dpy_internals.real_monotonic

        def run_real() -> None:
            if record.handle is not None and not record.handle.cancelled():
                if _wakes_recognized_wait(self._settlement_state(), record):
                    # The task suspended on a recognized wait after this
                    # timer was scheduled: the wake-up now belongs to the
                    # virtual clock alone.
                    record.real_handle = None
                    return
                # A future fallback can fire while another advance is settling.
                # Re-arm it: that advance may stop before this timer's instant.
                previous = self._real_advance_task
                if self._advancing or (
                    previous is not None
                    and (
                        not previous.done() or (not previous.cancelled() and previous.exception() is not None)
                    )
                ):
                    if when > self._virtual_time:
                        assert real_call_at is not None and real_monotonic is not None
                        record.real_handle = real_call_at(
                            real_monotonic() + max(when - self._virtual_time, 0.000001),
                            run_real,
                            context=schedule_context,
                        )
                    return

                async def advance_real() -> None:
                    assert self._loop is not None
                    try:
                        await self._advance_to(
                            max(self._virtual_time, when), self._loop.time() + self.settle_timeout
                        )
                    except TimeoutError:
                        # This is a settlement failure, not a bot exception.
                        # The retained task delivers it to the waiting/next operation.
                        raise
                    except Exception as error:
                        if self._operation_task is None:
                            self._record_error(error)
                        raise

                assert self._runtime is not None
                task = self._real_advance_task = self._runtime.create_task(
                    advance_real(), context=contextvars.Context()
                )
                # The waiting public operation receives the original failure via
                # result(); otherwise the catch above records asynchronous errors.
                task.add_done_callback(
                    lambda finished: None if finished.cancelled() else finished.exception()
                )

        assert real_call_at is not None and real_monotonic is not None
        remaining = max(when - self._virtual_time, 0.0)
        current = _current_task()
        virtual_birth = current is not None and (
            (current in self._work.external_waits and _dpy_internals.is_wakeup_shaped(callback))
            or _dpy_internals.is_intentional_wait_wakeup(self.bot, callback, args, current)
        )
        if not virtual_birth:
            # Wake-up timers behind a recognized wait are virtual from
            # birth — only advance_time() may ever fire them.
            record.real_handle = real_call_at(
                real_monotonic() + remaining, run_real, context=schedule_context
            )

    async def shutdown(self) -> None:
        await self._run_pre_shutdown()
        self._begin_operation("shutdown")
        try:
            await self._detach_bot()
            self._started = False
            if self._loop is not None and _LIVE_LOOPS.get(self._loop) is self:
                _LIVE_LOOPS.pop(self._loop, None)
        finally:
            self._end_operation()

    async def _detach_bot(self) -> None:
        """Detach bot machinery and cancel bot-owned work only."""
        current = _current_task()
        assert self._loop is not None or not self._work.executors
        deadline = self._loop.time() + self.settle_timeout if self._loop is not None else 0.0
        if self._real_advance_task is not None and self._real_advance_task is not current:
            self._real_advance_task.cancel()
            await asyncio.gather(self._real_advance_task, return_exceptions=True)
            self._real_advance_task = None
        while True:
            to_cancel = [task for task in self._work.tasks if task is not current and not task.done()]
            for executor_record in self._work.executors.values():
                if executor_record.caller is not None:
                    executor_record.caller.cancel()
            if not to_cancel:
                if self._work.executors:
                    assert self._loop is not None
                    remaining = deadline - self._loop.time()
                    if remaining <= 0:
                        raise TimeoutError("bot did not detach; native executor work remains tracked")
                    await asyncio.wait(
                        tuple(self._work.executors),
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    continue
                await asyncio.sleep(0)
                if not self._work.executors and not any(
                    task is not current and not task.done() for task in self._work.tasks
                ):
                    break
                continue
            for task in to_cancel:
                task.cancel()
            await asyncio.gather(*to_cancel, return_exceptions=True)
            for task in to_cancel:
                if not task.cancelled() and (error := task.exception()) is not None:
                    self._record_error(error)
            await asyncio.sleep(0)

        if self._gateway_feed is not None:
            try:
                self.backend.subscribers.remove(self._gateway_feed)
            except ValueError:
                pass
            self._gateway_feed = None
        if isinstance(self.bot, discord.AutoShardedClient):
            _dpy_internals.clear_shards(self.bot)
        if self._adapter_token is not None:
            _dpy_internals.reset_webhook_adapter(self._adapter_token)
            self._adapter_token = None
        self._webhook_adapter = None
        for task, record in list(self._work.tasks.items()):
            if task.done():
                assert self._loop is not None
                self._work.inspect_task(task, record, self._loop, self._record_error, force=True)
        with self._work._callback_lock:
            callbacks = tuple(self._work.callbacks.values())
        for record in callbacks:
            if record.handle is not None:
                record.handle.cancel()
            self._work.retire(record)
        with self._work._callback_lock:
            self._work.timers.clear()
        _restore_loop(self)
        _dpy_internals.release_clocks(self)
        self._work.tasks.clear()
        self._work.external_waits.clear()

    async def external_wait(self, awaitable: Any, *, reason: str) -> Any:
        """Await one explicitly declared external input without blocking settlement.

        Declarations apply to the calling task, not its siblings or children.
        Framework orchestration and lifecycle tasks must declare their own
        intentional waits; unrelated unknown waits remain settlement errors.

        Wake-up timers scheduled by the declaring task while inside the wait
        are virtual: they resume only through :meth:`advance_time` or the
        awaited input itself, never the wall clock. Timers owned by other
        already-running tasks keep their normal real-time behavior.
        """
        task = _current_task()
        record = self._work.tasks.get(task) if task is not None else None
        if not isinstance(reason, str) or not reason.strip():
            message = "external_wait reason must be a non-empty string"
        elif record is None:
            message = "external_wait must run inside bot-owned work"
        elif task in self._work.external_waits:
            message = "external_wait declarations cannot be nested"
        else:
            message = None
        if message is not None:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise SetupError(message)
        assert task is not None
        self._work.external_waits[task] = reason.strip()
        try:
            return await awaitable
        finally:
            self._work.external_waits.pop(task, None)

    async def settle(
        self,
        timeout: float | None = None,  # noqa: ASYNC109
        idle: float = 0.05,
    ) -> None:
        """Join all runnable bot-owned work, leaving only recognized waits."""
        self._begin_operation("settle")
        try:
            await self._settle_internal(timeout=timeout, idle=idle)
        finally:
            self._end_operation()

    async def _settle_internal(
        self,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
        idle: float = 0.05,
        dispatch: str | None = None,
        _deadline: float | None = None,
    ) -> None:
        effective = self.settle_timeout if timeout is None else self._validate_timeout(timeout, "timeout")
        interval = self._validate_idle(idle)
        assert self._loop is not None
        self._last_dispatch = dispatch
        deadline = self._loop.time() + effective if _deadline is None else _deadline
        stable_empty = 0
        while True:
            if self._loop.time() - self._work.reconciled_at >= interval:
                self._work.reconcile(self._loop.time())
            _run_due_virtual_callbacks(self._settlement_state(), deadline)
            before_yield = self._work.revision
            await asyncio.sleep(0)
            advancing = self._real_advance_task or self._advance_task
            if advancing is not None and advancing is not _current_task():
                done, _ = await asyncio.wait((advancing,), timeout=max(0.0, deadline - self._loop.time()))
                if not done:
                    raise TimeoutError("bot did not settle while advancing time")
                if self._real_advance_task is advancing:
                    self._real_advance_task = None
                advancing.result()
                continue
            # Keep draining actual progress before rescanning parked SDK work.
            # A busy callback chain must still reach the deadline check below.
            if self._work.revision != before_yield and self._loop.time() < deadline:
                stable_empty = 0
                continue
            revision = self._work.revision
            state = self._settlement_state()
            pending = _owned_tasks(state)
            _virtualize_recognized_waits(state, pending)
            # Deadline facts are read only after virtualization canceled fallbacks.
            parked = [task for task in pending if _is_parked(state, task, deadline)]
            active_callbacks = _active_callbacks(state, deadline)
            if not self._work.executors and not active_callbacks and len(parked) == len(pending):
                stable_empty += 1
                if stable_empty >= 2:
                    return
                continue
            stable_empty = 0
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    _settle_timeout_message(
                        state,
                        [task for task in pending if task not in parked],
                        pending,
                        effective,
                        active_callbacks,
                    )
                )
            if any(
                record.when is not None and record.when <= self._virtual_time for record in active_callbacks
            ):
                continue
            progress = self._loop.create_future()
            self._work.progress = progress
            try:
                # Arm before checking the revision: scheduling/completion between
                # classification and arming cannot become a lost notification.
                if self._work.revision != revision:
                    continue
                await asyncio.wait(
                    (progress, *self._work.executors),
                    timeout=min(interval, remaining),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                self._work.progress = None
                progress.cancel()

    def _next_resource_deadline(self) -> float | None:
        deadlines = (
            self.backend.next_poll_deadline(),
            self.backend.next_scheduled_event_deadline(),
            self.backend.next_entitlement_deadline(),
        )
        return min(
            (
                self._virtual_time + max(0.0, (at - self.now()).total_seconds())
                for at in deadlines
                if at is not None
            ),
            default=None,
        )

    def _process_resource_deadlines(self) -> None:
        with self._bot_scope():
            self.backend.expire_due_polls()
            self.backend.activate_due_scheduled_events()
            self.backend.process_entitlement_deadlines()

    def _move_clock(self, target: float) -> None:
        amount = max(0.0, target - self._virtual_time)
        self.backend.advance_clock(amount)
        self._process_resource_deadlines()

    async def _advance_to(self, target: float, deadline: float) -> None:
        if self._advance_task is not None:
            assert self._loop is not None
            await asyncio.wait_for(
                asyncio.shield(self._advance_task), timeout=max(0.0, deadline - self._loop.time())
            )
        self._advance_task = _current_task()
        self._advancing = True
        try:
            self._process_resource_deadlines()
            await self._settle_internal(_deadline=deadline)
            while self._virtual_time < target:
                if self._loop is not None and self._loop.time() >= deadline:
                    raise TimeoutError("bot did not settle while advancing time")
                candidates = [target]
                for instant in (
                    self._next_resource_deadline(),
                    _next_virtual_timer(self._settlement_state()),
                ):
                    if instant is not None:
                        candidates.append(max(self._virtual_time, instant))
                self._move_clock(min(candidates))
                await self._settle_internal(_deadline=deadline)
            self._process_resource_deadlines()
            await self._settle_internal(_deadline=deadline)
        finally:
            self._advancing = False
            self._advance_task = None

    async def advance_time(self, seconds: float) -> None:
        amount = self._validate_timeout(seconds, "seconds")
        if not self._started:
            raise SetupError("Env is not running")
        self._begin_operation("advance_time")
        requested = self.now()
        try:
            assert self._loop is not None
            requested = self.now()
            from datetime import timedelta

            requested += timedelta(seconds=amount)
            deadline = self._loop.time() + self.settle_timeout
            await self._advance_to(self._virtual_time + amount, deadline)
        except TimeoutError as error:
            error.add_note(
                f"advance_time requested {requested.isoformat()}; reached {self.now().isoformat()}; partial progress is committed"
            )
            raise
        finally:
            self._end_operation()

    @property
    def error_cursor(self) -> int:
        """Non-consuming position used by internal operation diagnostics."""
        return len(self._errors)

    def errors_since(self, cursor: int) -> tuple[BaseException, ...]:
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise SetupError("error cursor must be a non-negative integer")
        return tuple(self._errors[cursor:])

    def _record_error(self, error: BaseException) -> None:
        identity = id(error)
        if identity in self._error_ids:
            return
        self._error_ids.add(identity)
        self._errors.append(error)

    @property
    def errors(self) -> list[BaseException]:
        """Return a shallow snapshot and acknowledge only the currently captured prefix."""
        self._errors_acknowledged = self.error_cursor
        return list(self._errors)

    def _capture_errors(self) -> None:
        from discord.ext import commands

        _dpy_internals.capture_ui_errors(self.bot, self._record_error)

        async def on_command_error(_ctx: Any, error: BaseException) -> None:
            if not isinstance(error, commands.CommandNotFound):
                self._record_error(error)

        add_listener = getattr(self.bot, "add_listener", None)
        if add_listener is not None:
            add_listener(on_command_error, "on_command_error")

        original_on_error = self.bot.on_error

        async def on_error(event_method: str, /, *args: Any, **kwargs: Any) -> None:
            import sys

            exc = sys.exc_info()[1]
            if exc is not None:
                self._record_error(exc)
            await original_on_error(event_method, *args, **kwargs)

        self.bot.on_error = on_error  # type: ignore[method-assign]
        tree = getattr(self.bot, "tree", None)
        if tree is not None:
            original = tree.on_error

            async def on_tree_error(interaction: Any, error: BaseException) -> None:
                self._record_error(error)
                await original(interaction, error)

            tree.on_error = on_tree_error

    def preview(
        self,
        channel: ChannelHandle,
        *,
        viewers: Sequence[MemberActor | UserHandle],
        layout: Literal["message", "channel"] = "message",
        display: Literal["responsive", "fixed"] = "responsive",
        width: int = 960,
        height: int = 720,
        locale: str = "en-US",
        timezone: str = "UTC",
        presentation_time: datetime | None = None,
        assets: Mapping[str, tuple[str, bytes]] | None = None,
        sku_presentations: Mapping[str, Mapping[str, str]] | None = None,
        port: int | None = None,
    ) -> Preview:
        """Create one eagerly validated local preview context manager.

        ``layout="message"`` focuses one message; ``layout="channel"`` shows
        authorized channel history and a real actor-backed composer. ``channel``
        is the channel the session presents. ``viewers`` is a non-empty
        allowlist of same-Env handles — members for guild channels, the owning
        ``UserHandle`` for DMs. Human pages default to ``display="responsive"``,
        which fits the available workspace. ``display="fixed"`` uses an exact
        profile without shrinking. ``width``/``height`` configure that profile
        and managed-capture defaults; browser presentation changes are page-local.
        ``locale`` and ``timezone`` seed the rendered profile.
        ``assets`` maps otherwise-remote media URLs to ``(filename, bytes)``
        tuples so they render offline. ``sku_presentations`` maps positive SKU
        snowflake strings to exact ``name``/``price_text`` and a supported
        Discord ``locale``; optional ``icon_url`` values require matching
        supported raster bytes in ``assets``. No purchase state is modeled.
        ``port`` pins the loopback port.

        Returns an async context manager serving the authorized preview;
        ``preview.url`` is the capability-bearing address.
        """
        if not self._started:
            raise SetupError("Env is not running")
        if self._preview is not None and not self._preview._closed:
            raise SetupError("Only one active Preview is allowed per Env")
        self._begin_operation("preview")
        try:
            from .preview import make_preview

            return make_preview(
                self,
                channel,
                viewers=viewers,
                layout=layout,
                display=display,
                width=width,
                height=height,
                locale=locale,
                timezone=timezone,
                presentation_time=presentation_time,
                assets=assets,
                sku_presentations=sku_presentations,
                port=port,
            )
        finally:
            self._end_operation()

    # -------------------------------------------------------------- builders

    def create_user(
        self,
        name: str,
        *,
        bot: bool = False,
        system: bool = False,
        global_name: str | None = None,
        discriminator: str = "0",
        public_flags: discord.PublicUserFlags | None = None,
        avatar: str | None = None,
    ) -> UserHandle:
        """Create a virtual user.

        ``bot=True`` makes messages this user posts arrive with
        ``message.author.bot`` set — the way a bot/application account, or a
        webhook (see :meth:`GuildHandle.create_webhook`), appears to the bot
        under test. ``system=True`` marks an official Discord system account.
        ``global_name`` is the display name (distinct from the unique
        ``name``/username); ``discriminator`` is the legacy four-digit tag
        (``"0"`` for migrated accounts); ``public_flags`` carries badge flags
        such as ``verified_bot``; ``avatar`` is the avatar image hash
        (``None`` falls back to a default avatar).
        """
        return UserHandle(
            self,
            self.backend.make_user(
                name,
                bot=bot,
                system=system,
                global_name=global_name,
                discriminator=discriminator,
                public_flags=public_flags.value if public_flags is not None else 0,
                avatar=avatar,
            ),
        )

    def create_sku(
        self,
        name: str,
        *,
        type: discord.SKUType = discord.SKUType.subscription,
        flags: discord.SKUFlags | None = None,
        id: int | None = None,
        slug: str | None = None,
    ) -> SKUHandle:
        """Arrange an explicit catalog SKU, including non-grantable subscription groups."""
        if not isinstance(type, discord.SKUType):
            raise SetupError("type must be a discord.SKUType")
        if flags is not None and not isinstance(flags, discord.SKUFlags):
            raise SetupError("flags must be discord.SKUFlags")
        if flags is None:
            flags = (
                discord.SKUFlags(guild_subscription=True)
                if type in (discord.SKUType.subscription, discord.SKUType.subscription_group)
                else discord.SKUFlags(available=True)
            )
        sku = self.backend.create_sku(name, type=type.value, flags=flags.value, id=id, slug=slug)
        return SKUHandle(self, sku.id)

    def seed_entitlement(
        self,
        sku: SKUHandle | int,
        *,
        user: UserHandle | MemberActor | int | None = None,
        guild: GuildHandle | int | None = None,
        type: discord.EntitlementType | None = None,
        id: int | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        deleted: bool = False,
        consumed: bool = False,
        scheduled_end_at: datetime | None = None,
    ) -> EntitlementHandle:
        """Arrange historical state without emitting CREATE, including before startup."""
        from .actors import MemberActor

        if type is not None and not isinstance(type, discord.EntitlementType):
            raise SetupError("type must be a discord.EntitlementType")
        entitlement = self.backend.seed_entitlement(
            _fixture_id(self, sku, SKUHandle, "sku"),
            user_id=_fixture_id(self, user, (UserHandle, MemberActor), "user") if user is not None else None,
            guild_id=_fixture_id(self, guild, GuildHandle, "guild") if guild is not None else None,
            type=type.value if type is not None else None,
            id=id,
            starts_at=starts_at,
            ends_at=ends_at,
            deleted=deleted,
            consumed=consumed,
            scheduled_end_at=scheduled_end_at,
        )
        return EntitlementHandle(self, entitlement.id)

    async def grant_entitlement(
        self,
        sku: SKUHandle | int,
        *,
        user: UserHandle | MemberActor | int | None = None,
        guild: GuildHandle | int | None = None,
        type: discord.EntitlementType | None = None,
        id: int | None = None,
        starts_at: datetime | None = None,
        scheduled_end_at: datetime | None = None,
    ) -> EntitlementHandle:
        """Commit a live grant, emit CREATE and settle. Failures do not roll it back."""
        from .actors import MemberActor

        self._begin_operation("grant_entitlement")
        try:
            if not self._started:
                raise SetupError("grant_entitlement requires a running Env")
            if type is not None and not isinstance(type, discord.EntitlementType):
                raise SetupError("type must be a discord.EntitlementType")
            entitlement = self.backend.grant_entitlement(
                _fixture_id(self, sku, SKUHandle, "sku"),
                user_id=_fixture_id(self, user, (UserHandle, MemberActor), "user")
                if user is not None
                else None,
                guild_id=_fixture_id(self, guild, GuildHandle, "guild") if guild is not None else None,
                type=type.value if type is not None else None,
                id=id,
                starts_at=starts_at,
                scheduled_end_at=scheduled_end_at,
            )
            self._mark_mutation()
            try:
                await self._settle_internal(dispatch="ENTITLEMENT_CREATE")
            except BaseException as error:
                error.add_note(
                    f"Entitlement {entitlement.id} was committed; recover with env.get_entitlement({entitlement.id}). Do not retry the grant."
                )
                raise
            return EntitlementHandle(self, entitlement.id)
        finally:
            self._end_operation()

    def get_entitlement(self, entitlement_id: int) -> EntitlementHandle:
        """Recover fixture authority by ID, including after a committed grant times out."""
        entitlement_id = _fixture_id(self, entitlement_id, EntitlementHandle, "entitlement")
        self.backend.get_entitlement(entitlement_id)
        return EntitlementHandle(self, entitlement_id)

    def create_guild(
        self,
        name: str = "Test Guild",
        *,
        id: int | None = None,
        shard_id: int | None = None,
        owner: UserHandle | None = None,
        description: str | None = None,
        verification_level: discord.VerificationLevel | None = None,
        notifications: discord.NotificationLevel | None = None,
        content_filter: discord.ContentFilter | None = None,
        preferred_locale: str | None = None,
        afk_timeout: int | None = None,
    ) -> GuildHandle:
        """Create a guild.

        Pass ``id`` to pin a known id — e.g. to match a bot that syncs its
        commands to a hardcoded guild id, so ``strict_sync`` can stay on.
        ``shard_id`` creates a snowflake owned by that shard; when both are
        provided they must agree. Pass ``owner`` to make a specific user the
        guild owner (owners bypass every permission check); by default a fresh
        synthetic owner is created so the bot never owns the guild. The remaining
        keywords seed guild settings the bot can read back off ``discord.Guild``
        and could later change itself via ``Guild.edit`` (the keyword names here
        are friendlier aliases — e.g. ``notifications`` for
        ``default_notifications``).
        """
        if shard_id is not None:
            if (
                not isinstance(shard_id, int)
                or isinstance(shard_id, bool)
                or not 0 <= shard_id < self._shard_count
            ):
                raise ValueError(f"shard_id must be between 0 and {self._shard_count - 1}")
            if id is not None and (id >> 22) % self._shard_count != shard_id:
                raise ValueError(f"id {id} does not belong to shard {shard_id}")
        settings: dict[str, Any] = {}
        if description is not None:
            settings["description"] = description
        if verification_level is not None:
            settings["verification_level"] = verification_level.value
        if notifications is not None:
            settings["default_message_notifications"] = notifications.value
        if content_filter is not None:
            settings["explicit_content_filter"] = content_filter.value
        if preferred_locale is not None:
            settings["preferred_locale"] = preferred_locale
        if afk_timeout is not None:
            settings["afk_timeout"] = afk_timeout
        handle = GuildHandle(
            self,
            self.backend.create_guild(
                name,
                id=id,
                owner_id=owner.id if owner is not None else None,
                shard_id=shard_id,
                shard_count=self._shard_count,
                **settings,
            ),
        )
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
    def http_requests(self) -> list[HttpLogEntry]:
        """Every REST call the bot made as a structured request record."""
        return self.backend.http_requests

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
        This acknowledges only the current prefix, so errors captured later
        still fail teardown.
        """
        self._raise_errors_since(0)

    def _raise_errors_since(self, cursor: int) -> None:
        captured = self.errors_since(cursor)
        self._errors_acknowledged = self.error_cursor
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


def _guard_env_sync(method: Any) -> Any:
    @wraps(method)
    def guarded(self: Env, *args: Any, **kwargs: Any) -> Any:
        self._begin_operation(method.__name__)
        try:
            return method(self, *args, **kwargs)
        finally:
            self._end_operation()

    return guarded


Env.create_user = _guard_env_sync(Env.create_user)  # type: ignore[method-assign]
Env.create_guild = _guard_env_sync(Env.create_guild)  # type: ignore[method-assign]
Env.create_sku = _guard_env_sync(Env.create_sku)  # type: ignore[method-assign]
Env.seed_entitlement = _guard_env_sync(Env.seed_entitlement)  # type: ignore[method-assign]
Env.inject_error = _guard_env_sync(Env.inject_error)  # type: ignore[method-assign]


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

    On exit, uninspected bot errors are re-raised as an ``ExceptionGroup``.
    Error inspection acknowledges only the current prefix. Result handles stay
    live, while individual mutable payload reads are detached snapshots.
    Disable teardown checking with ``simcord.run(bot, check_errors=False)``.
    """

    def __init__(self, bot: discord.Client, **options: Any) -> None:
        self._env = Env(bot, **options)

    async def __aenter__(self) -> Env:
        await self._env.start()
        self._clock_context = self._env.time_context()
        self._clock_context.__enter__()
        return self._env

    async def __aexit__(self, exc_type: Any, *exc_info: Any) -> None:
        env = self._env
        try:
            await env.shutdown()
        finally:
            self._clock_context.__exit__(exc_type, *exc_info)
        # Don't mask an exception already propagating out of the test body.
        if exc_type is None and env.check_errors:
            env._raise_errors_since(env._errors_acknowledged)
