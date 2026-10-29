"""Revisioned metadata-only deadlines used by the three resource families."""

from __future__ import annotations

import datetime
import heapq
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

Error = tuple[type[Exception], tuple[Any, ...]]


@dataclass(eq=False, slots=True)
class _DueBatch:
    now: datetime.datetime
    pending: list[tuple[tuple[int, ...], int, Any]] = field(default_factory=list)
    cursor: tuple[int, ...] = ()


class Deadlines:
    def __init__(self) -> None:
        self._heap: list[tuple[datetime.datetime, int, Any]] = []
        self._live: dict[Any, tuple[int, datetime.datetime | Error, tuple[int, ...]]] = {}
        self._errors: dict[Any, Error] = {}
        self._naive: set[Any] = set()
        self._process_only_errors: set[Any] = set()
        self._revision = 0
        self._batches: list[_DueBatch] = []

    def set(
        self,
        key: Any,
        value: datetime.datetime | Exception | None,
        order: tuple[int, ...],
        *,
        process_only_error: bool = False,
    ) -> None:
        self._revision += 1
        self._live.pop(key, None)
        self._errors.pop(key, None)
        self._naive.discard(key)
        self._process_only_errors.discard(key)
        if value is not None:
            if isinstance(value, Exception):
                error = (type(value), value.args)
                self._live[key] = (self._revision, error, order)
                self._errors[key] = error
                if process_only_error:
                    self._process_only_errors.add(key)
            else:
                self._live[key] = (self._revision, value, order)
                if value.tzinfo is None:
                    self._naive.add(key)
                heapq.heappush(self._heap, (self._sort_time(value), self._revision, key))
            for batch in self._batches:
                if order > batch.cursor and (
                    isinstance(value, Exception) or value.tzinfo is None or value <= batch.now
                ):
                    heapq.heappush(batch.pending, (order, self._revision, key))
                    if len(batch.pending) > 2 * len(self._live) + 64:
                        batch.pending = [
                            entry
                            for entry in batch.pending
                            if (live := self._live.get(entry[2])) is not None and live[0] == entry[1]
                        ]
                        heapq.heapify(batch.pending)
        if len(self._heap) > 2 * len(self._live) + 64:
            self._heap = [
                (self._sort_time(value), revision, key)
                for key, (revision, value, _) in self._live.items()
                if isinstance(value, datetime.datetime)
            ]
            heapq.heapify(self._heap)

    @staticmethod
    def _sort_time(value: datetime.datetime) -> datetime.datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=datetime.UTC)

    def _trim(self) -> None:
        while self._heap:
            _, revision, key = self._heap[0]
            if key in self._live and self._live[key][0] == revision:
                break
            heapq.heappop(self._heap)

    def next(self) -> datetime.datetime | None:
        errors = self._errors.keys() - self._process_only_errors
        mixed_clocks = self._naive and len(self._naive) != len(self._live) - len(self._errors)
        if errors or mixed_clocks:
            # ponytail: O(n log n) exceptional metadata replay; index traversal
            # order separately only if invalid-state lookup becomes a real workload.
            earliest: datetime.datetime | None = None
            for key, (_, value, _) in sorted(self._live.items(), key=lambda item: item[1][2]):
                if isinstance(value, datetime.datetime):
                    if earliest is None or value < earliest:
                        earliest = value
                elif key not in self._process_only_errors:
                    error_type, args = value
                    raise error_type(*args)
        self._trim()
        if not self._heap:
            return None
        value = self._live[self._heap[0][2]][1]
        assert isinstance(value, datetime.datetime)
        return value

    def _due_keys(self, now: datetime.datetime) -> set[Any]:
        selected = set(self._errors) | self._naive
        popped = []
        self._trim()
        while self._heap and self._heap[0][0] <= now:
            entry = heapq.heappop(self._heap)
            _, revision, key = entry
            if key in self._live and self._live[key][0] == revision:
                selected.add(key)
                popped.append(entry)
            self._trim()
        # Do not consume schedules before their canonical mutation succeeds.
        # Exceptions and reentrant callbacks cannot drop unprocessed resources.
        for entry in popped:
            heapq.heappush(self._heap, entry)
        return selected

    @contextmanager
    def due(self, now: datetime.datetime) -> Iterator[Iterator[Any]]:
        batch = _DueBatch(now)
        for key in self._due_keys(now):
            revision, _, order = self._live[key]
            heapq.heappush(batch.pending, (order, revision, key))
        self._batches.append(batch)
        try:
            yield self._drain(batch)
        finally:
            self._batches.remove(batch)

    def _drain(self, batch: _DueBatch) -> Iterator[Any]:
        while batch.pending:
            order, revision, key = heapq.heappop(batch.pending)
            live = self._live.get(key)
            if live is not None and live[0] == revision and order > batch.cursor:
                batch.cursor = order
                yield key


def parse_deadline(value: Any, *, naive_utc: bool = False) -> datetime.datetime | Exception:
    try:
        moment = datetime.datetime.fromisoformat(value)
        if naive_utc and moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.UTC)
        return moment
    except (TypeError, ValueError) as exc:
        return exc
