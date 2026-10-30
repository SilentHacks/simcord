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
setup/teardown. Send, edit and history dispatch samples use a newly reconstructed
world for every measured round (`iterations=1`); fixture setup is excluded.
This resets HTTP/transcript records as well as messages. History starts with
200 messages and requests the newest 50.

`test_simulation_benchmarks.py` includes a small human-command → bot-reply
journey, local TCP I/O beside six live Views, callback bursts beside parked timers,
repeated settlement of live Views, and staggered virtual callbacks. The local-I/O
scenario completes 20 request/reply exchanges before the bot replies.
Each fresh-world scenario **includes setup, completed outputs and shutdown**.
The latter three are stress/scaling evidence, not claims about typical usage.
Do not compare their scope with bare dispatch.

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
- Interleave reference/candidate batches on the same host; preserve raw samples and
  report uncertainty. Small differences on noisy shared runners are inconclusive.
- Separate the original implementation, an ownership-correct reference and the
  optimized implementation. Added isolation work does not establish that every
  observed slowdown is unavoidable.
- Profile separately. cProfile's cumulative times overlap and its instrumentation
  amplifies Python-loop costs; do not add nested times or treat them as unprofiled
  speedup measurements. Collect allocation profiles separately too.
- Measure small worlds, writes, deletion/cancellation churn and retained memory,
  not only large read-side wins. Indexes exchange extra write work and memory for
  bounded selection and scheduling.

`benchmarks/simulation_attribution.py` runs fresh-process, randomized
forward/reverse batches of plain/rich sends and edits, reply ancestry, parked
listeners with callbacks, and exact search through signed cursors. Supply frozen
source directories containing `src/simcord`; output directories must be new:

```bash
uv run python benchmarks/simulation_attribution.py \
  --original /absolute/original-source --before /absolute/pre-repair-source \
  --output /absolute/new-evidence-directory
```

Use `--smoke` for small behavioral qualification. Allocation (`--instrument
allocation`) and profiling (`--instrument profile`) are separate runs. Raw files
include source hashes, dependencies, construction, batch order and completed
outputs. The correctness-only source overlay restores original search, wake and
index work and always creates independent history/delivery graphs. It shares the
stack-safe traversal repair with the candidate, so that traversal's efficiency
cannot be isolated as a pure correctness cost. Search timings cover Python
navigation, not an HTTP request or a full Preview snapshot. Individual first-page
and all-pages query spans are retained alongside complete-world timings.

## What remains real work

Settlement joins runnable bot-owned work and actual executor completion. Timeout
values are ceilings, not fixed sleeps; reducing them can change which work is
joined or fail an operation rather than optimize its CPU cost. Declared external
waits and recognized SDK waits retain their virtual-time behavior.

Wait virtualization consults the existing task/waiter wake index before recognizing
SDK waits: a task with no indexed wake cannot have a real timer fallback to cancel.
Normal parking classification still runs. This avoids redundant View/listener
discovery without changing settlement completion, timeouts or callback fairness;
task/composition classification across successive settlement turns remains real work.

Settlement drains owned progress across event-loop turns before repeating SDK
wait classification. It still checks the settlement deadline during continuous
progress and requires stable completion; it does not shorten timeouts or return
before the bot's I/O and callbacks complete. View discovery reads the lifecycle
fields verified against the supported SDK instead of scanning every attribute
on every View. There is no persistent wait cache or extra scheduling index.

Callback registration, invocation-before-publication and retirement share one
short per-Env reentrant lock. Settlement snapshots the relevant records under
that lock and releases it before running callbacks; user code and error reporting
do not run inside it. This preserves native executor scheduling without a new
scheduler or a process-wide callback lock.

Automatic Poll expiry remains deadline-indexed. Explicitly expiring a backend
Poll shared by multiple messages scans live message aliases and retires their
deadlines before publishing the finalization. This rare explicit path is O(history),
not an extra index or a claim that normal SDK sends share Poll objects.

Full-history reads and semantically exact text search remain proportional to
eligible input/output. Preview's default navigation should select bounded rows
before projecting them; text search still honors rendered labels, spoilers,
Unicode and authorization. Required detached request-log, gateway and returned
SDK payload copies protect isolation and must not be removed to improve a score.

Text navigation seeks through the existing numeric message indexes and stops once
the requested page and its boundary witnesses are known. Frequent matches can
avoid most summaries; sparse or absent matches can still require a full eligible
history scan. Forward/backward cursor and live authorization rules are unchanged.

Preview skips mention/emoji discovery and command-map construction when none of a
message's relevant texts contains `<`. The conservative check includes embeds,
forward snapshots and text-display components; possible syntax still takes the
normal path, including mention inventories inside code and escaped text.
`@everyone` behavior and live authorization filtering are unchanged. This removes
context preparation, not exact-search scans, and introduces no persistent cache.

Live command-catalog validation uses the same schema fingerprint as publication.
Only already-published keys are hashed, without rebuilding their wire option
trees. Visibility enumeration and command-leaf traversal can still inspect the
full current inventory. Live permission/schema changes remove stale entries;
newly visible commands still require refresh before publication.

REST/SDK response payloads, retained request/event history and live subscriber
envelopes have independent nested mutable data. This isolation costs CPU and
memory; synchronous subscribers still share their live envelope in registration
order. Input detachment finishes before edit/upload side effects. A later event
snapshot or subscriber failure can occur after state commits: inspect the state
and settle/refresh rather than replaying the action.

Plain wire dict/list graphs are detached iteratively, preserving aliases and
cycles without lowering the functioning reply-ancestry depth through recursive
copying. A graph containing custom/non-wire values falls back as a whole to
standard-library `deepcopy`, retaining its hooks, recursion limits and failures.
Subscriber-free emission retains one independent snapshot rather than also
allocating an unused delivery graph. Complete recursive ancestry serialization
and SDK parsing still have their own limits; there is no arbitrary-depth promise.

AutoMod create/edit inputs and serialized outputs are independently detached,
including nested metadata and action data. Each live rule lazily retains compiled
patterns only for keywords reached during ordered matching. A current keyword
tuple detects explicit and internal in-place changes; preparation is replaced,
not accumulated across revisions or stored in a global rule cache.

This avoids repeated compilation after unrelated regex-cache churn, but does
not eliminate matching work: every evaluation creates and compares an O(K)
keyword signature. A cold clean/late-hit message can still compile the whole
visited inventory, and retained pattern memory grows with visited keywords until
the rule changes or is deleted. Cold first-keyword hits do not compile later
keywords. No-rule and small-rule workloads should be measured alongside large
inventories; this is not a universal end-to-end speedup.

The synchronous send guards retain a generous absolute budget and compare send
latency in 100- and 10,000-message channels. They are useful catastrophe guards,
not proof that timer settlement, projections, memory or every end-to-end operation
is fast. Behavioral/conformance checks must pass alongside performance evidence.
