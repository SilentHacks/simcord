"""Direct coverage for the wait-recognition internals settlement relies on."""

from __future__ import annotations

import asyncio
import asyncio.timeouts
import math
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import discord
import pytest
from discord.ext import tasks as ext_tasks

import simcord
from simcord import _dpy_internals


def _fake_task(waiter: Any = None) -> Mock:
    task = Mock()
    task.done.return_value = False
    task._fut_waiter = waiter
    # A Mock coroutine's auto-generated cr_await children never terminate the
    # 3.11 listener/compose stack walk; end it deterministically instead.
    task.get_coro = lambda: SimpleNamespace(cr_frame=None)
    return task


def _set_result_unless_cancelled(fut: Any) -> None:
    pass


def _release_waiter(*args: Any) -> None:
    pass


def _wrapped_set_result(fut: Any) -> None:
    pass


def _plain_callback() -> None:
    pass


def _client_with_listeners(*futures: asyncio.Future[Any]) -> Any:
    return SimpleNamespace(_listeners={"message": [(fut, None) for fut in futures]})


async def test_is_wakeup_shaped_covers_known_resolvers() -> None:
    assert _dpy_internals.is_wakeup_shaped(_set_result_unless_cancelled)
    assert _dpy_internals.is_wakeup_shaped(_release_waiter)
    assert _dpy_internals.is_wakeup_shaped(_wrapped_set_result)


async def test_is_wakeup_shaped_covers_future_bound_methods() -> None:
    future: asyncio.Future[Any] = asyncio.Future()
    assert _dpy_internals.is_wakeup_shaped(future.set_result)
    assert _dpy_internals.is_wakeup_shaped(future.set_exception)


async def test_is_wakeup_shaped_rejects_plain_callbacks() -> None:
    assert not _dpy_internals.is_wakeup_shaped(_plain_callback)


async def test_is_wakeup_callback_matches_waiter_in_args() -> None:
    future: asyncio.Future[Any] = asyncio.Future()
    task = _fake_task()
    assert _dpy_internals.is_wakeup_callback(_set_result_unless_cancelled, (future,), future, task)


async def test_is_wakeup_callback_matches_bound_method_on_waiter() -> None:
    future: asyncio.Future[Any] = asyncio.Future()
    task = _fake_task()
    assert _dpy_internals.is_wakeup_callback(future.set_result, (), future, task)


async def test_is_wakeup_callback_matches_timeout_owner() -> None:
    task = _fake_task()
    other = _fake_task()
    timeout: Any = asyncio.timeouts.Timeout(1.0)
    vars(timeout)["_task"] = task
    assert _dpy_internals.is_wakeup_callback(timeout._on_timeout, (), None, task)
    assert not _dpy_internals.is_wakeup_callback(timeout._on_timeout, (), None, other)


async def test_is_wakeup_callback_rejects_unrelated_timer() -> None:
    future: asyncio.Future[Any] = asyncio.Future()
    task = _fake_task()
    assert not _dpy_internals.is_wakeup_callback(_plain_callback, (1,), future, task)
    assert not _dpy_internals.is_wakeup_callback(_set_result_unless_cancelled, (object(),), future, task)


def _store_client(*items: Any, modals: dict[Any, Any] | None = None) -> Any:
    store = SimpleNamespace(
        _views={1: {f"id{i}": item for i, item in enumerate(items)}}, _modals=modals or {}
    )
    return SimpleNamespace(_connection=SimpleNamespace(_view_store=store))


def _task_with_timeout(view: Any) -> Any:
    task = _fake_task()
    vars(view)["_BaseView__timeout_task"] = task
    return task


async def test_view_timeout_task_identity_matches_registered_view() -> None:
    view = discord.ui.View(timeout=5)
    task = _task_with_timeout(view)
    item = SimpleNamespace(_view=view)
    client = _store_client(item)
    assert _dpy_internals._view_timeout_task_identity(client, task)


async def test_view_timeout_task_identity_matches_registered_modal() -> None:
    modal = discord.ui.Modal(title="m", timeout=5)
    task = _task_with_timeout(modal)
    client = _store_client(modals={"key": modal})
    assert _dpy_internals._view_timeout_task_identity(client, task)


async def test_view_timeout_task_identity_rejects_unregistered_and_foreign_tasks() -> None:
    client = _store_client()
    assert not _dpy_internals._view_timeout_task_identity(client, _fake_task())

    view = discord.ui.View(timeout=5)
    item = SimpleNamespace(_view=view)
    assert not _dpy_internals._view_timeout_task_identity(_store_client(item), _fake_task())


async def test_view_timeout_task_identity_skips_stopped_views() -> None:
    view = SimpleNamespace()
    stopped: asyncio.Future[Any] = asyncio.Future()
    stopped.set_result(None)
    vars(view)["_View__stopped"] = stopped
    vars(view)["_View__timeout_task"] = _fake_task()
    item = SimpleNamespace(_view=view)
    assert not _dpy_internals._view_timeout_task_identity(_store_client(item), _fake_task())


async def test_view_timeout_task_identity_handles_missing_store() -> None:
    client: Any = SimpleNamespace(_connection=SimpleNamespace(_view_store=None))
    assert not _dpy_internals._view_timeout_task_identity(client, _fake_task())


def _impl_coro_task(view: Any, waiter: Any = None) -> tuple[Any, Any]:
    """A fake task running the view's real ``__timeout_task_impl`` coroutine."""
    task = _fake_task(waiter)
    coro = view._BaseView__timeout_task_impl()  # never awaited; caller closes it
    task.get_coro = lambda: coro
    return task, coro


async def test_view_timeout_task_identity_matches_impl_coroutine() -> None:
    """A fully-dynamic view lands in no store container; the coroutine itself
    still identifies its expiry task."""
    view = discord.ui.LayoutView(timeout=5)
    task, coro = _impl_coro_task(view)
    try:
        assert _dpy_internals._view_timeout_task_identity(_store_client(), task)
    finally:
        coro.close()


async def test_view_timeout_task_identity_rejects_stopped_impl_owner() -> None:
    view = discord.ui.LayoutView(timeout=5)
    task, coro = _impl_coro_task(view)
    stopped: asyncio.Future[Any] = asyncio.Future()
    stopped.set_result(None)
    stopped_name = next(name for name in vars(view) if name.endswith("__stopped"))
    vars(view)[stopped_name] = stopped
    try:
        assert not _dpy_internals._view_timeout_task_identity(_store_client(), task)
    finally:
        coro.close()


async def test_view_timeout_task_identity_rejects_foreign_coroutine() -> None:
    task = _fake_task()
    task.get_coro = lambda: SimpleNamespace(cr_code=object(), cr_frame=None)
    assert not _dpy_internals._view_timeout_task_identity(_store_client(), task)


async def test_view_timeout_task_identity_ignores_closed_impl_coroutine() -> None:
    """A finished impl coroutine has no live frame, so no owner to match."""
    view = discord.ui.LayoutView(timeout=5)
    coro = view._BaseView__timeout_task_impl()
    coro.close()
    task = _fake_task()
    task.get_coro = lambda: coro
    assert not _dpy_internals._view_timeout_task_identity(_store_client(), task)


async def test_is_intentional_wait_wakeup_impl_coroutine() -> None:
    view = discord.ui.LayoutView(timeout=5)
    task, coro = _impl_coro_task(view)
    try:
        assert _dpy_internals.is_intentional_wait_wakeup(
            _store_client(), _set_result_unless_cancelled, (), task
        )
    finally:
        coro.close()


async def test_view_expiry_task_requires_pending_waiter() -> None:
    view = discord.ui.View(timeout=5)
    task = _task_with_timeout(view)
    client = _store_client(SimpleNamespace(_view=view))
    assert not _dpy_internals.view_expiry_task(client, task, None)
    done: asyncio.Future[Any] = asyncio.Future()
    done.set_result(None)
    assert not _dpy_internals.view_expiry_task(client, task, done)
    pending: asyncio.Future[Any] = asyncio.Future()
    assert _dpy_internals.view_expiry_task(client, task, pending)


def _task_with_frame(f_locals: dict[str, Any] | None) -> Any:
    frame = SimpleNamespace(f_locals=f_locals, f_code=None) if f_locals is not None else None
    coro = SimpleNamespace(cr_frame=frame)
    return SimpleNamespace(get_coro=lambda: coro)


async def test_is_tasks_loop_task_matches_loop_frame() -> None:
    @ext_tasks.loop(seconds=1)
    async def probe() -> None:
        pass

    assert _dpy_internals.is_tasks_loop_task(_task_with_frame({"self": probe}))
    assert not _dpy_internals.is_tasks_loop_task(_task_with_frame({"self": object()}))
    assert not _dpy_internals.is_tasks_loop_task(_task_with_frame(None))


async def test_tasks_loop_sleep_matches_current_handle_future() -> None:
    @ext_tasks.loop(seconds=1)
    async def probe() -> None:
        pass

    waiter: asyncio.Future[Any] = asyncio.Future()
    probe._handle = SimpleNamespace(future=waiter)  # type: ignore[attr-defined]
    task = _task_with_frame({"self": probe})
    assert _dpy_internals.tasks_loop_sleep(task, waiter)
    assert not _dpy_internals.tasks_loop_sleep(task, asyncio.Future())
    assert not _dpy_internals.tasks_loop_sleep(_task_with_frame({"self": object()}), waiter)
    assert not _dpy_internals.tasks_loop_sleep(task, None)
    done: asyncio.Future[Any] = asyncio.Future()
    done.set_result(None)
    assert not _dpy_internals.tasks_loop_sleep(task, done)


async def test_is_intentional_wait_wakeup_view_expiry() -> None:
    view = discord.ui.View(timeout=5)
    task = _task_with_timeout(view)
    client = _store_client(SimpleNamespace(_view=view))
    assert _dpy_internals.is_intentional_wait_wakeup(client, _set_result_unless_cancelled, (), task)
    assert not _dpy_internals.is_intentional_wait_wakeup(
        _store_client(), _set_result_unless_cancelled, (), task
    )


async def test_is_intentional_wait_wakeup_tasks_loop() -> None:
    @ext_tasks.loop(seconds=1)
    async def probe() -> None:
        pass

    client: Any = SimpleNamespace(_connection=SimpleNamespace(_view_store=None))
    loop_task = _task_with_frame({"self": probe})
    assert _dpy_internals.is_intentional_wait_wakeup(client, _wrapped_set_result, (), loop_task)
    assert not _dpy_internals.is_intentional_wait_wakeup(
        client, _wrapped_set_result, (), _task_with_frame({"self": object()})
    )


async def test_is_intentional_wait_wakeup_release_waiter() -> None:
    listener: asyncio.Future[Any] = asyncio.Future()
    client = _client_with_listeners(listener)
    assert _dpy_internals.is_intentional_wait_wakeup(client, _release_waiter, (listener,), _fake_task())
    assert not _dpy_internals.is_intentional_wait_wakeup(client, _release_waiter, (object(),), _fake_task())
    assert not _dpy_internals.is_intentional_wait_wakeup(client, _plain_callback, (listener,), _fake_task())


async def test_listener_futures_collects_registered_futures() -> None:
    first: asyncio.Future[Any] = asyncio.Future()
    second: asyncio.Future[Any] = asyncio.Future()
    client = _client_with_listeners(first, second)
    assert _dpy_internals.is_listener_future(client, first)
    assert _dpy_internals.is_listener_future(client, second)
    assert not _dpy_internals.is_listener_future(client, asyncio.Future())
    first.set_result(None)
    assert not _dpy_internals.is_listener_future(client, first)


def _record(env: simcord.Env, callback: Any, args: tuple[Any, ...], when: float) -> Any:
    loop = asyncio.new_event_loop()
    handle = asyncio.TimerHandle(when, callback, args, loop)
    record = simcord.env._CallbackRecord(handle, when, "probe")  # type: ignore[attr-defined]
    env._callbacks.append(record)
    return record


@pytest.fixture
def env() -> Any:
    bot = discord.Client(intents=discord.Intents.none())
    return simcord.Env(bot)


async def test_timer_records_waking_filters_non_matching(env: Any) -> None:
    waiter: asyncio.Future[Any] = asyncio.Future()
    task = _fake_task(waiter)

    assert env._timer_records_waking(task, waiter) == []

    matching = _record(env, _set_result_unless_cancelled, (waiter,), 10.0)
    unrelated = _record(env, _set_result_unless_cancelled, (asyncio.Future(),), 10.0)
    due = _record(env, _set_result_unless_cancelled, (waiter,), -1.0)
    cancelled = _record(env, _set_result_unless_cancelled, (waiter,), 10.0)
    cancelled.handle.cancel()

    found = env._timer_records_waking(task, waiter)
    assert matching in found
    assert unrelated not in found
    assert due not in found
    assert cancelled not in found


async def test_wakes_recognized_wait_requires_live_handle(env: Any) -> None:
    record = _record(env, _plain_callback, (), 10.0)
    assert not env._wakes_recognized_wait(record)
    record.handle.cancel()
    assert not env._wakes_recognized_wait(record)
    record.handle = None
    assert not env._wakes_recognized_wait(record)


async def test_wakes_recognized_wait_matches_declared_task(env: Any) -> None:
    waiter: asyncio.Future[Any] = asyncio.Future()
    task = _fake_task(waiter)
    env._external_waits[task] = "declared"  # type: ignore[index]
    env._task_records[task] = SimpleNamespace()  # type: ignore[index]

    record = _record(env, _set_result_unless_cancelled, (waiter,), 10.0)
    assert env._wakes_recognized_wait(record)


async def test_wakes_recognized_wait_rejects_unrecognized_task(env: Any) -> None:
    waiter: asyncio.Future[Any] = asyncio.Future()
    task = _fake_task(waiter)
    env._task_records[task] = SimpleNamespace()  # type: ignore[index]

    record = _record(env, _set_result_unless_cancelled, (waiter,), 10.0)
    assert not env._wakes_recognized_wait(record)


def _parked_task(waiter: Any) -> Any:
    """A hashable fake suspended task whose coroutine introspection ends quickly."""
    return _fake_task(waiter)


def _register_view(env: Any, view: Any) -> None:
    store = env.bot._connection._view_store  # type: ignore[attr-defined]
    store._views.setdefault(1, {})["item"] = SimpleNamespace(_view=view)


async def test_stored_wait_futures_handles_missing_store_and_null_views() -> None:
    client: Any = SimpleNamespace(_connection=SimpleNamespace(_view_store=None))
    assert _dpy_internals._stored_wait_futures(client) == set()

    stopped: asyncio.Future[Any] = asyncio.Future()
    view = SimpleNamespace()
    vars(view)["_View__stopped"] = stopped
    client = _store_client(SimpleNamespace(_view=None), SimpleNamespace(_view=view))
    assert _dpy_internals._stored_wait_futures(client) == {stopped}


async def test_is_sleep_waiter_matches_scheduled_wake() -> None:
    waiter: asyncio.Future[Any] = asyncio.Future()
    handle = SimpleNamespace(
        cancelled=lambda: False,
        when=lambda: 100.0,
        _callback=_set_result_unless_cancelled,
        _args=(waiter,),
    )
    loop = SimpleNamespace(_scheduled=[handle])
    assert _dpy_internals.is_sleep_waiter(waiter, loop, 50.0)
    assert not _dpy_internals.is_sleep_waiter(None, loop, 50.0)
    done: asyncio.Future[Any] = asyncio.Future()
    done.set_result(None)
    assert not _dpy_internals.is_sleep_waiter(done, loop, 50.0)


async def test_state_helpers_and_install_http() -> None:
    state = SimpleNamespace(parsers={"message_create": object()})
    client: Any = SimpleNamespace(_connection=state, tree=SimpleNamespace())
    assert _dpy_internals.get_state(client) is state
    assert _dpy_internals.parsers(client) is state.parsers

    http: Any = SimpleNamespace()
    _dpy_internals.install_http(client, http)
    assert client.http is http
    assert state.http is http
    assert client.tree._http is http

    client_no_tree: Any = SimpleNamespace(_connection=SimpleNamespace(), tree=None)
    _dpy_internals.install_http(client_no_tree, http)
    assert client_no_tree._connection.http is http


async def test_view_timeout_task_identity_skips_null_candidates() -> None:
    client = _store_client(SimpleNamespace(_view=None))
    assert not _dpy_internals._view_timeout_task_identity(client, _fake_task())


async def test_external_wait_rejects_bad_reason_and_foreign_context(env: Any) -> None:
    async def waitable() -> None:
        pass

    with pytest.raises(simcord.SetupError, match="non-empty"):
        await env.external_wait(waitable(), reason="  ")
    with pytest.raises(simcord.SetupError, match="bot-owned work"):
        await env.external_wait(waitable(), reason="ok")
    with pytest.raises(simcord.SetupError, match="bot-owned work"):
        await env.external_wait(asyncio.Future(), reason="ok")


async def test_external_wait_rejects_nested_declaration(env: Any) -> None:
    async def waitable() -> None:
        pass

    task = asyncio.current_task()
    assert task is not None
    env._task_records[task] = SimpleNamespace()  # type: ignore[index]
    env._external_waits[task] = "outer"  # type: ignore[index]
    try:
        with pytest.raises(simcord.SetupError, match="cannot be nested"):
            await env.external_wait(waitable(), reason="inner")
    finally:
        env._external_waits.pop(task, None)  # type: ignore[arg-type]


async def test_callback_fire_time_reports_real_and_virtual_states(env: Any) -> None:
    due = _record(env, _plain_callback, (), -1.0)
    assert env._callback_fire_time(due) == -math.inf

    virtual = _record(env, _plain_callback, (), 10.0)
    assert env._callback_fire_time(virtual) == math.inf

    live = _record(env, _plain_callback, (), 10.0)
    live.real_handle = live.handle
    assert env._callback_fire_time(live) == live.handle.when()

    cancelled = _record(env, _plain_callback, (), 10.0)
    cancelled.real_handle = cancelled.handle
    cancelled.real_handle.cancel()
    assert env._callback_fire_time(cancelled) == math.inf


async def test_is_virtual_sleep_waiter_filters_records(env: Any) -> None:
    waiter: asyncio.Future[Any] = asyncio.Future()
    assert not env._is_virtual_sleep_waiter(None, 5.0)
    done: asyncio.Future[Any] = asyncio.Future()
    done.set_result(None)
    assert not env._is_virtual_sleep_waiter(done, 5.0)

    _record(env, _set_result_unless_cancelled, (waiter,), 10.0)
    assert env._is_virtual_sleep_waiter(waiter, 5.0)

    in_deadline = _record(env, _set_result_unless_cancelled, (asyncio.Future(),), 1.0)
    in_deadline.real_handle = in_deadline.handle
    assert not env._is_virtual_sleep_waiter(asyncio.Future(), 5.0)


async def test_park_reason_names_recognized_waits(env: Any) -> None:
    task = _parked_task(asyncio.Future())
    assert env._park_reason(task, 5.0) is None

    done_task = _parked_task(None)
    done_task.done.return_value = True
    assert env._park_reason(done_task, 5.0) == "completed"

    seen: set[int] = set()
    running = _parked_task(asyncio.Future())
    seen.add(id(running))
    assert env._park_reason(running, 5.0, seen) is None

    declared = _parked_task(asyncio.Future())
    env._external_waits[declared] = "model reply"  # type: ignore[index]
    assert env._park_reason(declared, 5.0) == "model reply"

    listener: asyncio.Future[Any] = asyncio.Future()
    env.bot._listeners.setdefault("on_message", []).append((listener, None))
    listening = _parked_task(listener)
    assert env._park_reason(listening, 5.0) == "discord Client.wait_for listener"


async def test_park_reason_names_view_expiry_and_loop_interval(env: Any) -> None:
    view = discord.ui.View(timeout=60)
    task = _parked_task(asyncio.Future())
    vars(view)["_BaseView__timeout_task"] = task
    _register_view(env, view)
    _record(env, _set_result_unless_cancelled, (task._fut_waiter,), 10.0)
    assert env._park_reason(task, 5.0) == "discord View/Modal expiry timer"

    @ext_tasks.loop(seconds=1)
    async def probe() -> None:
        pass

    waiter: asyncio.Future[Any] = asyncio.Future()
    probe._handle = SimpleNamespace(future=waiter)  # type: ignore[attr-defined]
    loop_task = _parked_task(waiter)
    loop_task.get_coro = lambda: SimpleNamespace(
        cr_frame=SimpleNamespace(f_locals={"self": probe}, f_code=None)
    )
    _record(env, _wrapped_set_result, (waiter,), 10.0)
    assert env._park_reason(loop_task, 5.0) == "discord.ext.tasks loop interval"


async def test_park_reason_names_unregistered_view_expiry(env: Any) -> None:
    """A fully-dynamic view occupies no store container; its expiry task is
    still a recognized wait, identified by the coroutine it runs."""
    view = discord.ui.LayoutView(timeout=60)
    waiter: asyncio.Future[Any] = asyncio.Future()
    task, coro = _impl_coro_task(view, waiter)
    try:
        _record(env, _set_result_unless_cancelled, (waiter,), 10.0)
        assert env._park_reason(task, 5.0) == "discord View/Modal expiry timer"
    finally:
        coro.close()


async def test_park_reason_composed_and_sleep_waits(env: Any) -> None:
    listener: asyncio.Future[Any] = asyncio.Future()
    env.bot._listeners.setdefault("on_message", []).append((listener, None))
    waiter = asyncio.gather(listener)
    task = _parked_task(waiter)
    assert env._park_reason(task, 5.0) == "composed external wait"

    sleeper: asyncio.Future[Any] = asyncio.Future()
    sleeping = _parked_task(sleeper)
    _record(env, _set_result_unless_cancelled, (sleeper,), 10.0)
    assert env._park_reason(sleeping, 5.0) == "sleep timer beyond settlement deadline"


async def test_composed_tasks_unwraps_gather_children() -> None:
    first: asyncio.Future[Any] = asyncio.Future()
    second: asyncio.Future[Any] = asyncio.Future()
    waiter = asyncio.gather(first, second)
    found = _dpy_internals.composed_tasks(_parked_task(None), waiter)
    assert first in found
    assert second in found


async def test_settle_timeout_message_classifies_pending_work(env: Any) -> None:
    env._last_dispatch = "actor.send"

    declared = _parked_task(asyncio.Future())
    env._task_records[declared] = SimpleNamespace(label="declared", generation=0)
    env._external_waits[declared] = "model reply"  # type: ignore[index]

    listener: asyncio.Future[Any] = asyncio.Future()
    env.bot._listeners.setdefault("on_message", []).append((listener, None))
    listening = _parked_task(listener)
    env._task_records[listening] = SimpleNamespace(label="listening", generation=0)

    view = discord.ui.View(timeout=60)
    waiting: asyncio.Future[Any] = asyncio.Future()
    vars(view)["_View__stopped"] = waiting
    _register_view(env, view)
    view_wait = _parked_task(waiting)
    env._task_records[view_wait] = SimpleNamespace(label="view", generation=0)

    runnable = _parked_task(None)
    env._task_records[runnable] = SimpleNamespace(label="runnable", generation=0)

    unknown = _parked_task(asyncio.Future())
    env._task_records[unknown] = SimpleNamespace(label="unknown", generation=0)

    unrecorded = _parked_task(asyncio.Future())

    pending = _record(env, _plain_callback, (), 10.0)
    message = env._settle_timeout_message(
        [declared, listening, view_wait, runnable, unknown, unrecorded],
        [],
        1.0,
        [pending],
    )
    assert "after actor.send" in message
    assert "model reply" in message
    assert "Client.wait_for listener" in message
    assert "View/Modal completion" in message
    assert "runnable continuation" in message
    assert "unknown wait (Future)" in message
    assert "bot-owned callbacks pending" in message


async def test_advance_time_validates_seconds(env: Any) -> None:
    with pytest.raises(simcord.SetupError):
        await env.advance_time(True)
    with pytest.raises(simcord.SetupError):
        await env.advance_time(-1)


async def test_error_cursor_and_dedup(env: Any) -> None:
    assert env.error_cursor == 0
    with pytest.raises(simcord.SetupError):
        env.errors_since(-1)

    error = RuntimeError("boom")
    env._record_error(error)
    env._record_error(error)
    assert env.error_cursor == 1
    assert env.errors_since(0) == (error,)


async def test_guild_requires_creation(env: Any) -> None:
    with pytest.raises(simcord.SetupError):
        _ = env.guild
