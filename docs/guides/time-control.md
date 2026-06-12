---
title: "Time control"
description: "Fast-forward time in discord.py tests with SimCord's env.advance_time(). Fire View timeouts, reset command cooldowns and drain asyncio.sleep chains instantly — no real waiting, fully deterministic."
---

# Time control

Time-dependent behaviour — `View` timeouts, command cooldowns, `asyncio.sleep`-based delays
— is normally painful to test: you either wait in real time (slow, flaky) or mock the clock
by hand (fiddly, leaky). SimCord gives you a **virtual clock** you fast-forward in one call.

## `env.advance_time(seconds)`

```python
result = await alice.slash(channel, "offer")   # the command posts a View(timeout=180)
await simcord_env.advance_time(180)            # instant — the view times out
assert "expired" in channel.last_message.content
```

`advance_time` moves the virtual clock forward by `seconds` and fires **every timer that
becomes due** along the way, settling the bot's reactions after each one. No real time
passes.

## What it fires

Anything the bot scheduled against the loop's clock:

- **`View` timeouts** — `on_timeout` runs, and whatever it does (editing the message to
  disable buttons, posting an "expired" notice) happens.
- **Command cooldowns** — `@commands.cooldown(...)` / `@app_commands.checks.cooldown(...)`
  buckets reset, so a second invocation is allowed again.
- **`asyncio.sleep` chains** — delays inside handlers complete. Timers fire **in order**: a
  chain of three 60-second sleeps all complete within `advance_time(180)`.

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

Both clocks advance together. The event-loop clock (what `asyncio` timers and discord.py
deadlines read) **and** the backend's wall clock (what message/interaction timestamps and
cooldown age math derive from) move by the same amount. So a message's `created_at` and its
`timestamp` always agree, and everything discord.py computes from time stays coherent —
there's no skew to trip over in assertions.

!!! tip "Stepwise vs. one jump"
    `advance_time` consumes timers in order, settling between each, so intermediate effects
    actually happen. If a handler sleeps 30s, posts, then sleeps another 30s and posts
    again, a single `advance_time(60)` produces **both** posts in sequence — not just the
    final state.

## Determinism

Snowflake IDs and timestamps come from a fixed virtual epoch, so they're identical across
runs. Combined with no network and no real sleeps, your time-based tests are fully
reproducible — the same inputs always produce the same IDs, timestamps and ordering.

## Next

- [Components & modals](components.md) — firing `View` timeouts after posting components.
- [Slash commands](interactions.md) — app-command cooldowns.
- [Recipes](../cookbook.md) — a complete timeout-handling test.
