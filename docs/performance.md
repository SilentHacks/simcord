---
title: "Performance"
description: "Measure complete simulation operations, CPU, latency and scaling without sacrificing deterministic completion."
---

# Performance

SimCord runs offline: REST requests are routed in memory and gateway payloads
enter real discord.py parsers. No Discord socket, token, network retry or backoff
is involved. This removes network latency; it does not make every complete bot
operation a constant-time dictionary lookup.

## Measure the operation the test actually performs

An actor operation includes bot callbacks and settlement. A bare REST dispatch
benchmark excludes that work. Likewise, returning all history must materialize
all requested messages, while `last_message` should select and materialize only
the final visible message.

The reported suite covers synchronous send/edit/history and full empty Env
setup/teardown. `test_simulation_benchmarks.py` adds complete public scenarios:
callback bursts beside parked timers, repeated settlement of live Views, and
staggered virtual callbacks. Those scenario timings **include world setup and
teardown**; do not compare them with bare dispatch as if the scopes were equal.

```bash
uv run --extra bench pytest benchmarks --benchmark-only
uv run --extra bench pytest benchmarks --benchmark-json=benchmark.json
```

The second command also runs the existing catastrophe/scaling guards. CI uploads
the JSON as a trend artifact. Results depend on Python, discord.py, machine,
fixture sizes, and the bot's work; there is no universal sub-millisecond Env
startup or actor-operation guarantee.

## Compare changes honestly

- Freeze the complete reference source and dependency environment. Confirm which
  source tree each worker imports, especially with editable installations.
- Use equivalent seeded worlds and completed outputs. Reset mutable fixtures
  between samples rather than letting messages accumulate unnoticed.
- Separate cold import, setup, steady-state operations and teardown. Include
  end-to-end scenarios as well as focused diagnostic probes.
- Report wall-clock latency and process CPU separately using `perf_counter()`
  and `process_time()`. SDK-selected monotonic clocks can be virtualized.
- Repeat reference/candidate runs on the same host; preserve raw samples and
  report uncertainty. Small differences on noisy shared runners are inconclusive.
- Profile separately. cProfile's cumulative times overlap and its instrumentation
  amplifies Python-loop costs; do not add nested times or treat them as unprofiled
  speedup measurements. Collect allocation profiles separately too.
- Measure small worlds, writes, deletion/cancellation churn and retained memory,
  not only large read-side wins. Indexes exchange extra write work and memory for
  bounded selection and scheduling.

## What remains real work

Settlement joins runnable bot-owned work and actual executor completion. Timeout
values are ceilings, not fixed sleeps; reducing them can change which work is
joined or fail an operation rather than optimize its CPU cost. Declared external
waits and recognized SDK waits retain their virtual-time behavior.

Full-history reads and semantically exact text search remain proportional to
eligible input/output. Preview's default navigation should select bounded rows
before projecting them; text search still honors rendered labels, spoilers,
Unicode and authorization. Required detached request-log, gateway and returned
SDK payload copies protect isolation and must not be removed to improve a score.

The synchronous send guards retain a generous absolute budget and compare send
latency in 100- and 10,000-message channels. They are useful catastrophe guards,
not proof that timer settlement, projections, memory or every end-to-end operation
is fast. Behavioral/conformance checks must pass alongside performance evidence.
