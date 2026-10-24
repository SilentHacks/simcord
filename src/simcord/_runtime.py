"""Bot loop hooks and callback ownership for the Env lifecycle."""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import weakref
from _thread import LockType
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal, cast

import discord

from . import _dpy_internals
from . import intents as _intents
from .backend import serializers
from .backend.errors import SetupError
from .gateway import ShardRouter
from .http import FakeHTTPClient, FakeWebhookAdapter

if TYPE_CHECKING:
    from .env import Env


_BOT_SCOPE: contextvars.ContextVar[tuple[Any, int] | None] = contextvars.ContextVar(
    "simcord_bot_scope", default=None
)
_CLOCK_SCOPE: contextvars.ContextVar[Any] = contextvars.ContextVar("simcord_clock_scope", default=None)
_LIVE_LOOPS: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()


def _selected_env(*, driver: bool = True) -> Any:
    scope = _BOT_SCOPE.get()
    if scope is not None:
        env, generation = scope
        if env._started and env._runtime is not None and generation == env._generation:
            return env
        return None
    env = _CLOCK_SCOPE.get() if driver else None
    return env if env is not None and env._started and env._runtime is not None else None


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


@dataclass(slots=True)
class _ExecutorRecord:
    completed: asyncio.Future[None]
    generation: int
    label: str
    owner: asyncio.Task[Any] | None
    caller: asyncio.Future[Any] | None = None
    state: Literal["pending", "running", "completed", "abandoned"] = "pending"
    lock: LockType = field(default_factory=Lock)
    error: BaseException | None = None
    submission_failed: bool = False


@dataclass(slots=True)
class _WorkState:
    """Generation-spanning work ownership; Env retains lifecycle and world state."""

    tasks: dict[asyncio.Task[Any], _TaskRecord] = field(default_factory=dict)
    callbacks: list[_CallbackRecord] = field(default_factory=list)
    executors: dict[asyncio.Future[None], _ExecutorRecord] = field(default_factory=dict)
    external_waits: dict[asyncio.Task[Any], str] = field(default_factory=dict)

    def inspect_task(
        self,
        completed: asyncio.Task[Any],
        record: _TaskRecord | None,
        loop: asyncio.AbstractEventLoop,
        record_error: Callable[[BaseException], None],
        *,
        force: bool = False,
    ) -> None:
        if record is None or self.tasks.get(completed) is not record or not completed.done():
            return
        if completed.cancelled():
            self.tasks.pop(completed, None)
            return
        error = getattr(completed, "_exception", None)
        if error is None or not getattr(completed, "_log_traceback", False):
            self.tasks.pop(completed, None)
            return
        owner = record.owner() if record.owner is not None else None
        if not force and owner is not None and owner is not completed and not owner.done():
            if not record.inspection_waiting:
                record.inspection_waiting = True

                def retry(_owner: asyncio.Task[Any]) -> None:
                    loop.call_soon(self.inspect_task, completed, record, loop, record_error)

                owner.add_done_callback(retry)
            return
        record_error(error)
        completed.exception()
        self.tasks.pop(completed, None)

    def track_task(
        self,
        task: asyncio.Task[Any],
        generation: int,
        label: str,
        loop: asyncio.AbstractEventLoop,
        record_error: Callable[[BaseException], None],
    ) -> None:
        owner = _current_task()
        self.tasks[task] = _TaskRecord(generation, label, weakref.ref(owner) if owner is not None else None)

        def done(completed: asyncio.Task[Any]) -> None:
            loop.call_soon(self.inspect_task, completed, self.tasks.get(completed), loop, record_error)

        task.add_done_callback(done)


@dataclass(slots=True)
class _Runtime:
    create_task: Any
    call_soon: Any
    call_later: Any
    call_at: Any
    call_soon_threadsafe: Any
    run_in_executor: Any


def _restore_loop(self: Env) -> None:
    runtime = self._runtime
    if runtime is None:
        return
    assert self._loop is not None
    self._loop.create_task = runtime.create_task  # type: ignore[method-assign]
    self._loop.call_soon = runtime.call_soon  # type: ignore[method-assign]
    self._loop.call_later = runtime.call_later  # type: ignore[method-assign]
    self._loop.call_at = runtime.call_at  # type: ignore[method-assign]
    self._loop.call_soon_threadsafe = runtime.call_soon_threadsafe  # type: ignore[method-assign]
    self._loop.run_in_executor = runtime.run_in_executor  # type: ignore[method-assign]
    self._runtime = None


def _inspect_executor(record_error: Callable[[BaseException], None], record: _ExecutorRecord) -> None:
    caller = record.caller
    owner = record.owner
    if caller is not None and not caller.done():
        caller.add_done_callback(lambda _: _inspect_executor(record_error, record))
        return
    if record.submission_failed or (caller is not None and caller.cancelled()):
        if record.error is not None:
            record_error(record.error)
        return
    if owner is not None and not owner.done():
        record.owner = None
        owner.add_done_callback(lambda _: _inspect_executor(record_error, record))
        return
    if caller is not None and getattr(caller, "_log_traceback", False):
        error = caller.exception()
        if error is not None:
            record_error(error)


def _current_task() -> asyncio.Task[Any] | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _track_callback(
    work: _WorkState,
    original: Any,
    callback: Any,
    args: tuple[Any, ...],
    *,
    identity: tuple[Any, int],
    loop: asyncio.AbstractEventLoop,
    context_for_scope: Callable[[contextvars.Context, tuple[Any, int] | None], contextvars.Context],
    record_error: Callable[[BaseException], None],
    schedule_timer: Callable[[_CallbackRecord, Any, tuple[Any, ...], contextvars.Context | None], None],
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
        owner_record = work.tasks.get(bound_task)
        scope = (
            (identity[0], owner_record.generation)
            if owner_record is not None
            else context.get(_BOT_SCOPE)
            if context is not None
            else None
        )
        schedule_context = context
    else:
        scope = _BOT_SCOPE.get()
        if scope is None and context is not None:
            # Future callbacks carry the registration context even when an
            # unowned native state-transfer callback schedules their delivery.
            scope = context.get(_BOT_SCOPE)
        schedule_context = context_for_scope(context, scope) if context is not None else context
    if scope is None or scope != identity:
        return original(*schedule_args, callback, *args, context=schedule_context)
    label = getattr(callback, "__qualname__", None) or getattr(callback, "__name__", type(callback).__name__)
    record = _CallbackRecord(None, when, label)
    called = False

    def invoke(*call_args: Any) -> None:
        nonlocal called
        called = True
        if record.real_handle is not None:
            record.real_handle.cancel()
        if record.handle is not None:
            try:
                work.callbacks.remove(record)
            except ValueError:
                pass
        try:
            callback(*call_args)
        except BaseException as error:
            record_error(error)
            raise

    invoke.__simcord_original_callback__ = callback
    if when is None:
        handle = original(*schedule_args, invoke, *args, context=schedule_context)
    else:
        handle = asyncio.TimerHandle(when, invoke, args, loop, context=schedule_context)
        schedule_timer(record, callback, args, schedule_context)

    record.handle = handle
    if not called:
        work.callbacks.append(record)
    return handle


async def _attach_bot(self: Env, bot: discord.Client) -> None:
    """Install fakes, start a new bot generation, and bring it to READY."""
    self.bot = bot
    loop = self._loop
    assert loop is not None
    _dpy_internals.verify_loop(loop)
    self._generation += 1
    self._shard_count, self._shard_ids = self._resolve_shards(bot)

    runtime = self._runtime = _Runtime(
        loop.create_task,
        loop.call_soon,
        loop.call_later,
        loop.call_at,
        loop.call_soon_threadsafe,
        loop.run_in_executor,
    )
    orig_create_task = runtime.create_task
    orig_call_soon = runtime.call_soon
    orig_call_later = runtime.call_later
    orig_call_at = runtime.call_at
    orig_call_soon_threadsafe = runtime.call_soon_threadsafe
    orig_run_in_executor = runtime.run_in_executor
    track_callback = partial(
        _track_callback,
        self._work,
        identity=(self, self._generation),
        loop=loop,
        context_for_scope=self._context_for_scope,
        record_error=self._record_error,
        schedule_timer=self._schedule_timer,
    )

    def tracking_create_task(coro: Any, **kwargs: Any) -> asyncio.Task[Any]:
        scope = _BOT_SCOPE.get()
        context = kwargs.get("context")
        if context is not None:
            kwargs["context"] = self._context_for_scope(context, scope)
        label = (
            _dpy_internals.task_label(coro)
            if scope is not None and scope == (self, self._generation)
            else None
        )
        task = orig_create_task(coro, **kwargs)
        if not isinstance(task, asyncio.Task):
            raise SetupError("simcord requires the loop task factory to return asyncio.Task objects")
        if label is not None:
            assert scope is not None
            self._work.track_task(task, scope[1], label, loop, self._record_error)
        return task

    def call_soon(callback: Any, *args: Any, context: contextvars.Context | None = None) -> asyncio.Handle:
        return track_callback(orig_call_soon, callback, args, context=context, when=None)

    def call_later(
        delay: float, callback: Any, *args: Any, context: contextvars.Context | None = None
    ) -> asyncio.TimerHandle:
        return cast(
            asyncio.TimerHandle,
            track_callback(
                orig_call_later,
                callback,
                args,
                context=context,
                # A zero-delay timer runs on the next representable UTC tick.
                # Frozen same-instant wakes otherwise trap SDK clock-drift loops.
                when=self._virtual_time + max(delay, 0.000001),
                schedule_args=(delay,),
            ),
        )

    def call_at(
        when: float, callback: Any, *args: Any, context: contextvars.Context | None = None
    ) -> asyncio.TimerHandle:
        return cast(
            asyncio.TimerHandle,
            track_callback(
                orig_call_at,
                callback,
                args,
                context=context,
                when=self._virtual_time + max(when - loop.time(), 0.000001),
                schedule_args=(when,),
            ),
        )

    def call_soon_threadsafe(
        callback: Any, *args: Any, context: contextvars.Context | None = None
    ) -> asyncio.Handle:
        return track_callback(orig_call_soon_threadsafe, callback, args, context=context, when=None)

    def run_in_executor(executor: Any, func: Any, *args: Any) -> Any:
        scope = _BOT_SCOPE.get()
        if scope is None or scope != (self, self._generation):
            return orig_run_in_executor(executor, func, *args)
        # Validate the submitted callable, not the synchronous ownership wrapper.
        # Executor selection and submission remain entirely the saved hook's job.
        if loop.is_closed():
            raise RuntimeError("Event loop is closed")
        if loop.get_debug():
            if asyncio.iscoroutine(func) or inspect.iscoroutinefunction(func):
                raise TypeError("coroutines cannot be used with run_in_executor()")
            if not callable(func):
                raise TypeError(f"a callable object was expected by run_in_executor(), got {func!r}")
        completed = loop.create_future()
        record = _ExecutorRecord(
            completed, scope[1], getattr(func, "__qualname__", type(func).__name__), _current_task()
        )
        self._work.executors[completed] = record

        def finish() -> None:
            if completed.done():
                return
            with record.lock:
                terminal = record.state == "abandoned" or (
                    record.state == "completed"
                    and (record.submission_failed or (record.caller is not None and record.caller.done()))
                )
            if terminal:
                self._work.executors.pop(completed, None)
                completed.set_result(None)
                # Caller policy and the awaiting owner get first refusal.
                orig_call_soon(_inspect_executor, self._record_error, record, context=contextvars.Context())

        def caller_done(caller: asyncio.Future[Any]) -> None:
            with record.lock:
                if caller.cancelled() and record.state == "pending":
                    record.state = "abandoned"
            finish()

        def owned_call(*call_args: Any) -> Any:
            with record.lock:
                if record.state == "abandoned":
                    return None
                if record.caller is not None and record.caller.cancelled():
                    record.state = "abandoned"
                    orig_call_soon_threadsafe(finish, context=contextvars.Context())
                    return None
                record.state = "running"
            token = _BOT_SCOPE.set(scope)
            try:
                return func(*call_args)
            except BaseException as error:
                record.error = error
                raise
            finally:
                _BOT_SCOPE.reset(token)
                with record.lock:
                    record.state = "completed"
                orig_call_soon_threadsafe(finish, context=contextvars.Context())

        try:
            caller = orig_run_in_executor(executor, owned_call, *args)
        except BaseException:
            with record.lock:
                record.submission_failed = True
                if record.state == "pending":
                    record.state = "abandoned"
            finish()
            raise
        record.caller = caller
        caller.add_done_callback(caller_done, context=contextvars.Context())
        return caller

    loop.create_task = tracking_create_task  # type: ignore[method-assign]
    loop.call_soon = call_soon  # type: ignore[method-assign]
    loop.call_later = call_later  # type: ignore[method-assign]
    loop.call_at = call_at  # type: ignore[method-assign]
    loop.call_soon_threadsafe = call_soon_threadsafe  # type: ignore[method-assign]
    loop.run_in_executor = run_in_executor  # type: ignore[method-assign]
    _dpy_internals.acquire_clocks(self)

    message_content_access = self.approved_intents is None or self.approved_intents.message_content
    _dpy_internals.install_http(
        bot, FakeHTTPClient(self.backend, loop, message_content_access=message_content_access)
    )
    _dpy_internals.set_guild_ready_timeout(bot, 0.0)
    self._webhook_adapter = FakeWebhookAdapter(self.backend, message_content_access=message_content_access)
    self._adapter_token = _dpy_internals.set_webhook_adapter(self._webhook_adapter)

    state = _dpy_internals.get_state(bot)
    self._message_states.add(state)
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
            for guild in list(self.backend.guilds.values()):
                self.backend.emit("GUILD_CREATE", serializers.guild_create_payload(self.backend, guild))
        await self._settle_internal(timeout=5.0)
    except BaseException:
        await self._detach_bot()
        raise
