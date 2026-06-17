---
title: "Performance"
description: "Why SimCord is fast — the in-memory hot path, indicative numbers, and the CI guard that keeps it that way."
---

# Performance

SimCord's value proposition is that bot tests run **fast and offline**: no network,
no real gateway, no sleeps. This page makes that concrete and explains the guard
that keeps it true.

## Why it is fast

A REST call a bot makes never leaves the process. `discord.py`'s HTTP client is
replaced by an in-memory transport that calls a synchronous router, which mutates
a plain-dictionary backend and serialises the reply — no sockets, no event loop
hop, no retry/backoff. The whole request hot path is `router.dispatch(backend, ...)`.

## Indicative numbers

Measured on the benchmark suite (`benchmarks/`, run with `pytest-benchmark`). These
are order-of-magnitude figures on a developer laptop — your machine will differ,
but the shape (microseconds per call, well under a millisecond to spin up an `Env`)
holds:

| Operation | Median |
| --- | --- |
| Send a message (`POST .../messages`) | ~40 µs |
| Edit a message (`PATCH .../messages/{id}`) | ~25 µs |
| Read 50 messages of history | ~175 µs |
| Full `async with simcord.run(bot)` setup + teardown | <1 ms |

Reproduce them yourself:

```bash
uv run --extra bench pytest benchmarks --benchmark-only
```

## How the guard works

Wall-clock benchmarks are noisy on shared CI runners, so the build is **not** gated
on a percentage regression against a committed baseline (that proved too flaky).
Instead two robust checks run in CI (`benchmarks/test_perf_guards.py`):

- **A generous absolute budget** — a single send dispatch must stay far under
  5 ms. In-memory dispatch is microseconds, so this only trips on a *catastrophe*:
  a real network call sneaking in, or an accidental quadratic blow-up.
- **A same-run scaling ratio** — sending a message into a 10,000-message channel
  must not be meaningfully slower than into a 100-message one. Comparing the two
  *in the same run* cancels out runner speed, so an algorithmic regression (a hot
  path that grows with world size) is caught without a brittle absolute number.

The raw benchmark JSON is uploaded as a CI artifact on every run, so trends are
visible over time without gating on them.
