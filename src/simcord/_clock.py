"""A backend-owned clock, independent of event loops and environments."""

from datetime import UTC, datetime, timedelta

_DISCORD_EPOCH = datetime(2015, 1, 1, tzinfo=UTC)
_END_START = _DISCORD_EPOCH + timedelta(milliseconds=1 << 42)


class Clock:
    def __init__(self, start_time: datetime | None = None) -> None:
        from .backend.errors import SetupError

        if start_time is None:
            start_time = datetime(2026, 1, 1, tzinfo=UTC)
        if not isinstance(start_time, datetime):
            raise SetupError("start_time must be an aware datetime")
        try:
            if start_time.utcoffset() is None:
                raise SetupError("start_time must be an aware datetime")
            start_time = start_time.astimezone(UTC)
        except (OverflowError, ValueError, TypeError) as exc:
            raise SetupError("start_time must normalize to a supported UTC datetime") from exc
        if not _DISCORD_EPOCH <= start_time < _END_START:
            raise SetupError(
                "start_time must be at least 2015-01-01T00:00:00+00:00 "
                "and before 2154-05-15T07:35:11.104+00:00"
            )
        self.elapsed = 0.0
        self._epoch = start_time

    def now(self) -> datetime:
        return self._epoch + timedelta(seconds=self.elapsed)

    def advance(self, seconds: float) -> None:
        self.elapsed += seconds
