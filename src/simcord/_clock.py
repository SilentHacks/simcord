"""A backend-owned clock, independent of event loops and environments."""

from datetime import UTC, datetime, timedelta


class Clock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self._epoch = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._epoch + timedelta(seconds=self.elapsed)

    def advance(self, seconds: float) -> None:
        self.elapsed += seconds
