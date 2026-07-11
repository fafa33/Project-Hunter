from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from hunter.automation.models import AutomationEvent, AutomationJob, is_due
from hunter.automation.runner import AutomationJobRunner
from hunter.automation.status import AutomationStatus


class AutomationScheduler:
    def __init__(self, jobs: tuple[AutomationJob, ...], runner: AutomationJobRunner | None = None, *, clock: Any | None = None) -> None:
        self.jobs = jobs
        self.runner = runner or AutomationJobRunner(clock=clock)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.events: list[AutomationEvent] = []
        self.started = False

    def start(self) -> None:
        self.started = True
        self._event("scheduler_started", "scheduler", None, "scheduler started")

    def stop(self) -> None:
        self.started = False
        self._event("scheduler_stopped", "scheduler", None, "scheduler stopped")

    def due_jobs(self, *, at: datetime | None = None) -> tuple[AutomationJob, ...]:
        now = at or self.clock()
        return tuple(job for job in self.jobs if job.enabled and is_due(job.schedule, at=now, timezone=job.timezone))

    def run_due(self, *, at: datetime | None = None) -> tuple[object, ...]:
        now = at or self.clock()
        runs = []
        for job in self.jobs:
            if not job.enabled:
                self._event("job_skipped", job.job_id, None, "job disabled")
                continue
            if is_due(job.schedule, at=now, timezone=job.timezone):
                runs.append(self.runner.run_once(job, scheduled_for=now))
        return tuple(runs)

    def recover(self) -> None:
        self._event("scheduler_started", "scheduler", None, "scheduler recovered")

    def status(self) -> AutomationStatus:
        return AutomationStatus(jobs=self.jobs, runs=tuple(self.runner.runs), events=tuple([*self.events, *self.runner.events]))

    def _event(self, event_type: str, job_id: str, run_id: str | None, detail: str) -> None:
        self.events.append(AutomationEvent(event_type=event_type, job_id=job_id, automation_run_id=run_id, at=self.clock(), detail=detail))
