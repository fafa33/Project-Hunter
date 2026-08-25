from __future__ import annotations

from dataclasses import dataclass
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone as _tz
    UTC = _tz.utc
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        raise NotImplementedError


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class FixedClock:
    fixed_at: datetime

    def now(self) -> datetime:
        if self.fixed_at.tzinfo is None:
            return self.fixed_at.replace(tzinfo=UTC)
        return self.fixed_at.astimezone(UTC)
