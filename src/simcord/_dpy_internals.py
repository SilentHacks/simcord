"""Every touch of a private discord.py API, quarantined in one module.

If a discord.py release changes one of these internals, the import-time
self-check below fails with a clear message instead of users' tests breaking
mysteriously. Keep this inventory in sync with what the framework touches.
"""

import asyncio
import asyncio.timeouts
import datetime as _datetime
import inspect
import sys
import threading
import time as _time
from collections.abc import Callable, Collection
from types import ModuleType
from typing import Any

import discord
from discord.ext import tasks as _ext_tasks
from discord.gateway import DiscordWebSocket
from discord.http import HTTPClient
from discord.interactions import _InteractionMessageState
from discord.state import ChunkRequest, ConnectionState
from discord.ui import view as _view
from discord.webhook.async_ import AsyncWebhookAdapter, _WebhookState, async_context

from .backend.errors import SetupError


def listener_futures(client: discord.Client) -> list[Any]:
    """Every future registered by ``Client.wait_for`` (``Client._listeners``)."""
    listeners = getattr(client, "_listeners", None)
    return [fut for entries in (listeners or {}).values() for fut, _ in entries]


def capture_ui_errors(client: discord.Client, record_error: Callable[[BaseException], None]) -> None:
    """Capture UI exceptions before discord.py's default handlers swallow them."""
    store = get_state(client)._view_store
    add_view = _original_callback(store.add_view)

    def register(view: Any, message_id: int | None = None) -> None:
        original = _original_callback(view.on_error)

        async def on_error(interaction: Any, error: BaseException, *args: Any) -> None:
            record_error(error)
            await original(interaction, error, *args)

        on_error.__simcord_original_callback__ = original
        view.on_error = on_error
        add_view(view, message_id)

    register.__simcord_original_callback__ = add_view
    store.add_view = register


def verify() -> None:
    """Sanity-check the discord.py internals this framework relies on."""
    if discord.version_info.major != 2 or (discord.version_info.minor, discord.version_info.micro) < (
        7,
        1,
    ):  # pragma: no cover - guards an unsupported discord.py
        raise ImportError(
            f"simcord requires discord.py 2.7.1+; found {discord.__version__}. "
            "Check https://github.com/SilentHacks/simcord for supported versions."
        )
    problems = []
    for cls, attr in (
        (HTTPClient, "request"),
        (HTTPClient, "static_login"),
        (HTTPClient, "get_from_cdn"),
        (ConnectionState, "parse_ready"),
        (ConnectionState, "parse_message_create"),
        (ConnectionState, "parse_interaction_create"),
        # Intent simulation and member chunking rely on these:
        (ConnectionState, "intents"),
        (ConnectionState, "parse_guild_members_chunk"),
        (discord.Client, "_run_event"),
        (ChunkRequest, "done"),
        (DiscordWebSocket, "request_chunks"),
        (discord.Client, "_get_websocket"),
        # Settlement recognizes (and virtualizes) the timers behind these waits:
        (_view.BaseView, "_dispatch_timeout"),
        (_view.BaseView, "_BaseView__timeout_task_impl"),
        (_ext_tasks.Loop, "_loop"),
        (_ext_tasks.SleepHandle, "_wrapped_set_result"),
        (asyncio.timeouts.Timeout, "_on_timeout"),
    ):
        if not hasattr(cls, attr):  # pragma: no cover - fires only if discord.py drops an internal
            problems.append(f"{cls.__name__}.{attr}")
    for name in (
        "create_interaction_response",
        "get_original_interaction_response",
        "edit_original_interaction_response",
        "delete_original_interaction_response",
        "execute_webhook",
        "get_webhook_message",
        "edit_webhook_message",
        "delete_webhook_message",
        "fetch_webhook",
        "fetch_webhook_with_token",
        "edit_webhook",
        "edit_webhook_with_token",
        "delete_webhook",
        "delete_webhook_with_token",
    ):
        helper = getattr(AsyncWebhookAdapter, name, None)
        if not callable(helper) or not callable(getattr(helper, "__get__", None)):
            problems.append(f"AsyncWebhookAdapter.{name}")
    run_event_code = getattr(discord.Client._run_event, "__code__", None)
    if run_event_code is None or "coro" not in run_event_code.co_varnames:  # pragma: no cover
        problems.append("discord.Client._run_event no longer exposes its handler coroutine")
    client_probe = discord.Client(intents=discord.Intents.none())
    if not hasattr(client_probe, "_listeners"):  # pragma: no cover
        problems.append("discord.Client no longer exposes _listeners")
    if not hasattr(client_probe.http, "_HTTPClient__session"):
        problems.append("HTTPClient.__session")
    store_probe = _view.ViewStore(client_probe._connection)
    for attr in ("_views", "_modals"):
        if not hasattr(store_probe, attr):  # pragma: no cover
            problems.append(f"ViewStore.{attr}")
    if not hasattr(discord.ui.Button(label="x", custom_id="y"), "_view"):  # pragma: no cover
        problems.append("Item._view")
    view_probe = discord.ui.View(timeout=5)
    for attr in ("_BaseView__stopped", "_BaseView__timeout_task"):
        if not hasattr(view_probe, attr):  # pragma: no cover
            problems.append(f"BaseView.{attr}")
    impl_probe = getattr(_view.BaseView, "_BaseView__timeout_task_impl", None)
    if (
        impl_probe is None
        or not inspect.iscoroutinefunction(impl_probe)
        or "self" not in impl_probe.__code__.co_varnames
    ):  # pragma: no cover
        problems.append("BaseView._BaseView__timeout_task_impl coroutine")
    if not hasattr(asyncio.timeouts.Timeout(0.0), "_task"):  # pragma: no cover
        problems.append("Timeout._task")

    async def _probe_loop_body() -> None:
        pass  # pragma: no cover - the probe body is never run, only introspected

    if not hasattr(_ext_tasks.loop(seconds=1)(_probe_loop_body), "_handle"):  # pragma: no cover
        problems.append("Loop._handle")
    if problems:  # pragma: no cover - only when discord.py changed an internal
        raise ImportError(
            "This discord.py version changed internals simcord depends on: "
            + ", ".join(problems)
            + ". Please report this at https://github.com/SilentHacks/simcord/issues."
        )


def verify_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Reject event loops missing capabilities settlement needs."""
    required = ("create_task", "call_soon", "call_later", "call_at", "call_soon_threadsafe", "time")
    missing = [name for name in required if not callable(getattr(loop, name, None))]
    if not hasattr(loop, "_scheduled"):  # pragma: no cover - only on a deficient loop
        missing.append("_scheduled timer heap")
    task = asyncio.current_task()
    for name in ("_exception", "_fut_waiter", "_log_traceback"):
        if task is None or not hasattr(task, name):  # pragma: no cover
            missing.append(f"Task.{name}")
    factory = loop.get_task_factory() if callable(getattr(loop, "get_task_factory", None)) else None
    if factory is not None and not callable(factory):  # pragma: no cover
        missing.append("callable task factory")
    if missing:  # pragma: no cover - only on a deficient loop
        raise SetupError("simcord requires asyncio loop capabilities: " + ", ".join(missing))
    return None


def is_listener_future(client: discord.Client, waiter: Any) -> bool:
    return any(future is waiter and not future.done() for future in listener_futures(client))


def _listener_wait_for_frame(
    client: discord.Client, task: asyncio.Task[Any], listeners: set[Any] | None = None
) -> bool:  # pragma: no cover
    """True when the task's coroutine stack runs ``asyncio.wait_for`` on a listener."""
    wait_for_code = getattr(asyncio.wait_for, "__code__", None)
    if wait_for_code is None:
        return False
    coroutine: Any = task.get_coro()
    seen: set[int] = set()
    while coroutine is not None and id(coroutine) not in seen:
        seen.add(id(coroutine))
        frame = getattr(coroutine, "cr_frame", None)
        if frame is not None and frame.f_code is wait_for_code:
            future = frame.f_locals.get("fut")
            if (
                is_listener_future(client, future)
                if listeners is None
                else future in listeners and not future.done()
            ):
                return True
        coroutine = getattr(coroutine, "cr_await", None)
    return False


def is_wait_for_listener(
    client: discord.Client, task: asyncio.Task[Any], listeners: set[Any] | None = None
) -> bool:  # pragma: no cover
    """Recognize the CPython 3.11 ``wait_for`` wrapper around a listener future."""
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 11):
        return False
    return _listener_wait_for_frame(client, task, listeners)


def _stored_view_facts(client: discord.Client) -> tuple[set[Any], set[Any]]:
    store = getattr(get_state(client), "_view_store", None)
    futures: set[Any] = set()
    tasks: set[Any] = set()
    if store is None:
        return futures, tasks
    views = {
        id(view): view
        for entries in getattr(store, "_views", {}).values()
        for item in entries.values()
        if (view := getattr(item, "_view", None)) is not None
    }
    views.update((id(view), view) for view in getattr(store, "_modals", {}).values())
    for view in views.values():
        stopped = getattr(view, "_BaseView__stopped", None)
        if isinstance(stopped, asyncio.Future):
            futures.add(stopped)
            if stopped.done():
                continue
        timeout_task = getattr(view, "_BaseView__timeout_task", None)
        if timeout_task is not None:
            tasks.add(timeout_task)
    return futures, tasks


def _stored_wait_futures(client: discord.Client) -> set[Any]:
    return _stored_view_facts(client)[0]


def is_view_wait_future(client: discord.Client, waiter: Any) -> bool:
    return waiter in _stored_wait_futures(client) and not waiter.done()


_WAKEUP_CALLBACK_NAMES = frozenset(
    {
        "_set_result_unless_cancelled",  # asyncio.sleep
        "_release_waiter",  # asyncio.wait_for (Python 3.11)
        "_wrapped_set_result",  # discord.ext.tasks SleepHandle
        "_on_timeout",  # asyncio.timeouts.Timeout
    }
)


def is_wakeup_shaped(callback: Any) -> bool:
    """True when a callback has the shape of a timer that resumes a future/task."""
    if getattr(callback, "__name__", "") in _WAKEUP_CALLBACK_NAMES:
        return True
    return isinstance(getattr(callback, "__self__", None), asyncio.Future)


def is_wakeup_callback(callback: Any, args: tuple[Any, ...], waiter: Any, task: asyncio.Task[Any]) -> bool:
    """True when a timer callback exists only to resume this task's current wait."""
    if waiter is not None and (
        any(arg is waiter for arg in args) or getattr(callback, "__self__", None) is waiter
    ):
        return True
    owner = getattr(callback, "__self__", None)
    return (
        getattr(callback, "__name__", "") == "_on_timeout"
        and getattr(type(owner), "__module__", "") == "asyncio.timeouts"
        and getattr(owner, "_task", None) is task
    )


def _view_timeout_task_identity(
    client: discord.Client, task: asyncio.Task[Any], expiry_tasks: set[Any] | None = None
) -> bool:
    """True when task is a registered View/Modal's expiry task."""
    coro = task.get_coro()
    impl = getattr(_view.BaseView, "_BaseView__timeout_task_impl", None)
    if impl is not None and getattr(coro, "cr_code", None) is getattr(impl, "__code__", None):
        owner = getattr(getattr(coro, "cr_frame", None), "f_locals", {}).get("self")
        if isinstance(owner, _view.BaseView):
            return not owner.is_finished()
    return task in (_stored_view_facts(client)[1] if expiry_tasks is None else expiry_tasks)


def view_expiry_task(
    client: discord.Client, task: asyncio.Task[Any], waiter: Any, expiry_tasks: set[Any] | None = None
) -> bool:
    """True while task is a store-registered View/Modal expiry task suspended on its timer."""
    if waiter is None or waiter.done():
        return False
    return _view_timeout_task_identity(client, task, expiry_tasks)


def _tasks_loop_self(task: asyncio.Task[Any]) -> Any:
    coro = task.get_coro()
    frame = getattr(coro, "cr_frame", None)
    return frame.f_locals.get("self") if frame is not None else None


def is_tasks_loop_task(task: asyncio.Task[Any]) -> bool:
    """True when task runs a discord.ext.tasks Loop's ``_loop`` coroutine."""
    return isinstance(_tasks_loop_self(task), _ext_tasks.Loop)


def tasks_loop_sleep(task: asyncio.Task[Any], waiter: Any) -> bool:
    """True while a discord.ext.tasks Loop is suspended on its between-iteration sleep."""
    if waiter is None or waiter.done():
        return False
    self_obj = _tasks_loop_self(task)
    if not isinstance(self_obj, _ext_tasks.Loop):
        return False
    handle = getattr(self_obj, "_handle", None)
    return getattr(handle, "future", None) is waiter


def is_intentional_wait_wakeup(
    client: discord.Client, callback: Any, args: tuple[Any, ...], task: asyncio.Task[Any]
) -> bool:
    """True when a timer scheduled right now exists only to resume a wait on this task.

    Covers the shapes identifiable at schedule time, before the task suspends:
    a registered View/Modal expiry task's sleep, an ext.tasks iteration
    ``SleepHandle``, and a 3.11 ``wait_for`` listener's ``_release_waiter``.
    Timer wake-ups not identifiable here are still caught at settle time and
    gated at fire time by the env.
    """
    name = getattr(callback, "__name__", "")
    if name == "_set_result_unless_cancelled":
        return _view_timeout_task_identity(client, task)
    if name == "_wrapped_set_result":
        return is_tasks_loop_task(task)
    return name == "_release_waiter" and any(is_listener_future(client, arg) for arg in args)


def _original_callback(callback: Any) -> Any:
    while True:
        original = getattr(callback, "__simcord_original_callback__", callback)
        if original is callback:
            return callback
        callback = original


def composed_tasks(task: asyncio.Task[Any], waiter: Any) -> list[Any]:
    """Return every unresolved dependency of a supported asyncio composition."""
    found: list[Any] = []
    if isinstance(waiter, asyncio.Task):
        found.append(waiter)
    if type(waiter).__module__ == "asyncio.tasks" and type(waiter).__name__ == "_GatheringFuture":
        found.extend(child for child in getattr(waiter, "_children", ()) if isinstance(child, asyncio.Future))

    for entry in getattr(waiter, "_callbacks", ()) or ():
        callback = entry[0] if isinstance(entry, tuple) else entry
        if getattr(callback, "__module__", "") == "asyncio.tasks" and getattr(
            callback, "__qualname__", ""
        ).endswith("shield.<locals>._outer_done_callback"):
            for name, cell in zip(callback.__code__.co_freevars, callback.__closure__ or (), strict=True):
                value = cell.cell_contents
                if name == "inner" and isinstance(value, asyncio.Future):
                    found.append(value)

    wait_codes = {
        getattr(asyncio.wait, "__code__", None),
        getattr(getattr(getattr(asyncio, "tasks", None), "_wait", None), "__code__", None),
    }
    group_codes = {
        getattr(asyncio.TaskGroup.__aexit__, "__code__", None),
        getattr(getattr(asyncio.TaskGroup, "_aexit", None), "__code__", None),
    }
    coroutine: Any = task.get_coro()
    seen: set[int] = set()
    while coroutine is not None and id(coroutine) not in seen:
        seen.add(id(coroutine))
        frame = getattr(coroutine, "cr_frame", None)
        code = frame.f_code if frame is not None else None
        if frame is not None and code in wait_codes:
            found.extend(child for child in frame.f_locals.get("fs", ()) if isinstance(child, asyncio.Future))
        elif frame is not None and code in group_codes:
            group = frame.f_locals.get("self")
            found.extend(child for child in getattr(group, "_tasks", ()) if isinstance(child, asyncio.Task))
        coroutine = getattr(coroutine, "cr_await", None)
    return list(dict.fromkeys(found))


def task_label(coro: Any) -> str:
    """Name a task, including discord.py's wrapped event callback when present."""
    wrapper = getattr(coro, "__qualname__", "?")
    frame = getattr(coro, "cr_frame", None)
    callback = frame.f_locals.get("coro") if frame is not None else None
    if wrapper.endswith("Client._run_event") and callable(callback):
        return f"{getattr(callback, '__qualname__', '?')} via {wrapper}"
    return wrapper


_REAL_MONOTONIC = _time.monotonic
_clock_owners: set[Any] = set()
_clock_lock = threading.RLock()
_clock_originals: tuple[Any, Any, Any] | None = None


def real_monotonic() -> float:
    return _REAL_MONOTONIC()


class _DatetimeMeta(type):
    def __instancecheck__(cls, instance: Any) -> bool:
        return isinstance(instance, _datetime.datetime)

    def __call__(cls, *args: Any, **kwargs: Any) -> _datetime.datetime:
        return _datetime.datetime(*args, **kwargs)

    def __getattr__(cls, name: str) -> Any:
        return getattr(_datetime.datetime, name)


class _ClockDatetime(metaclass=_DatetimeMeta):
    @classmethod
    def now(cls, tz: Any = None) -> Any:
        from ._runtime import _selected_env

        env = _selected_env()
        if env is None:
            return _datetime.datetime.now(tz)
        current = env.now()
        return current.astimezone(tz) if tz is not None else current.astimezone().replace(tzinfo=None)

    @classmethod
    def utcnow(cls) -> Any:
        from ._runtime import _selected_env

        env = _selected_env()
        return env.now().replace(tzinfo=None) if env is not None else _datetime.datetime.utcnow()


class _DatetimeModule(ModuleType):
    datetime = _ClockDatetime

    def __getattr__(self, name: str) -> Any:
        return getattr(_datetime, name)


def _clock_monotonic() -> float:
    from ._runtime import _selected_env

    env = _selected_env(driver=False)
    return env._virtual_time if env is not None else _REAL_MONOTONIC()


def acquire_clocks(env: Any) -> None:
    """Install process dispatch once, retaining independent loop ownership."""
    global _clock_originals
    with _clock_lock:
        if not _clock_owners:
            utils: Any = discord.utils
            tasks: Any = _ext_tasks
            if utils.datetime is not _datetime or tasks.datetime is not _datetime:
                raise SetupError("discord.py datetime module bindings changed")
            _clock_originals = (_time.monotonic, utils.datetime, tasks.datetime)
            _time.monotonic = _clock_monotonic
            utils.datetime = _DatetimeModule("datetime")
            tasks.datetime = _DatetimeModule("datetime")
        _clock_owners.add(env)


def release_clocks(env: Any) -> None:
    global _clock_originals
    with _clock_lock:
        _clock_owners.discard(env)
        if not _clock_owners and _clock_originals is not None:
            utils: Any = discord.utils
            tasks: Any = _ext_tasks
            _time.monotonic, utils.datetime, tasks.datetime = _clock_originals
            _clock_originals = None


def get_state(client: discord.Client) -> Any:
    """The client's ConnectionState (cache + gateway event parsers)."""
    return client._connection


def message_belongs_to_states(message: discord.Message, states: Collection[Any]) -> bool:
    """Check originating connections, including prior generations, without trusting IDs."""
    state = message._state
    if isinstance(state, (_InteractionMessageState, _WebhookState)):
        state = state._parent
    return state in states


def resource_belongs_to_states(
    resource: (
        discord.app_commands.AppCommand
        | discord.ScheduledEvent
        | discord.Guild
        | discord.Role
        | discord.Member
        | discord.User
        | discord.ClientUser
    ),
    states: Collection[Any],
) -> bool:
    """Check native SDK connection provenance before IDs enter public operations."""
    return resource._state in states


def resolve_pending_chunk(state: Any, guild_id: int, nonce: str | None) -> None:
    """Wake a member-chunk request whose guild vanished before it was answered.

    When a guild is created and then immediately left/deleted, discord.py's
    startup chunking (``_chunk_and_dispatch``) parks on a ``ChunkRequest`` that
    its own ``parse_guild_members_chunk`` then drops — the guild is already gone
    from cache, so the chunk we deliver is discarded and the waiter is never
    resolved, leaving the wrapper to burn its full ``wait_for`` timeout. Resolve
    the request directly so it completes at once. A no-op once discord.py has
    already resolved and removed the request (the normal, guild-still-present
    path), so it is safe to call after every chunk delivery.
    """
    requests = getattr(state, "_chunk_requests", None)
    if not requests:
        return
    request = requests.get(guild_id)
    if request is not None and (nonce is None or request.nonce == nonce):
        request.done()
        requests.pop(guild_id, None)


def initialize_http_session(http: HTTPClient) -> None:
    """Provide the session captured by SDK interactions and state-backed webhooks."""
    http._HTTPClient__session = None  # type: ignore[assignment]


def bind_webhook_helper(adapter: Any, name: str) -> Any:
    """Run the real SDK helper against the fake adapter's request implementation."""
    helper = getattr(AsyncWebhookAdapter, name)
    return helper.__get__(adapter, type(adapter))


def install_http(client: discord.Client, http: HTTPClient) -> None:
    """Point every captured HTTP reference at the fake transport."""
    client.http = http  # type: ignore[misc]
    get_state(client).http = http
    tree = getattr(client, "tree", None)
    if tree is not None:
        # CommandTree captures its own HTTP reference at construction time.
        tree._http = http


def install_websocket(client: discord.Client, ws: Any) -> None:
    """Install the fake upstream gateway as ``client.ws``.

    ``Client._get_websocket`` (which ConnectionState uses for chunk requests)
    returns ``self.ws``, so this one assignment routes REQUEST_GUILD_MEMBERS
    — startup chunking, ``Guild.chunk()``, ``Guild.query_members()`` — to the
    fake, instead of crashing on the ``None`` ws of a never-connected client.
    """
    client.ws = ws


def _shards(client: discord.AutoShardedClient) -> dict[int, Any]:
    return client.__dict__["_AutoShardedClient__shards"]


def install_shards(client: discord.AutoShardedClient, shards: dict[int, Any]) -> None:
    """Populate the shard mapping behind ``AutoShardedClient.shards``."""
    installed = _shards(client)
    installed.clear()
    installed.update(shards)


def clear_shards(client: discord.AutoShardedClient) -> None:
    """Remove fake shard adapters when an environment detaches."""
    _shards(client).clear()


def set_guild_ready_timeout(client: discord.Client, timeout: float) -> None:
    """No guilds arrive before our READY; don't wait for stragglers."""
    get_state(client).guild_ready_timeout = timeout


def set_webhook_adapter(adapter: Any) -> Any:
    """Interaction responses go through this context-local adapter, not HTTPClient.

    Returns the previous adapter so it can be restored with
    :func:`reset_webhook_adapter`. We restore by value rather than with a
    ``ContextVar`` token because a restart sets and clears the adapter across
    different async contexts, and a token can only be reset in its own context.
    """
    previous = async_context.get()
    async_context.set(adapter)
    return previous


def reset_webhook_adapter(previous: Any) -> None:
    async_context.set(previous)
