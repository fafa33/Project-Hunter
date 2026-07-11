from __future__ import annotations

from datetime import UTC, datetime
from time import sleep

from hunter.automation.contracts import ClockFn
from hunter.automation.lifecycle import transition
from hunter.automation.models import AutomationEvent, AutomationJob, AutomationRun, is_due
from hunter.automation.persistence import run_from_record
from hunter.automation.runner import AutomationJobRunner
from hunter.automation.status import AutomationStatus
from hunter.persistence.models import QueryFilter, QuerySpec


class AutomationScheduler:
    def __init__(
        self,
        jobs: tuple[AutomationJob, ...],
        runner: AutomationJobRunner | None = None,
        *,
        polling_interval_seconds: int = 60,
        clock: ClockFn | None = None,
    ) -> None:
        self.jobs = jobs
        self.runner = runner or AutomationJobRunner(clock=clock)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.polling_interval_seconds = polling_interval_seconds
        self.events: list[AutomationEvent] = []
        self.started = False

    def start(self, *, recover: bool = True) -> None:
        self.started = True
        self._event("scheduler_started", "scheduler", None, "scheduler started")
        if recover:
            self.recover()

    def stop(self) -> None:
        self.started = False
        self._event("scheduler_stopped", "scheduler", None, "scheduler stopped")

    def due_jobs(self, *, at: datetime | None = None) -> tuple[AutomationJob, ...]:
        now = at or self.clock()
        return tuple(
            job
            for job in self.jobs
            if job.enabled
            and is_due(job.schedule, at=now, timezone=job.timezone)
            and not self._completed_one_time(job)
        )

    def run_due(self, *, at: datetime | None = None) -> tuple[AutomationRun, ...]:
        now = at or self.clock()
        runs: list[AutomationRun] = []
        for job in self.jobs:
            if not job.enabled:
                self._event("job_skipped", job.job_id, None, "job disabled")
                continue
            if self._completed_one_time(job):
                self._event("job_skipped", job.job_id, None, "one-time job already completed")
                continue
            if is_due(job.schedule, at=now, timezone=job.timezone):
                runs.append(self.runner.run_once(job, scheduled_for=now))
        return tuple(runs)

    def run_loop(self, *, max_iterations: int | None = None) -> None:
        iterations = 0
        if not self.started:
            self.start()
        while self.started and (max_iterations is None or iterations < max_iterations):
            self.run_due()
            iterations += 1
            if self.started and (max_iterations is None or iterations < max_iterations):
                sleep(self.polling_interval_seconds)

    def recover(self) -> None:
        self._event("scheduler_started", "scheduler", None, "scheduler recovered")
        if self.runner.repositories is None:
            return
        records = self.runner.repositories.automation_runs().query(
            QuerySpec(record_kind="automation-run", filters=(), sort_by="created_at", direction="asc")
        )
        for record in records:
            run = run_from_record(record)
            if run.status not in {"scheduled", "claimed", "running"}:
                continue
            job = self._job(run.job_id)
            if job is None:
                recovered = transition(run, "blocked", at=self.clock(), warning="job definition unavailable during recovery")
                self._event("job_blocked", run.job_id, run.automation_run_id, "job definition unavailable during recovery")
            elif run.status == "scheduled":
                recovered = transition(run, "blocked", at=self.clock(), warning="scheduled run blocked during recovery")
                self._event("job_blocked", job.job_id, run.automation_run_id, "scheduled run blocked during recovery")
            else:
                if self.runner.lock.locked(job.lock_key()):
                    self.runner.lock.release(job.lock_key())
                    self._event("lock_released", job.job_id, run.automation_run_id, "stale lock released")
                recovered = transition(run, "failed", at=self.clock(), error="RestartRecoveryError: active run abandoned during restart")
                self._event("job_failed", job.job_id, run.automation_run_id, "active run failed during restart recovery")
            self.runner._save_run(recovered, recovered.scheduled_for)
            self.runner.runs.append(recovered)

    def status(self) -> AutomationStatus:
        return AutomationStatus(jobs=self.jobs, runs=tuple(self.runner.runs), events=tuple([*self.events, *self.runner.events]))

    def _event(self, event_type: str, job_id: str, run_id: str | None, detail: str) -> None:
        self.events.append(AutomationEvent(event_type=event_type, job_id=job_id, automation_run_id=run_id, at=self.clock(), detail=detail))

    def _job(self, job_id: str) -> AutomationJob | None:
        for job in self.jobs:
            if job.job_id == job_id:
                return job
        return None

    def _completed_one_time(self, job: AutomationJob) -> bool:
        if job.schedule.schedule_type != "once" or self.runner.repositories is None:
            return False
        records = self.runner.repositories.automation_runs().query(
            QuerySpec(record_kind="automation-run", filters=(QueryFilter("job_id", job.job_id),), sort_by="created_at", direction="desc")
        )
        return any(record.status in {"succeeded", "partial", "failed", "cancelled"} for record in records)
