"""Test environment lifecycle: attach a real bot to the virtual backend."""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import math
import time
import weakref
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeVar, cast

import discord

from . import _dpy_internals
from . import intents as _intents
from .backend import Backend, serializers
from .backend.errors import SetupError
from .builders import GuildHandle, UserHandle
from .gateway import ShardRouter
from .http import FakeHTTPClient, FakeWebhookAdapter

_BOT_SCOPE: contextvars.ContextVar[tuple[Any, int] | None] = contextvars.ContextVar(
    "simcord_bot_scope", default=None
)

# Cap on virtual callbacks drained per settle turn so self-rescheduling
# zero-delay chains cannot starve the settlement deadline check.
_DUE_CALLBACK_BATCH = 64

_C = TypeVar("_C", bound=Callable[..., Any])


@dataclass(slots=True)
class _TaskRecord:
    generation: int
    label: str
    owner: weakref.ReferenceType[asyncio.Task[Any]] | None
    inspection_waiting: bool = False


@dataclass(slots=True)
class _CallbackRecord:
    handle: asyncio.TimerHandle | asyncio.Handle | None
    when: float | None
    label: str
    real_handle: asyncio.TimerHandle | None = None


def _current_task() -> asyncio.Task[Any] | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class _VirtualTime:
    __slots__ = ("_env", "_real")

    def __init__(self, env: Env, real: Any) -> None:
        self._env = env
        self._real = real

    def monotonic(self) -> float:
        scope = _BOT_SCOPE.get()
        if scope is not None and scope[0] is self._env:
            return self._env._virtual_time
        return self._real.monotonic()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


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
        self._errors_inspected = False
        self._guilds: list[GuildHandle] = []
        self._task_records: dict[asyncio.Task[Any], _TaskRecord] = {}
        self._callbacks: list[_CallbackRecord] = []
        self._generation = 0
        self._external_waits: dict[asyncio.Task[Any], str] = {}
        self._operation_task: asyncio.Task[Any] | None = None
        self._operation_depth = 0
        self._operation_label: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._orig_create_task: Any = None
        self._orig_call_soon: Any = None
        self._orig_call_later: Any = None
        self._orig_call_at: Any = None
        self._orig_call_soon_threadsafe: Any = None
        self._orig_run_in_executor: Any = None
        self._orig_view_time: Any = None
        self._orig_monotonic: Any = None
        self._virtual_time = 0.0
        self._pre_shutdown_hooks: list[Callable[[], Any]] = []
        self._pre_shutdown_task: asyncio.Task[Any] | None = None
        self._pre_shutdown_complete = False
        self._dispatch_observers: list[Callable[[Any], Any]] = []
        self._preview: Any | None = None
        self._adapter_token: Any = None
        self._gateway_feed: Any = None
        self._shard_count = 1
        self._shard_ids = (0,)
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

    @contextmanager
    def _bot_scope(self) -> Iterator[None]:
        token = _BOT_SCOPE.set((self, self._generation))
        try:
            yield
        finally:
            _BOT_SCOPE.reset(token)

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

    def _begin_operation(self, label: str) -> asyncio.Task[Any] | None:
        scope = _BOT_SCOPE.get()
        if scope is not None and scope[0] is self:
            raise SetupError(f"bot-owned work cannot call simcord operation {label}")
        current = _current_task()
        if self._operation_task is not None and self._operation_task is not current:
            active = self._operation_label or "another operation"
            raise SetupError(f"operation overlaps active {active}")
        if self._operation_task is None:
            self._operation_task = current
            self._operation_label = label
        self._operation_depth += 1
        return current

    def _end_operation(self, _token: asyncio.Task[Any] | None) -> None:
        if self._operation_depth:
            self._operation_depth -= 1
        if not self._operation_depth:
            self._operation_task = None
            self._operation_label = None

    async def start(self) -> None:
        token = self._begin_operation("start")
        try:
            if self._started:
                raise SetupError("Env already started")
            self._started = True
            self._loop = asyncio.get_running_loop()
            await self._attach_bot(self.bot)
        finally:
            self._end_operation(token)

    async def restart_bot(self, bot: discord.Client | None = None) -> None:
        """Drain the old generation, detach it, and attach a fresh bot."""
        token = self._begin_operation("restart_bot")
        try:
            if not self._started:
                raise SetupError("Env not started; use restart_bot() only inside simcord.run()")
            await self._settle_internal()
            await self._detach_bot()
            await self._attach_bot(bot or self.bot)
            for handle in self._guilds:
                guild = self.backend.get_guild(handle.id)
                with self._bot_scope():
                    self.backend.emit("GUILD_CREATE", serializers.guild_create_payload(self.backend, guild))
            await self._settle_internal(timeout=5.0)
        finally:
            self._end_operation(token)

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

    def _inspect_task_result(
        self,
        completed: asyncio.Task[Any],
        record: _TaskRecord | None,
        *,
        force: bool = False,
    ) -> None:
        if record is None or self._task_records.get(completed) is not record:
            return
        if not completed.done():
            return
        if completed.cancelled():
            self._task_records.pop(completed, None)
            return
        error = getattr(completed, "_exception", None)
        if error is None or not getattr(completed, "_log_traceback", False):
            self._task_records.pop(completed, None)
            return
        owner = record.owner() if record.owner is not None else None
        if not force and owner is not None and owner is not completed and not owner.done():
            if not record.inspection_waiting:
                record.inspection_waiting = True

                def retry(_owner: asyncio.Task[Any]) -> None:
                    if self._loop is None:
                        self._inspect_task_result(completed, record)
                    else:
                        self._loop.call_soon(self._inspect_task_result, completed, record)

                owner.add_done_callback(retry)
            return
        self._record_error(error)
        completed.exception()
        self._task_records.pop(completed, None)

    def _track_task(self, task: asyncio.Task[Any], generation: int, label: str) -> None:
        owner = _current_task()
        self._task_records[task] = _TaskRecord(
            generation,
            label,
            weakref.ref(owner) if owner is not None else None,
        )

        def done(completed: asyncio.Task[Any]) -> None:
            record = self._task_records.get(completed)
            if self._loop is None:
                self._inspect_task_result(completed, record)
                return
            self._loop.call_soon(self._inspect_task_result, completed, record)

        task.add_done_callback(done)

    def _track_callback(
        self,
        original: Any,
        callback: Any,
        args: tuple[Any, ...],
        *,
        context: contextvars.Context | None,
        when: float | None,
        schedule_args: tuple[Any, ...] = (),
    ) -> asyncio.Handle:
        bound_task = getattr(callback, "__self__", None)
        if isinstance(bound_task, asyncio.Task):
            # A task resumption callback (__step/__wakeup) must run in the exact
            # Context the task was scheduled with — scheduling a copy resumes the
            # coroutine in a different Context object, where ContextVar.reset of
            # a pre-suspension token raises "created in a different Context".
            # Ownership is already carried by the task's own context.
            owner_record = self._task_records.get(bound_task)
            scope = (
                (self, owner_record.generation)
                if owner_record is not None
                else context.get(_BOT_SCOPE)
                if context is not None
                else None
            )
            schedule_context = context
        else:
            scope = _BOT_SCOPE.get()
            schedule_context = self._context_for_scope(context, scope) if context is not None else context
        if scope is None or scope[0] is not self:
            return original(*schedule_args, callback, *args, context=schedule_context)
        label = getattr(callback, "__qualname__", None) or getattr(
            callback, "__name__", type(callback).__name__
        )
        record = _CallbackRecord(None, when, label)
        called = False

        def invoke(*call_args: Any) -> None:
            nonlocal called
            called = True
            if record.real_handle is not None:
                record.real_handle.cancel()
            if record.handle is not None:
                try:
                    self._callbacks.remove(record)
                except ValueError:
                    pass
            try:
                callback(*call_args)
            except BaseException as error:
                self._record_error(error)
                raise

        invoke.__simcord_original_callback__ = callback
        if when is None:
            handle = original(*schedule_args, invoke, *args, context=schedule_context)
        else:
            assert self._loop is not None
            handle = asyncio.TimerHandle(when, invoke, args, self._loop, context=schedule_context)

            def run_real() -> None:
                if record.handle is not None and not record.handle.cancelled():
                    # The real clock reached this timer: sync the virtual clock
                    # so code observing monotonic() (e.g. View timeouts) sees
                    # the elapsed delay too.
                    if record.when is not None and record.when > self._virtual_time:
                        self._virtual_time = record.when
                    record.handle._run()

            remaining = max(when - self._virtual_time, 0.0)
            record.real_handle = self._orig_call_at(
                self._orig_monotonic() + remaining, run_real, context=schedule_context
            )
        record.handle = handle
        if not called:
            self._callbacks.append(record)
        return handle

    async def _attach_bot(self, bot: discord.Client) -> None:
        """Install fakes, start a new bot generation, and bring it to READY."""
        self.bot = bot
        loop = self._loop
        assert loop is not None
        _dpy_internals.verify_loop(loop)
        self._generation += 1
        self._shard_count, self._shard_ids = self._resolve_shards(bot)

        self._orig_create_task = loop.create_task
        self._orig_call_soon = loop.call_soon
        self._orig_call_later = loop.call_later
        self._orig_call_at = loop.call_at
        self._orig_call_soon_threadsafe = loop.call_soon_threadsafe
        self._orig_run_in_executor = loop.run_in_executor

        def tracking_create_task(coro: Any, **kwargs: Any) -> asyncio.Task[Any]:
            scope = _BOT_SCOPE.get()
            context = kwargs.get("context")
            if context is not None:
                kwargs["context"] = self._context_for_scope(context, scope)
            label = _dpy_internals.task_label(coro) if scope is not None and scope[0] is self else None
            task = self._orig_create_task(coro, **kwargs)
            if not isinstance(task, asyncio.Task):
                raise SetupError("simcord requires the loop task factory to return asyncio.Task objects")
            if label is not None:
                assert scope is not None
                self._track_task(task, scope[1], label)
            return task

        def call_soon(
            callback: Any, *args: Any, context: contextvars.Context | None = None
        ) -> asyncio.Handle:
            return self._track_callback(self._orig_call_soon, callback, args, context=context, when=None)

        def call_later(
            delay: float, callback: Any, *args: Any, context: contextvars.Context | None = None
        ) -> asyncio.TimerHandle:
            return cast(
                asyncio.TimerHandle,
                self._track_callback(
                    self._orig_call_later,
                    callback,
                    args,
                    context=context,
                    when=self._virtual_time + max(delay, 0.0),
                    schedule_args=(delay,),
                ),
            )

        def call_at(
            when: float, callback: Any, *args: Any, context: contextvars.Context | None = None
        ) -> asyncio.TimerHandle:
            return cast(
                asyncio.TimerHandle,
                self._track_callback(
                    self._orig_call_at,
                    callback,
                    args,
                    context=context,
                    when=self._virtual_time + max(when - loop.time(), 0.0),
                    schedule_args=(when,),
                ),
            )

        def call_soon_threadsafe(
            callback: Any, *args: Any, context: contextvars.Context | None = None
        ) -> asyncio.Handle:
            return self._track_callback(
                self._orig_call_soon_threadsafe, callback, args, context=context, when=None
            )

        def run_in_executor(executor: Any, func: Any, *args: Any) -> Any:
            scope = _BOT_SCOPE.get()
            if scope is None or scope[0] is not self:
                return self._orig_run_in_executor(executor, func, *args)

            def owned_call(*call_args: Any) -> Any:
                token = _BOT_SCOPE.set(scope)
                try:
                    return func(*call_args)
                finally:
                    _BOT_SCOPE.reset(token)

            return self._orig_run_in_executor(executor, owned_call, *args)

        loop.create_task = tracking_create_task  # type: ignore[method-assign]
        loop.call_soon = call_soon  # type: ignore[method-assign]
        loop.call_later = call_later  # type: ignore[method-assign]
        loop.call_at = call_at  # type: ignore[method-assign]
        loop.call_soon_threadsafe = call_soon_threadsafe  # type: ignore[method-assign]
        loop.run_in_executor = run_in_executor  # type: ignore[method-assign]

        if self._virtual_time == 0.0:
            self._virtual_time = loop.time()
        self._orig_monotonic = time.monotonic

        def monotonic() -> float:
            scope = _BOT_SCOPE.get()
            if scope is not None and scope[0] is self:
                return self._virtual_time
            return self._orig_monotonic()

        time.monotonic = monotonic
        self._orig_view_time = _dpy_internals.view_time()
        _dpy_internals.swap_view_time(_VirtualTime(self, self._orig_view_time))
        _dpy_internals.install_http(bot, FakeHTTPClient(self.backend, loop))
        _dpy_internals.set_guild_ready_timeout(bot, 0.0)
        self._adapter_token = _dpy_internals.set_webhook_adapter(FakeWebhookAdapter(self.backend))

        state = _dpy_internals.get_state(bot)
        state.shard_count = self._shard_count
        state.shard_ids = list(self._shard_ids)
        router = ShardRouter(self.backend, state, bot.dispatch, self._shard_count, self._shard_ids)
        raw_feed = router.feed

        def windowed_feed(event: str, payload: Any) -> None:
            with self._bot_scope():
                raw_feed(event, payload)

        self._gateway_feed = windowed_feed
        self.backend.subscribers.append(windowed_feed)
        if not isinstance(bot, discord.AutoShardedClient):
            _dpy_internals.install_websocket(bot, router.websockets[0])
        try:
            self._capture_errors()
            if self.approved_intents is not None and _intents.missing_privileged_intents(
                bot.intents, self.approved_intents
            ):
                raise discord.PrivilegedIntentsRequired(shard_id=self._shard_ids[0])
            with self._bot_scope():
                await bot.login("simcord.fake.token")
                if isinstance(bot, discord.AutoShardedClient):
                    bot.shard_count = self._shard_count
                    _dpy_internals.install_shards(bot, router.shards)
                for shard_id in self._shard_ids:
                    router.identify(shard_id)
            await self._settle_internal(timeout=5.0)
        except BaseException:
            await self._detach_bot()
            raise

    async def shutdown(self) -> None:
        await self._run_pre_shutdown()
        token = self._begin_operation("shutdown")
        try:
            await self._detach_bot()
            self._started = False
        finally:
            self._end_operation(token)

    async def _detach_bot(self) -> None:
        """Detach bot machinery and cancel bot-owned work only."""
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
        current = _current_task()
        while True:
            to_cancel = [task for task in self._task_records if task is not current and not task.done()]
            if not to_cancel:
                await asyncio.sleep(0)
                if not any(task is not current and not task.done() for task in self._task_records):
                    break
                continue
            for task in to_cancel:
                task.cancel()
            await asyncio.gather(*to_cancel, return_exceptions=True)
            for task in to_cancel:
                if not task.cancelled() and (error := task.exception()) is not None:
                    self._record_error(error)
            await asyncio.sleep(0)

        for task, record in list(self._task_records.items()):
            if task.done():
                self._inspect_task_result(task, record, force=True)
        for record in self._callbacks:
            if record.handle is not None:
                record.handle.cancel()
            if record.real_handle is not None:
                record.real_handle.cancel()
        self._callbacks.clear()
        if self._loop is not None and self._orig_create_task is not None:
            self._loop.create_task = self._orig_create_task  # type: ignore[method-assign]
            self._loop.call_soon = self._orig_call_soon  # type: ignore[method-assign]
            self._loop.call_later = self._orig_call_later  # type: ignore[method-assign]
            self._loop.call_at = self._orig_call_at  # type: ignore[method-assign]
            self._loop.call_soon_threadsafe = self._orig_call_soon_threadsafe  # type: ignore[method-assign]
            self._loop.run_in_executor = self._orig_run_in_executor  # type: ignore[method-assign]
            self._orig_create_task = None
            self._orig_call_soon = None
            self._orig_call_later = None
            self._orig_call_at = None
            self._orig_call_soon_threadsafe = None
            self._orig_run_in_executor = None
        if self._orig_view_time is not None:
            _dpy_internals.swap_view_time(self._orig_view_time)
            self._orig_view_time = None
        if self._orig_monotonic is not None:
            time.monotonic = self._orig_monotonic
            self._orig_monotonic = None
        self._task_records.clear()
        self._external_waits.clear()

    async def external_wait(self, awaitable: Any, *, reason: str) -> Any:
        """Await one explicitly declared external input without blocking settlement."""
        task = _current_task()
        record = self._task_records.get(task) if task is not None else None
        if not isinstance(reason, str) or not reason.strip():
            message = "external_wait reason must be a non-empty string"
        elif record is None:
            message = "external_wait must run inside bot-owned work"
        elif task in self._external_waits:
            message = "external_wait declarations cannot be nested"
        else:
            message = None
        if message is not None:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise SetupError(message)
        assert task is not None
        self._external_waits[task] = reason.strip()
        try:
            return await awaitable
        finally:
            self._external_waits.pop(task, None)

    async def settle(
        self,
        timeout: float | None = None,  # noqa: ASYNC109
        idle: float = 0.05,
    ) -> None:
        """Join all runnable bot-owned work, leaving only recognized waits."""
        token = self._begin_operation("settle")
        try:
            await self._settle_internal(timeout=timeout, idle=idle)
        finally:
            self._end_operation(token)

    async def _settle_internal(
        self,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
        idle: float = 0.05,
        dispatch: str | None = None,
    ) -> None:
        effective = self.settle_timeout if timeout is None else self._validate_timeout(timeout, "timeout")
        interval = self._validate_idle(idle)
        assert self._loop is not None
        self._last_dispatch = dispatch
        deadline = self._loop.time() + effective
        stable_empty = 0
        while True:
            self._run_due_virtual_callbacks(deadline)
            await asyncio.sleep(0)
            pending = [task for task in self._owned_tasks() if not task.done()]
            callbacks = self._active_callbacks(deadline)
            if not pending and not callbacks:
                stable_empty += 1
                if stable_empty >= 2:
                    return
                continue
            stable_empty = 0
            parked = [task for task in pending if self._is_parked(task, deadline)]
            active_callbacks = [
                record for record in callbacks if not self._callback_is_parked(record, deadline)
            ]
            if not active_callbacks and len(parked) == len(pending):
                # Recheck after another loop turn so a resumed continuation or
                # callback scheduled by a just-finished task cannot escape.
                await asyncio.sleep(0)
                again = [task for task in self._owned_tasks() if not task.done()]
                again_callbacks = [
                    record
                    for record in self._active_callbacks(deadline)
                    if not self._callback_is_parked(record, deadline)
                ]
                if not again_callbacks and all(self._is_parked(task, deadline) for task in again):
                    return
                continue
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                stuck = [task for task in pending if task not in parked]
                raise TimeoutError(self._settle_timeout_message(stuck, pending, effective, active_callbacks))
            wait_for = min(interval, remaining)
            if pending:
                await asyncio.wait(pending, timeout=wait_for, return_when=asyncio.FIRST_COMPLETED)
            else:
                await asyncio.sleep(wait_for)
            # A busy task can make progress forever. The deadline is absolute,
            # so progress never extends it.
            if self._loop.time() >= deadline:
                pending = [task for task in self._owned_tasks() if not task.done()]
                parked = [task for task in pending if self._is_parked(task, deadline)]
                active_callbacks = [
                    record
                    for record in self._active_callbacks(deadline)
                    if not self._callback_is_parked(record, deadline)
                ]
                if active_callbacks or len(parked) != len(pending):
                    raise TimeoutError(
                        self._settle_timeout_message(
                            [task for task in pending if task not in parked],
                            pending,
                            effective,
                            active_callbacks,
                        )
                    )

    def _run_due_virtual_callbacks(self, deadline: float) -> None:
        # Bounded per settle turn: a callback rescheduling itself with
        # call_later(0) must yield to the loop and the settlement deadline.
        assert self._loop is not None
        ran = 0
        while True:
            due = next(
                (
                    record
                    for record in self._callbacks
                    if record.when is not None
                    and record.when <= self._virtual_time
                    and record.handle is not None
                    and not record.handle.cancelled()
                ),
                None,
            )
            if due is None:
                return
            assert due.handle is not None
            try:
                due.handle._run()
            except Exception:
                # The real loop routes callback failures to its exception
                # handler; advancing virtual timers must keep that behavior.
                pass
            ran += 1
            if ran >= _DUE_CALLBACK_BATCH or self._loop.time() >= deadline:
                return

    def _callback_fire_time(self, record: _CallbackRecord) -> float:
        """Real-clock time at which the callback fires without advance_time().

        ``-inf`` for already-due/immediate callbacks, ``+inf`` when only
        ``advance_time()`` can still reach it (no live real fallback left).
        """
        if record.when is None or record.when <= self._virtual_time:
            return -math.inf
        real = record.real_handle
        if real is None or real.cancelled():
            return math.inf
        return real.when()

    def _is_virtual_sleep_waiter(self, waiter: Any, deadline: float) -> bool:
        if waiter is None or waiter.done():
            return False
        for record in self._callbacks:
            handle = record.handle
            if record.when is None or handle is None or handle.cancelled():
                continue
            if self._callback_fire_time(record) <= deadline:
                continue
            callback = getattr(handle, "_callback", None)
            callback = getattr(callback, "__simcord_original_callback__", callback)
            if getattr(callback, "__name__", "") == "_set_result_unless_cancelled" and any(
                arg is waiter for arg in getattr(handle, "_args", ())
            ):
                return True
        return False

    def _owned_tasks(self) -> list[asyncio.Task[Any]]:
        return [task for task in self._task_records if not task.done()]

    def _active_callbacks(self, _deadline: float) -> list[_CallbackRecord]:
        live = [
            record
            for record in self._callbacks
            if record.handle is not None and not record.handle.cancelled()
        ]
        self._callbacks = live
        return live

    def _callback_is_parked(self, record: _CallbackRecord, deadline: float) -> bool:
        return record.when is not None and self._callback_fire_time(record) > deadline

    def _is_parked(self, task: asyncio.Task[Any], deadline: float) -> bool:
        return self._park_reason(task, deadline) is not None

    def _park_reason(
        self, task: asyncio.Task[Any], deadline: float, seen: set[int] | None = None
    ) -> str | None:
        if task.done():
            return "completed"
        seen = set() if seen is None else seen
        if id(task) in seen:
            return None
        seen.add(id(task))
        waiter = getattr(task, "_fut_waiter", None)
        if waiter is None or waiter.done():
            return None
        reason = self._external_waits.get(task)
        if reason:
            return reason
        if _dpy_internals.is_listener_future(self.bot, waiter) or _dpy_internals.is_wait_for_listener(
            self.bot, task
        ):
            return "discord Client.wait_for listener"
        if _dpy_internals.is_view_wait_future(self.bot, waiter):
            return "discord View/Modal completion"
        dependencies = _dpy_internals.composed_tasks(task, waiter)
        unresolved = [dependency for dependency in dependencies if not dependency.done()]
        if unresolved:
            reasons: list[str] = []
            for dependency in unresolved:
                if isinstance(dependency, asyncio.Task):
                    if dependency not in self._task_records:
                        break
                    reason = self._park_reason(dependency, deadline, seen)
                elif _dpy_internals.is_listener_future(self.bot, dependency):
                    reason = "discord Client.wait_for listener"
                elif _dpy_internals.is_view_wait_future(self.bot, dependency):
                    reason = "discord View/Modal completion"
                else:
                    break
                if reason is None:
                    break
                reasons.append(reason)
            else:
                if reasons and all(reason is not None for reason in reasons):
                    return "composed external wait"
        if self._is_virtual_sleep_waiter(waiter, deadline) or _dpy_internals.is_sleep_waiter(
            waiter, self._loop, deadline
        ):
            return "sleep timer beyond settlement deadline"

    def _settle_timeout_message(
        self,
        stuck: list[asyncio.Task[Any]],
        all_pending: list[asyncio.Task[Any]],
        effective: float,
        callbacks: list[_CallbackRecord],
    ) -> str:
        lines = ["bot did not settle"]
        if self._last_dispatch:
            lines[0] += f" after {self._last_dispatch}"
        lines[0] += (
            f" (timeout={effective:g}s); state may already have changed and outstanding work remains tracked"
        )
        for task in stuck:
            record = self._task_records.get(task)
            label = record.label if record is not None else _dpy_internals.task_label(task.get_coro())
            waiter = getattr(task, "_fut_waiter", None)
            reason = self._external_waits.get(task)
            if reason is None:
                if _dpy_internals.is_listener_future(self.bot, waiter) or _dpy_internals.is_wait_for_listener(
                    self.bot, task
                ):
                    reason = "Client.wait_for listener"
                elif _dpy_internals.is_view_wait_future(self.bot, waiter):
                    reason = "View/Modal completion"
                elif waiter is None:
                    reason = "runnable continuation"
                else:
                    reason = f"unknown wait ({type(waiter).__name__})"
            generation = record.generation if record is not None else self._generation
            lines.append(f"  bot-owned generation {generation} {label}: {reason}")
        if callbacks:
            lines.append("  bot-owned callbacks pending:")
            for record in callbacks:
                lines.append(f"    {record.label}")
        lines.append(
            "  use await env.external_wait(...) for intentional external input; "
            "use await env.advance_time(...) for virtual timers"
        )
        if len(all_pending) > len(stuck):
            lines.append(f"  {len(all_pending) - len(stuck)} recognized waits remain parked")
        return "\n".join(lines)

    def _next_virtual_timer(self) -> float | None:
        times = [
            record.when
            for record in self._callbacks
            if record.when is not None and record.handle is not None and not record.handle.cancelled()
        ]
        return min(times) if times else None

    async def advance_time(self, seconds: float) -> None:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
            raise SetupError("seconds must be a finite non-negative number")

        if seconds < 0:
            raise SetupError("seconds must be a finite non-negative number")
        token = self._begin_operation("advance_time")
        try:
            assert self._loop is not None
            amount = float(seconds)
            await self._settle_internal()
            target = self._virtual_time + amount
            self.backend.advance_clock(amount)
            self.backend.expire_due_polls()
            self.backend.activate_due_scheduled_events()
            while (next_timer := self._next_virtual_timer()) is not None and next_timer <= target:
                # max(): a real fallback firing during settle may already have
                # pushed the clock past this timer — virtual time never regresses.
                self._virtual_time = max(self._virtual_time, next_timer)
                await self._settle_internal()
            self._virtual_time = max(self._virtual_time, target)
            await self._settle_internal()
        finally:
            self._end_operation(token)

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
        """Errors the bot raised; reading marks them inspected."""
        self._errors_inspected = True
        return self._errors

    def _capture_errors(self) -> None:
        from discord.ext import commands

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
        channel: Any,
        *,
        viewers: Any,
        theme: str = "dark",
        width: int = 960,
        height: int = 720,
        locale: str = "en-US",
        timezone: str = "UTC",
        assets: Mapping[str, tuple[str, bytes]] | None = None,
    ) -> Any:
        """Create one eagerly validated local preview context manager."""
        if not self._started:
            raise SetupError("Env is not running")
        if self._preview is not None and not getattr(self._preview, "_closed", False):
            raise SetupError("Only one active Preview is allowed per Env")
        token = self._begin_operation("preview")
        try:
            from .preview import make_preview

            return make_preview(
                self,
                channel,
                viewers=viewers,
                theme=theme,
                width=width,
                height=height,
                locale=locale,
                timezone=timezone,
                assets=assets,
            )
        finally:
            self._end_operation(token)

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
    ) -> UserHandle:
        """Create a virtual user.

        ``bot=True`` makes messages this user posts arrive with
        ``message.author.bot`` set — the way a bot/application account, or a
        webhook (see :meth:`GuildHandle.create_webhook`), appears to the bot
        under test. ``system=True`` marks an official Discord system account.
        ``global_name`` is the display name (distinct from the unique
        ``name``/username); ``discriminator`` is the legacy four-digit tag
        (``"0"`` for migrated accounts); ``public_flags`` carries badge flags
        such as ``verified_bot``.
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
            ),
        )

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
            if id is None:
                id = self.backend.snowflake_for_shard(shard_id, self._shard_count)
            elif (id >> 22) % self._shard_count != shard_id:
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
                name, id=id, owner_id=owner.id if owner is not None else None, **settings
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


def _guard_env_sync(method: Any) -> Any:
    @wraps(method)
    def guarded(self: Env, *args: Any, **kwargs: Any) -> Any:
        token = self._begin_operation(method.__name__)
        try:
            return method(self, *args, **kwargs)
        finally:
            self._end_operation(token)

    return guarded


Env.create_user = _guard_env_sync(Env.create_user)  # type: ignore[method-assign]
Env.create_guild = _guard_env_sync(Env.create_guild)  # type: ignore[method-assign]
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
