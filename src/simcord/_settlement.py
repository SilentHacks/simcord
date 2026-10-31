"""Tracked-work classification and virtual timer bookkeeping for Env."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import discord

from . import _dpy_internals

if TYPE_CHECKING:
    from ._runtime import _CallbackRecord, _WorkState

# Cap on virtual callbacks drained per settle turn so self-rescheduling
# zero-delay chains cannot starve the settlement deadline check.
_DUE_CALLBACK_BATCH = 64


@dataclass(frozen=True, slots=True)
class _SettlementState:
    work: _WorkState
    bot: discord.Client
    loop: asyncio.AbstractEventLoop | None
    virtual_time: float
    advancing: bool
    generation: int
    last_dispatch: str | None
    _listeners: set[Any] | None = field(init=False, default=None)
    _view_facts: tuple[set[Any], set[Any]] | None = field(init=False, default=None)
    reasons: dict[Any, str | None] = field(default_factory=dict)
    visiting: set[Any] = field(default_factory=set)
    foreign_sleeps: dict[Any, float] | None = None

    @property
    def listeners(self) -> set[Any]:
        listeners = self._listeners
        if listeners is None:
            listeners = set(_dpy_internals.listener_futures(self.bot))
            object.__setattr__(self, "_listeners", listeners)
        return listeners

    def _stored_view_facts(self) -> tuple[set[Any], set[Any]]:
        facts = self._view_facts
        if facts is None:
            facts = _dpy_internals._stored_view_facts(self.bot)
            object.__setattr__(self, "_view_facts", facts)
        return facts

    @property
    def views(self) -> set[Any]:
        return self._stored_view_facts()[0]

    @property
    def expiry_tasks(self) -> set[Any]:
        return self._stored_view_facts()[1]


def _run_due_virtual_callbacks(self: _SettlementState, deadline: float) -> None:
    # Bounded per settle turn: a callback rescheduling itself with
    # call_later(0) must yield to the loop and the settlement deadline.
    assert self.loop is not None
    ran = 0
    while True:
        handle = self.work.next_due_callback(self.virtual_time)
        if handle is None:
            return
        if handle.cancelled():
            continue
        try:
            handle._run()
        except Exception:
            # The real loop routes callback failures to its exception
            # handler; advancing virtual timers must keep that behavior.
            pass
        ran += 1
        if ran >= _DUE_CALLBACK_BATCH or self.loop.time() >= deadline:
            return


def _callback_fire_time(self: _SettlementState, record: _CallbackRecord) -> float:
    """Real-clock time at which the callback fires without advance_time().

    ``-inf`` for already-due/immediate callbacks, ``+inf`` when only
    ``advance_time()`` can still reach it (no live real fallback left).
    """
    if record.when is None or record.when <= self.virtual_time:
        return -math.inf
    if self.advancing:
        return math.inf
    real = record.real_handle
    if real is None or real.cancelled():
        return math.inf
    return real.when()


def _sleep_waiter_beyond_deadline(self: _SettlementState, waiter: Any, deadline: float) -> bool:
    if waiter is None or waiter.done():
        return False
    sleeps = self.foreign_sleeps
    if sleeps is None:
        sleeps = {}
        for handle in getattr(self.loop, "_scheduled", ()):
            args = getattr(handle, "_args", ())
            if not args or handle.cancelled():
                continue
            callback = _dpy_internals._original_callback(getattr(handle, "_callback", None))
            if getattr(callback, "__name__", "") == "_set_result_unless_cancelled" and isinstance(
                args[0], asyncio.Future
            ):
                target = args[0]
                sleeps[target] = min(sleeps.get(target, math.inf), handle.when())
        object.__setattr__(self, "foreign_sleeps", sleeps)
    fire_time = sleeps.get(waiter)
    for record in self.work.wake_records(waiter):
        handle = record.handle
        if handle is None or handle.cancelled():
            continue
        args = getattr(handle, "_args", ())
        if not args or args[0] is not waiter:
            continue
        callback = _dpy_internals._original_callback(getattr(handle, "_callback", None))
        if getattr(callback, "__name__", "") == "_set_result_unless_cancelled":
            when = _callback_fire_time(self, record)
            fire_time = when if fire_time is None else min(fire_time, when)
    return fire_time is not None and fire_time > deadline


def _timer_records_waking(
    self: _SettlementState, task: asyncio.Task[Any], waiter: Any
) -> list[_CallbackRecord]:
    """Tracked timers whose only effect is resuming this task's current wait."""
    found: list[_CallbackRecord] = []
    for record in self.work.wake_records(waiter, task):
        handle = record.handle
        if record.when is None or handle is None or handle.cancelled():
            continue
        if record.when <= self.virtual_time:
            continue  # already due — the due-callback pass fires it this turn
        callback = _dpy_internals._original_callback(getattr(handle, "_callback", None))
        if _dpy_internals.is_wakeup_callback(callback, getattr(handle, "_args", ()), waiter, task):
            found.append(record)
    return found


def _recognized_wait(self: _SettlementState, task: asyncio.Task[Any], waiter: Any) -> bool:
    return (
        task in self.work.external_waits
        or (waiter in self.listeners and not waiter.done())
        or _dpy_internals.is_wait_for_listener(self.bot, task, self.listeners)
        or (waiter in self.views and not waiter.done())
        or _dpy_internals.view_expiry_task(self.bot, task, waiter, self.expiry_tasks)
        or _dpy_internals.tasks_loop_sleep(task, waiter)
    )


def _wakes_recognized_wait(self: _SettlementState, record: _CallbackRecord) -> bool:
    """True when a timer about to fire only resumes a currently recognized wait."""
    if not record.wake_targets:
        return False
    handle = record.handle
    if handle is None or handle.cancelled():
        return False
    callback = _dpy_internals._original_callback(getattr(handle, "_callback", None))
    args = getattr(handle, "_args", ())
    targets = set(record.wake_targets)
    for task in _owned_tasks(self):
        waiter = getattr(task, "_fut_waiter", None)
        if task not in targets and waiter not in targets:
            continue
        if _dpy_internals.is_wakeup_callback(callback, args, waiter, task) and _recognized_wait(
            self, task, waiter
        ):
            return True
    return False


def _virtualize_recognized_waits(self: _SettlementState, pending: list[asyncio.Task[Any]]) -> None:
    """Strip the real fallback from timers that only resume a recognized wait.

    A wait parked on purpose must fire exclusively through advance_time(): a
    wall-clock fallback would let it expire mid-operation and reintroduce the
    nondeterminism settlement exists to remove.
    """
    for task in pending:
        waiter = getattr(task, "_fut_waiter", None)
        if not self.work.has_wake(task, waiter):
            continue
        if not _recognized_wait(self, task, waiter):
            continue
        for record in _timer_records_waking(self, task, waiter):
            real = record.real_handle
            if real is not None and not real.cancelled():
                real.cancel()


def _owned_tasks(self: _SettlementState) -> list[asyncio.Task[Any]]:
    return [task for task in self.work.tasks if not task.done()]


def _active_callbacks(self: _SettlementState, deadline: float | None = None) -> list[_CallbackRecord]:
    if deadline is None:
        self.work.reconcile(self.loop.time() if self.loop is not None else 0.0)
        return list(self.work.callback_records())
    candidates = self.work.callback_records(ready_only=self.advancing)
    active = []
    for record in candidates:
        if record.handle is None or record.handle.cancelled():
            self.work.retire(record)
        elif self.advancing or not _callback_is_parked(self, record, deadline):
            active.append(record)
    return active


def _callback_is_parked(self: _SettlementState, record: _CallbackRecord, deadline: float) -> bool:
    return record.when is not None and _callback_fire_time(self, record) > deadline


def _is_parked(self: _SettlementState, task: asyncio.Task[Any], deadline: float) -> bool:
    return _park_reason(self, task, deadline) is not None


def _park_reason(
    self: _SettlementState, task: asyncio.Task[Any], deadline: float, seen: set[int] | None = None
) -> str | None:
    if task.done():
        return "completed"
    if seen is not None and id(task) in seen:
        return None
    if task in self.reasons:
        return self.reasons[task]
    if task in self.visiting:
        return None
    self.visiting.add(task)
    try:
        reason = _compute_park_reason(self, task, deadline)
        self.reasons[task] = reason
        return reason
    finally:
        self.visiting.remove(task)


def _compute_park_reason(self: _SettlementState, task: asyncio.Task[Any], deadline: float) -> str | None:
    waiter = getattr(task, "_fut_waiter", None)
    if waiter is None or waiter.done():
        return None
    reason = self.work.external_waits.get(task)
    if reason:
        return reason
    if (waiter in self.listeners and not waiter.done()) or _dpy_internals.is_wait_for_listener(
        self.bot, task, self.listeners
    ):
        return "discord Client.wait_for listener"
    if waiter in self.views and not waiter.done():
        return "discord View/Modal completion"
    if _dpy_internals.view_expiry_task(self.bot, task, waiter, self.expiry_tasks) and _timer_records_waking(
        self, task, waiter
    ):
        return "discord View/Modal expiry timer"
    if _dpy_internals.tasks_loop_sleep(task, waiter) and _timer_records_waking(self, task, waiter):
        return "discord.ext.tasks loop interval"
    dependencies = _dpy_internals.composed_tasks(task, waiter)
    unresolved = [dependency for dependency in dependencies if not dependency.done()]
    if unresolved:
        reasons: list[str] = []
        for dependency in unresolved:
            if isinstance(dependency, asyncio.Task):
                if dependency not in self.work.tasks:
                    break
                reason = _park_reason(self, dependency, deadline)
            elif dependency in self.listeners and not dependency.done():
                reason = "discord Client.wait_for listener"
            elif dependency in self.views and not dependency.done():
                reason = "discord View/Modal completion"
            else:
                break
            if reason is None:
                break
            reasons.append(reason)
        else:
            if reasons and all(reason is not None for reason in reasons):
                return "composed external wait"
    if _sleep_waiter_beyond_deadline(self, waiter, deadline):
        return "sleep timer beyond settlement deadline"


def _settle_timeout_message(
    self: _SettlementState,
    stuck: list[asyncio.Task[Any]],
    all_pending: list[asyncio.Task[Any]],
    effective: float,
    callbacks: list[_CallbackRecord],
) -> str:
    lines = ["bot did not settle"]
    if self.last_dispatch:
        lines[0] += f" after {self.last_dispatch}"
    lines[0] += (
        f" (timeout={effective:g}s); state may already have changed and outstanding work remains tracked"
    )
    for task in stuck:
        record = self.work.tasks.get(task)
        label = record.label if record is not None else _dpy_internals.task_label(task.get_coro())
        waiter = getattr(task, "_fut_waiter", None)
        reason = self.work.external_waits.get(task)
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
        generation = record.generation if record is not None else self.generation
        lines.append(f"  bot-owned generation {generation} {label}: {reason}")
    for executor_record in self.work.executors.values():
        state = executor_record.state
        lines.append(
            f"  bot-owned generation {executor_record.generation} executor "
            f"{executor_record.label}: {state} native work"
        )
    if callbacks:
        lines.append("  bot-owned callbacks pending:")
        for record in callbacks:
            lines.append(f"    {record.label}")
    lines.append(
        "  use await env.external_wait(...) for intentional external input; "
        "use await env.advance_time(...) for virtual timers"
    )
    if self.work.external_waits and stuck:
        lines.append(
            "  external_wait is task-scoped; declare framework orchestration/lifecycle waits "
            "at their public hooks rather than assuming sibling waits are external"
        )
    if len(all_pending) > len(stuck):
        lines.append(f"  {len(all_pending) - len(stuck)} recognized waits remain parked")
    return "\n".join(lines)
