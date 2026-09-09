"""Every touch of a private discord.py API, quarantined in one module.

If a discord.py release changes one of these internals, the import-time
self-check below fails with a clear message instead of users' tests breaking
mysteriously. Keep this inventory in sync with what the framework touches.
"""

import asyncio
import sys
from typing import Any

import discord
from discord.gateway import DiscordWebSocket
from discord.http import HTTPClient
from discord.state import ChunkRequest, ConnectionState
from discord.ui import view as _view
from discord.webhook.async_ import async_context

from .backend.errors import SetupError


def listener_futures(client: discord.Client) -> list[Any]:
    """Every future registered by ``Client.wait_for`` (``Client._listeners``)."""
    listeners = getattr(client, "_listeners", None)
    return [fut for entries in (listeners or {}).values() for fut, _ in entries]


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
    ):
        if not hasattr(cls, attr):  # pragma: no cover - fires only if discord.py drops an internal
            problems.append(f"{cls.__name__}.{attr}")
    run_event_code = getattr(discord.Client._run_event, "__code__", None)
    if run_event_code is None or "coro" not in run_event_code.co_varnames:
        problems.append("discord.Client._run_event no longer exposes its handler coroutine")
    client_probe = discord.Client(intents=discord.Intents.none())
    if not hasattr(client_probe, "_listeners"):
        problems.append("discord.Client no longer exposes _listeners")
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
    if not hasattr(loop, "_scheduled"):
        missing.append("_scheduled timer heap")
    task = asyncio.current_task()
    for name in ("_exception", "_fut_waiter", "_log_traceback"):
        if task is None or not hasattr(task, name):
            missing.append(f"Task.{name}")
    factory = loop.get_task_factory() if callable(getattr(loop, "get_task_factory", None)) else None
    if factory is not None and not callable(factory):
        missing.append("callable task factory")
    if missing:
        raise SetupError("simcord requires asyncio loop capabilities: " + ", ".join(missing))
    return None


def is_listener_future(client: discord.Client, waiter: Any) -> bool:
    return any(future is waiter and not future.done() for future in listener_futures(client))


def is_wait_for_listener(client: discord.Client, task: asyncio.Task[Any]) -> bool:  # pragma: no cover
    """Recognize the CPython 3.11 ``wait_for`` wrapper around a listener future."""
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 11):
        return False
    wait_for_code = getattr(asyncio.wait_for, "__code__", None)
    if wait_for_code is None:
        return False
    coroutine: Any = task.get_coro()
    seen: set[int] = set()
    while coroutine is not None and id(coroutine) not in seen:
        seen.add(id(coroutine))
        frame = getattr(coroutine, "cr_frame", None)
        if frame is not None and frame.f_code is wait_for_code:
            if is_listener_future(client, frame.f_locals.get("fut")):
                return True
        coroutine = getattr(coroutine, "cr_await", None)
    return False


def _stored_wait_futures(client: discord.Client) -> set[Any]:
    state = get_state(client)
    store = getattr(state, "_view_store", None)
    futures: set[Any] = set()
    if store is None:
        return futures
    views = getattr(store, "_views", {})
    for entries in views.values():
        for item in entries.values():
            view = getattr(item, "_view", None)
            if view is None:
                continue
            for name, value in vars(view).items():
                if name.endswith("__stopped") and isinstance(value, asyncio.Future):
                    futures.add(value)
    for modal in getattr(store, "_modals", {}).values():
        for name, value in vars(modal).items():
            if name.endswith("__stopped") and isinstance(value, asyncio.Future):
                futures.add(value)
    return futures


def is_view_wait_future(client: discord.Client, waiter: Any) -> bool:
    return waiter in _stored_wait_futures(client) and not waiter.done()


def _original_callback(callback: Any) -> Any:
    while True:
        original = getattr(callback, "__simcord_original_callback__", callback)
        if original is callback:
            return callback
        callback = original


def is_sleep_waiter(waiter: Any, loop: Any, deadline: float) -> bool:
    if waiter is None or waiter.done():
        return False
    for handle in getattr(loop, "_scheduled", ()):
        if handle.cancelled() or handle.when() <= deadline:
            continue
        callback = _original_callback(getattr(handle, "_callback", None))
        if getattr(callback, "__name__", "") == "_set_result_unless_cancelled" and any(
            arg is waiter for arg in getattr(handle, "_args", ())
        ):
            return True
    return False


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
            for cell in callback.__closure__ or ():
                value = cell.cell_contents
                if isinstance(value, asyncio.Future):
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

def view_time() -> Any:
    """Return discord.py's View clock."""
    return _view.time  # type: ignore[reportPrivateUsage]


def swap_view_time(value: Any) -> Any:
    """Replace discord.py's View clock and return the previous value."""
    previous = _view.time  # type: ignore[reportPrivateUsage]
    _view.time = value  # type: ignore[reportPrivateUsage]
    return previous


def get_state(client: discord.Client) -> Any:
    """The client's ConnectionState (cache + gateway event parsers)."""
    return client._connection


def parsers(client: discord.Client) -> dict[str, Any]:
    return get_state(client).parsers


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
