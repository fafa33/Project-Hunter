from __future__ import annotations

from dataclasses import dataclass

from hunter.acquisition.models import AcquisitionRequest


@dataclass(frozen=True)
class AcquisitionSchedule:
    requests: tuple[AcquisitionRequest, ...] = ()


class AcquisitionScheduler:
    def due(self, schedule: AcquisitionSchedule) -> tuple[AcquisitionRequest, ...]:
        return tuple(sorted(schedule.requests, key=lambda item: (item.requested_at, item.domain, item.metric)))
