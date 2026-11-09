---
title: "Time control"
description: "Fast-forward time in discord.py tests with SimCord's env.advance_time(). Fire View timeouts, reset command cooldowns and drain asyncio.sleep chains instantly — no real waiting, fully deterministic."
---

# Time control

Time-dependent behaviour — `View` timeouts, command cooldowns, `asyncio.sleep`-based delays
— is normally painful to test: you either wait in real time (slow, flaky) or mock the clock
by hand (fiddly, leaky). SimCord gives you a **virtual clock** you fast-forward in one call.

## Choosing the initial UTC instant

`Env(bot, start_time=...)` and `simcord.run(bot, start_time=...)` accept an aware
`datetime`. The default remains **2026-01-01 00:00:00 UTC**. Offsets normalize to UTC;
naive datetimes, non-datetimes and unsupported instants raise `SetupError` before backend
IDs or runtime hooks are created. The supported initial UTC range is
**2015-01-01 00:00:00 inclusive through 2154-05-15 07:35:11.104 exclusive**.
The origin is fixed at construction, already applies to pre-start fixtures and backend
timestamps, and survives bot restarts along with elapsed virtual time. There is no rewind
or `set_time` operation.

Bind your application's existing clock seam **before** startup so `setup_hook` and
fixture writes use the same clock. For example, a custom pytest fixture can use manual
lifecycle control:

```python
from datetime import UTC, datetime

import pytest_asyncio
import simcord

@pytest_asyncio.fixture
async def world():
    bot = create_bot()
    env = simcord.Env(bot, start_time=datetime(2030, 4, 5, 12, tzinfo=UTC))
    bot.service.now = env.now  # application-owned callable used by startup and writes
    await env.start()
    try:
        yield env
    finally:
        await env.shutdown()
```

The standard pytest fixture also accepts
`@pytest.mark.simcord(start_time=datetime(2030, 4, 5, tzinfo=UTC))`.
Use a manual fixture when application wiring must happen before `env.start()`.
Selecting an initial origin does **not** synchronize Python wall time, SQL
`CURRENT_TIMESTAMP`/`now()`, database defaults or application clocks. Inject `env.now`
through existing application time seams, including relevant persisted writes, rather
than globally monkeypatching datetime, SQL or SDK functions.

## `env.advance_time(seconds)`

```python
result = await alice.slash(channel, "offer")   # the command posts a View(timeout=180)
await simcord_env.advance_time(180)            # instant — the view times out
assert "expired" in channel.last_message.content
```

`advance_time` moves the shared UTC/monotonic clock through **every resource deadline and
bot timer** on the way, settling reactions at each intermediate instant. Resources are
processed before bot timers tied at the same instant. There is one absolute real
`settle_timeout` budget for the whole advance, including initial settlement.

## What it fires

Anything the bot scheduled against the loop's clock:

- **`View` timeouts** — `on_timeout` runs, and whatever it does (editing the message to
  disable buttons, posting an "expired" notice) happens.
- **Command cooldowns** — `@commands.cooldown(...)` / `@app_commands.checks.cooldown(...)`
  buckets reset, so a second invocation is allowed again.
- **`asyncio.sleep` chains** — delays inside handlers complete. Timers fire **in order**: a
  chain of three 60-second sleeps all complete within `advance_time(180)`.
- **Resource deadlines** — polls, scheduled events and explicitly scheduled entitlement ends.
- **Supported SDK UTC helpers** — entitlement expiry, `discord.utils.sleep_until`, and
  relative/calendar `discord.ext.tasks` loops use the selected virtual clock.
- **Typing refresh intervals** — the native SDK typing task's direct five-second sleep
  is virtual-only. Its initial HTTP request and every refresh still execute and join
  normally; HTTP waits and retries are not treated as parked typing intervals.

```python
async def test_cooldown_resets(simcord_env):
    ...
    await alice.send(channel, "!daily")
    second = await alice.send(channel, "!daily")        # on cooldown
    assert "wait" in channel.last_message.content.lower()

    await simcord_env.advance_time(60 * 60 * 24)         # a day passes, instantly
    await alice.send(channel, "!daily")
    assert "claimed" in channel.last_message.content.lower()
```

## Why it's consistent

`env.now()` returns aware UTC. Backend timestamps, resource transitions and tracked bot
timers see each intermediate instant, not the final requested horizon prematurely.
UTC preserves microseconds and fractional advances accumulate rather than truncating
each step to milliseconds. Zero/past-due bot timers are scheduled on the next
representable UTC tick (one microsecond), so the SDK's calendar clock-drift correction
can make progress without changing its algorithm. A calendar callback may therefore
observe its boundary plus one microsecond.

Direct `async with simcord.run(...)` selects SDK virtual UTC in the driver context.
The pytest plugin selects the unique running Env during the actual test call, including
aliased fixtures. With manual `Env.start()` / `shutdown()` use:

```python
with env.time_context():
    assert discord.utils.utcnow() == env.now()
```

Bot-owned tasks and executor work inherit clock ownership; stale contexts cannot
reactivate a closed generation. Unrelated loops/contexts keep real clocks. There is no
latest-Env fallback, global datetime patch, or general application-time virtualization.
Inject `env.now` into your application's own clock seam. Only one live Env per event
loop is supported; a second start rejects before altering hooks.

Executor ownership follows actual execution, not the caller's Future. Native
cancellation that removes a queued job prevents execution; cancelling a shielded
or non-forwarding facade does not. Cancelling a running job never claims that its
thread stopped. Work it enqueues before completion retains bot ownership.

While Envs are active, paired, reference-counted hooks observe owned submissions
through the standard `ThreadPoolExecutor.submit` and `ProcessPoolExecutor.submit`
APIs, including interpreter pools where available. Unrelated submissions retain
their original behavior; SimCord neither replaces nor shuts down supplied
executors. Native rejection, initializer failure and completion retire the
actual job independently of the facade. Isolated process/interpreter pools
receive only the original picklable callable, not an Env or its in-process state.
The hooks are restored when the last Env detaches.

SimCord preserves the original executor hook and caller Future. Custom hooks
should forward the submitted callable unchanged to the native executor. An
opaque callable adapter can hide pre-entry cancellation or rejection, leaving
ownership unresolved and timing out rather than guessing that no work remains.
A custom hook can also hide later asynchronous post-processing behind an
already-completed facade; settlement cannot join that invisible work. The hook
must expose or await its own completion. Captured callback context is preserved
when later work is scheduled.

A shutdown timeout while draining native work retains the gateway, shards and
webhook adapter until a later successful drain. The attempt may already have
cancelled asynchronous tasks: it is not a rollback. Release outstanding work
and retry shutdown.

!!! tip "Stepwise vs. one jump"
    `advance_time` consumes timers in order, settling between each, so intermediate effects
    actually happen. If a handler sleeps 30s, posts, then sleeps another 30s and posts
    again, a single `advance_time(60)` produces **both** posts in sequence — not just the
    final state.

## Determinism

Snowflake IDs and UTC start from the configured origin (the fixed default is unchanged).
ID allocation does not advance time: IDs use a sequence within the current millisecond
and explicit IDs are reserved against collisions. Exhausting the sequence fails instead
of silently moving UTC.
Shard-pinned guild fixtures can use an earlier timestamp so they route to the requested
shard without time travel. Their `created_at` can precede arrangement time, and fixture
creation order need not match numeric ID order. Normal snowflake timestamps have
millisecond precision, while backend UTC retains microseconds.
At the Discord epoch, allocation skips ID zero. A shard request requiring a timestamp
before that epoch raises `SetupError` without reserving IDs. Allocation beyond the
64-bit snowflake timestamp range also rejects rather than generating an invalid ID.
Settlement joins runnable work before and after each timer step. Deadline timers behind
recognized waits — `wait_for` timeouts on listener waits, View/Modal expiry, `ext.tasks`
loop intervals and wake-up timers scheduled inside `env.external_wait` — never fire on the
real clock: drive them with `advance_time()` (or the awaited input). A far-future sleep is
recognized only when its live loop timer resolves the awaited future; unrelated or cancelled
timers do not make an unknown wait look parked. `advance_time()` is itself exclusive with
other public operations and rejects invalid or negative durations.

Unrecognized waits can have real-due timers handled by the automatic coordinator. That
fallback advances through its due horizon even when an unrelated input remains blocked,
still processing resource deadlines before tied bot timers. It does not declare the
blocked input settled, relax executor ownership or suppress settlement failures.
Recognized intervals such as typing refresh sleeps remain virtual-only. Explicit
`advance_time()` still uses strict settlement and reports committed partial progress on
failure.

If an advance times out, earlier transitions remain committed. The exception notes
report requested and reached UTC instants. Resolve the outstanding work and advance
only the remaining duration; do not repeat the original duration or replay an action.
An automatic real-timer coordinator's undelivered timeout is retained until a public
settlement receives it; a later timer cannot silently replace the failure. After
acknowledging it and resolving blocked work, settle again to continue.

## Next

- [Components & modals](components.md) — firing `View` timeouts after posting components.
- [Slash commands](interactions.md) — app-command cooldowns.
- [Recipes](../cookbook.md) — a complete timeout-handling test.
