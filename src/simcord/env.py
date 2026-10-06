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
from datetime import datetime
from functools import wraps
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import discord

from . import _dpy_internals
from ._runtime import _BOT_SCOPE, _attach_bot, _current_task, _TaskRecord
from ._settlement import (
    _active_callbacks,
    _callback_is_parked,
    _is_parked,
    _next_virtual_timer,
    _owned_tasks,
    _run_due_virtual_callbacks,
    _settle_timeout_message,
    _virtualize_recognized_waits,
)
from .backend import Backend, serializers
from .backend.errors import SetupError
from .builders import GuildHandle, UserHandle
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
        self._preview: Preview | None = None
        self._adapter_token: Any = None
        self._gateway_feed: Any = None
        self._shard_count = 1
        self._shard_ids: tuple[int, ...] = (0,)
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

    def _begin_operation(self, label: str) -> None:
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
            self._started = True
            self._loop = asyncio.get_running_loop()
            await _attach_bot(self, self.bot)
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
            for handle in self._guilds:
                guild = self.backend.get_guild(handle.id)
                with self._bot_scope():
                    self.backend.emit("GUILD_CREATE", serializers.guild_create_payload(self.backend, guild))
            await self._settle_internal(timeout=5.0)
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

    async def shutdown(self) -> None:
        await self._run_pre_shutdown()
        self._begin_operation("shutdown")
        try:
            await self._detach_bot()
            self._started = False
        finally:
            self._end_operation()

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
        """Await one explicitly declared external input without blocking settlement.

        Wake-up timers scheduled by the declaring task while inside the wait
        are virtual: they resume only through :meth:`advance_time` or the
        awaited input itself, never the wall clock. Timers owned by other
        already-running tasks keep their normal real-time behavior.
        """
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
    ) -> None:
        effective = self.settle_timeout if timeout is None else self._validate_timeout(timeout, "timeout")
        interval = self._validate_idle(idle)
        assert self._loop is not None
        self._last_dispatch = dispatch
        deadline = self._loop.time() + effective
        stable_empty = 0
        while True:
            _run_due_virtual_callbacks(self, deadline)
            await asyncio.sleep(0)
            pending = [
                task
                for task in _owned_tasks(
                    self,
                )
                if not task.done()
            ]
            callbacks = _active_callbacks(
                self,
            )
            if not pending and not callbacks:
                stable_empty += 1
                if stable_empty >= 2:
                    return
                continue
            stable_empty = 0
            _virtualize_recognized_waits(self, pending)
            parked = [task for task in pending if _is_parked(self, task, deadline)]
            active_callbacks = [
                record for record in callbacks if not _callback_is_parked(self, record, deadline)
            ]
            if not active_callbacks and len(parked) == len(pending):
                # Recheck after another loop turn so a resumed continuation or
                # callback scheduled by a just-finished task cannot escape.
                await asyncio.sleep(0)
                again = [
                    task
                    for task in _owned_tasks(
                        self,
                    )
                    if not task.done()
                ]
                _virtualize_recognized_waits(self, again)
                again_callbacks = [
                    record
                    for record in _active_callbacks(
                        self,
                    )
                    if not _callback_is_parked(self, record, deadline)
                ]
                if not again_callbacks and all(_is_parked(self, task, deadline) for task in again):
                    return
                continue
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                stuck = [task for task in pending if task not in parked]
                raise TimeoutError(_settle_timeout_message(self, stuck, pending, effective, active_callbacks))
            wait_for = min(interval, remaining)
            if pending:
                await asyncio.wait(pending, timeout=wait_for, return_when=asyncio.FIRST_COMPLETED)
            else:
                await asyncio.sleep(wait_for)
            # A busy task can make progress forever. The deadline is absolute,
            # so progress never extends it.
            if self._loop.time() >= deadline:
                pending = [
                    task
                    for task in _owned_tasks(
                        self,
                    )
                    if not task.done()
                ]
                _virtualize_recognized_waits(self, pending)
                parked = [task for task in pending if _is_parked(self, task, deadline)]
                active_callbacks = [
                    record
                    for record in _active_callbacks(
                        self,
                    )
                    if not _callback_is_parked(self, record, deadline)
                ]
                if active_callbacks or len(parked) != len(pending):
                    raise TimeoutError(
                        _settle_timeout_message(
                            self,
                            [task for task in pending if task not in parked],
                            pending,
                            effective,
                            active_callbacks,
                        )
                    )

    async def advance_time(self, seconds: float) -> None:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
            raise SetupError("seconds must be a finite non-negative number")

        if seconds < 0:
            raise SetupError("seconds must be a finite non-negative number")
        self._begin_operation("advance_time")
        try:
            assert self._loop is not None
            amount = float(seconds)
            await self._settle_internal()
            target = self._virtual_time + amount
            self.backend.advance_clock(amount)
            self.backend.expire_due_polls()
            self.backend.activate_due_scheduled_events()
            while (
                next_timer := _next_virtual_timer(
                    self,
                )
            ) is not None and next_timer <= target:
                # max(): a real fallback firing during settle may already have
                # pushed the clock past this timer — virtual time never regresses.
                self._virtual_time = max(self._virtual_time, next_timer)
                await self._settle_internal()
            self._virtual_time = max(self._virtual_time, target)
            await self._settle_internal()
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
        return self._env

    async def __aexit__(self, exc_type: Any, *exc_info: Any) -> None:
        env = self._env
        await env.shutdown()
        # Don't mask an exception already propagating out of the test body.
        if exc_type is None and env.check_errors:
            env._raise_errors_since(env._errors_acknowledged)
