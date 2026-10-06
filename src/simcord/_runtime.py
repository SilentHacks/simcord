"""Bot loop hooks and callback ownership for the Env lifecycle."""

from __future__ import annotations

import asyncio
import contextvars
import time
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import discord

from . import _dpy_internals
from . import intents as _intents
from .backend.errors import SetupError
from .gateway import ShardRouter
from .http import FakeHTTPClient, FakeWebhookAdapter

if TYPE_CHECKING:
    from .env import Env


_BOT_SCOPE: contextvars.ContextVar[tuple[Any, int] | None] = contextvars.ContextVar(
    "simcord_bot_scope", default=None
)


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


def _track_callback(
    self: Env,
    original: Any,
    callback: Any,
    args: tuple[Any, ...],
    *,
    context: contextvars.Context | None,
    when: float | None,
    schedule_args: tuple[Any, ...] = (),
    real_call_at: Any = None,
    real_monotonic: Any = None,
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
                if self._wakes_recognized_wait(record):
                    # The task suspended on a recognized wait after this
                    # timer was scheduled: the wake-up now belongs to the
                    # virtual clock alone.
                    record.real_handle = None
                    return
                # The real clock reached this timer: sync the virtual clock
                # so code observing monotonic() (e.g. View timeouts) sees
                # the elapsed delay too.
                if record.when is not None and record.when > self._virtual_time:
                    self._virtual_time = record.when
                record.handle._run()

        assert real_call_at is not None and real_monotonic is not None
        remaining = max(when - self._virtual_time, 0.0)
        current = _current_task()
        virtual_birth = current is not None and (
            (current in self._external_waits and _dpy_internals.is_wakeup_shaped(callback))
            or _dpy_internals.is_intentional_wait_wakeup(self.bot, callback, args, current)
        )
        if not virtual_birth:
            # Wake-up timers behind a recognized wait are virtual from
            # birth — only advance_time() may ever fire them.
            record.real_handle = real_call_at(
                real_monotonic() + remaining, run_real, context=schedule_context
            )
    record.handle = handle
    if not called:
        self._callbacks.append(record)
    return handle


async def _attach_bot(self: Env, bot: discord.Client) -> None:
    """Install fakes, start a new bot generation, and bring it to READY."""
    self.bot = bot
    loop = self._loop
    assert loop is not None
    _dpy_internals.verify_loop(loop)
    self._generation += 1
    self._shard_count, self._shard_ids = self._resolve_shards(bot)

    # Locals as well as attributes: patched callables must keep working if
    # an out-of-order detach restores one of them after _orig_* is cleared.
    orig_create_task = self._orig_create_task = loop.create_task
    orig_call_soon = self._orig_call_soon = loop.call_soon
    orig_call_later = self._orig_call_later = loop.call_later
    orig_call_at = self._orig_call_at = loop.call_at
    orig_call_soon_threadsafe = self._orig_call_soon_threadsafe = loop.call_soon_threadsafe
    orig_run_in_executor = self._orig_run_in_executor = loop.run_in_executor
    orig_monotonic = self._orig_monotonic = time.monotonic

    def tracking_create_task(coro: Any, **kwargs: Any) -> asyncio.Task[Any]:
        scope = _BOT_SCOPE.get()
        context = kwargs.get("context")
        if context is not None:
            kwargs["context"] = self._context_for_scope(context, scope)
        label = _dpy_internals.task_label(coro) if scope is not None and scope[0] is self else None
        task = orig_create_task(coro, **kwargs)
        if not isinstance(task, asyncio.Task):
            raise SetupError("simcord requires the loop task factory to return asyncio.Task objects")
        if label is not None:
            assert scope is not None
            self._track_task(task, scope[1], label)
        return task

    def call_soon(callback: Any, *args: Any, context: contextvars.Context | None = None) -> asyncio.Handle:
        return _track_callback(self, orig_call_soon, callback, args, context=context, when=None)

    def call_later(
        delay: float, callback: Any, *args: Any, context: contextvars.Context | None = None
    ) -> asyncio.TimerHandle:
        return cast(
            asyncio.TimerHandle,
            _track_callback(
                self,
                orig_call_later,
                callback,
                args,
                context=context,
                when=self._virtual_time + max(delay, 0.0),
                schedule_args=(delay,),
                real_call_at=orig_call_at,
                real_monotonic=orig_monotonic,
            ),
        )

    def call_at(
        when: float, callback: Any, *args: Any, context: contextvars.Context | None = None
    ) -> asyncio.TimerHandle:
        return cast(
            asyncio.TimerHandle,
            _track_callback(
                self,
                orig_call_at,
                callback,
                args,
                context=context,
                when=self._virtual_time + max(when - loop.time(), 0.0),
                schedule_args=(when,),
                real_call_at=orig_call_at,
                real_monotonic=orig_monotonic,
            ),
        )

    def call_soon_threadsafe(
        callback: Any, *args: Any, context: contextvars.Context | None = None
    ) -> asyncio.Handle:
        return _track_callback(self, orig_call_soon_threadsafe, callback, args, context=context, when=None)

    def run_in_executor(executor: Any, func: Any, *args: Any) -> Any:
        scope = _BOT_SCOPE.get()
        if scope is None or scope[0] is not self:
            return orig_run_in_executor(executor, func, *args)

        def owned_call(*call_args: Any) -> Any:
            token = _BOT_SCOPE.set(scope)
            try:
                return func(*call_args)
            finally:
                _BOT_SCOPE.reset(token)

        return orig_run_in_executor(executor, owned_call, *args)

    loop.create_task = tracking_create_task  # type: ignore[method-assign]
    loop.call_soon = call_soon  # type: ignore[method-assign]
    loop.call_later = call_later  # type: ignore[method-assign]
    loop.call_at = call_at  # type: ignore[method-assign]
    loop.call_soon_threadsafe = call_soon_threadsafe  # type: ignore[method-assign]
    loop.run_in_executor = run_in_executor  # type: ignore[method-assign]

    if self._virtual_time == 0.0:
        self._virtual_time = loop.time()

    def monotonic() -> float:
        scope = _BOT_SCOPE.get()
        if scope is not None and scope[0] is self:
            return self._virtual_time
        return orig_monotonic()

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
