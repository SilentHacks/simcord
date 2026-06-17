"""The performance gate: robust assertions that survive noisy CI runners.

Two complementary guards, chosen to avoid the flakiness of wall-clock thresholds
on shared runners:

* **Generous absolute budget** — ~100x typical in-memory latency, so it only trips
  on a *catastrophe* (a real network call sneaking in, an accidental O(n^2)), never
  on normal jitter.
* **Same-run scaling ratio** — sending a message must stay O(1) in channel size;
  comparing a small vs a large channel *in the same run* cancels runner speed, so
  an algorithmic regression is caught without a brittle absolute number.

These need no committed baseline (deliberately — %-regression gating proved too
flaky across runner types); ``test_dispatch_benchmarks.py`` carries the trend.
"""

from __future__ import annotations

import time
from statistics import median

from _helpers import backend_with_channel

from simcord.http.router import dispatch

_SEND_BUDGET_S = 0.005  # 5 ms; in-memory dispatch is microseconds, so this is huge headroom


def _median_time(fn: object, repeats: int = 200) -> float:
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()  # type: ignore[operator]
        samples.append(time.perf_counter() - start)
    return median(samples)


def test_send_dispatch_within_budget() -> None:
    """A single message-send round-trip stays far under the catastrophe budget."""
    backend, _gid, channel_id = backend_with_channel(50)
    path = f"/channels/{channel_id}/messages"
    elapsed = _median_time(lambda: dispatch(backend, "POST", path, json={"content": "hi"}))
    assert elapsed < _SEND_BUDGET_S, (
        f"median send dispatch {elapsed * 1000:.3f}ms exceeds {_SEND_BUDGET_S * 1000:.0f}ms"
    )


def test_send_latency_independent_of_channel_size() -> None:
    """Sending is O(1) in channel size — a 100x bigger channel is not 100x slower."""
    small_backend, _sg, small_channel = backend_with_channel(100)
    large_backend, _lg, large_channel = backend_with_channel(10_000)
    small = _median_time(
        lambda: dispatch(small_backend, "POST", f"/channels/{small_channel}/messages", json={"content": "x"})
    )
    large = _median_time(
        lambda: dispatch(large_backend, "POST", f"/channels/{large_channel}/messages", json={"content": "x"})
    )
    # Allow 3x headroom plus an epsilon so microsecond-scale noise can't trip it.
    assert large <= small * 3 + 1e-4, (
        f"send latency grows with channel size: {small * 1e6:.1f}us (100 msgs) -> {large * 1e6:.1f}us (10k msgs)"
    )
