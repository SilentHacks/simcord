"""Tracked-work classification and virtual timer bookkeeping for Env."""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING, Any

from . import _dpy_internals

if TYPE_CHECKING:
    from ._runtime import _CallbackRecord
    from .env import Env

# Cap on virtual callbacks drained per settle turn so self-rescheduling
# zero-delay chains cannot starve the settlement deadline check.
_DUE_CALLBACK_BATCH = 64


def _run_due_virtual_callbacks(self: Env, deadline: float) -> None:
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


def _callback_fire_time(self: Env, record: _CallbackRecord) -> float:
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


def _is_virtual_sleep_waiter(self: Env, waiter: Any, deadline: float) -> bool:
    if waiter is None or waiter.done():
        return False
    for record in self._callbacks:
        handle = record.handle
        if record.when is None or handle is None or handle.cancelled():
            continue
        if _callback_fire_time(self, record) <= deadline:
            continue
        callback = _dpy_internals._original_callback(getattr(handle, "_callback", None))
        if getattr(callback, "__name__", "") == "_set_result_unless_cancelled" and any(
            arg is waiter for arg in getattr(handle, "_args", ())
        ):
            return True
    return False


def _timer_records_waking(self: Env, task: asyncio.Task[Any], waiter: Any) -> list[_CallbackRecord]:
    """Tracked timers whose only effect is resuming this task's current wait."""
    found: list[_CallbackRecord] = []
    for record in self._callbacks:
        handle = record.handle
        if record.when is None or handle is None or handle.cancelled():
            continue
        if record.when <= self._virtual_time:
            continue  # already due — the due-callback pass fires it this turn
        callback = _dpy_internals._original_callback(getattr(handle, "_callback", None))
        if _dpy_internals.is_wakeup_callback(callback, getattr(handle, "_args", ()), waiter, task):
            found.append(record)
    return found


def _recognized_wait(self: Env, task: asyncio.Task[Any], waiter: Any) -> bool:
    return (
        task in self._external_waits
        or _dpy_internals.is_listener_future(self.bot, waiter)
        or _dpy_internals.is_wait_for_listener(self.bot, task)
        or _dpy_internals.is_view_wait_future(self.bot, waiter)
        or _dpy_internals.view_expiry_task(self.bot, task, waiter)
        or _dpy_internals.tasks_loop_sleep(task, waiter)
    )


def _wakes_recognized_wait(self: Env, record: _CallbackRecord) -> bool:
    """True when a timer about to fire only resumes a currently recognized wait."""
    handle = record.handle
    if handle is None or handle.cancelled():
        return False
    callback = _dpy_internals._original_callback(getattr(handle, "_callback", None))
    args = getattr(handle, "_args", ())
    for task in _owned_tasks(
        self,
    ):
        waiter = getattr(task, "_fut_waiter", None)
        if _dpy_internals.is_wakeup_callback(callback, args, waiter, task) and _recognized_wait(
            self, task, waiter
        ):
            return True
    return False


def _virtualize_recognized_waits(self: Env, pending: list[asyncio.Task[Any]]) -> None:
    """Strip the real fallback from timers that only resume a recognized wait.

    A wait parked on purpose must fire exclusively through advance_time(): a
    wall-clock fallback would let it expire mid-operation and reintroduce the
    nondeterminism settlement exists to remove.
    """
    for task in pending:
        waiter = getattr(task, "_fut_waiter", None)
        if not _recognized_wait(self, task, waiter):
            continue
        for record in _timer_records_waking(self, task, waiter):
            real = record.real_handle
            if real is not None and not real.cancelled():
                real.cancel()


def _owned_tasks(self: Env) -> list[asyncio.Task[Any]]:
    return [task for task in self._task_records if not task.done()]


def _active_callbacks(self: Env) -> list[_CallbackRecord]:
    live = [
        record for record in self._callbacks if record.handle is not None and not record.handle.cancelled()
    ]
    self._callbacks = live
    return live


def _callback_is_parked(self: Env, record: _CallbackRecord, deadline: float) -> bool:
    return record.when is not None and _callback_fire_time(self, record) > deadline


def _is_parked(self: Env, task: asyncio.Task[Any], deadline: float) -> bool:
    return _park_reason(self, task, deadline) is not None


def _park_reason(
    self: Env, task: asyncio.Task[Any], deadline: float, seen: set[int] | None = None
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
    if _dpy_internals.view_expiry_task(self.bot, task, waiter) and _timer_records_waking(self, task, waiter):
        return "discord View/Modal expiry timer"
    if _dpy_internals.tasks_loop_sleep(task, waiter) and _timer_records_waking(self, task, waiter):
        return "discord.ext.tasks loop interval"
    dependencies = _dpy_internals.composed_tasks(task, waiter)
    unresolved = [dependency for dependency in dependencies if not dependency.done()]
    if unresolved:
        reasons: list[str] = []
        for dependency in unresolved:
            if isinstance(dependency, asyncio.Task):
                if dependency not in self._task_records:
                    break
                reason = _park_reason(self, dependency, deadline, seen)
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
    if _is_virtual_sleep_waiter(self, waiter, deadline) or _dpy_internals.is_sleep_waiter(
        waiter, self._loop, deadline
    ):
        return "sleep timer beyond settlement deadline"


def _settle_timeout_message(
    self: Env,
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


def _next_virtual_timer(self: Env) -> float | None:
    times = [
        record.when
        for record in self._callbacks
        if record.when is not None and record.handle is not None and not record.handle.cancelled()
    ]
    return min(times) if times else None
