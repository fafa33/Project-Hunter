from __future__ import annotations

from dataclasses import dataclass

from hunter.automation.models import AutomationEvent, AutomationJob, AutomationRun


@dataclass(frozen=True)
class AutomationStatus:
    jobs: tuple[AutomationJob, ...]
    runs: tuple[AutomationRun, ...]
    events: tuple[AutomationEvent, ...]

    @property
    def active_runs(self) -> tuple[AutomationRun, ...]:
        return tuple(run for run in self.runs if run.status in {"scheduled", "claimed", "running"})
