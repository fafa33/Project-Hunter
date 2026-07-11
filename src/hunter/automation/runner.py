from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from hunter.automation.jobs import job_as_of, validate_job_for_execution
from hunter.automation.lifecycle import transition
from hunter.automation.locking import AutomationLock, InProcessAutomationLock
from hunter.automation.models import AutomationEvent, AutomationJob, AutomationRun
from hunter.execution.identity import identity
from hunter.plugins.contracts import PipelineContext


class AutomationJobRunner:
    def __init__(
        self,
        *,
        pipeline_executor: Any | None = None,
        lock: AutomationLock | None = None,
        repositories: Any | None = None,
        clock: Any | None = None,
    ) -> None:
        self.pipeline_executor = pipeline_executor or _default_pipeline_executor
        self.lock = lock or InProcessAutomationLock()
        self.repositories = repositories
        self.clock = clock or (lambda: datetime.now(UTC))
        self.events: list[AutomationEvent] = []
        self.runs: list[AutomationRun] = []

    def run_once(self, job: AutomationJob, *, scheduled_for: datetime | None = None) -> AutomationRun:
        scheduled = scheduled_for or self.clock()
        validate_job_for_execution(job)
        run = AutomationRun(
            automation_run_id=_run_id(job, scheduled),
            job_id=job.job_id,
            scheduled_for=scheduled,
            status="scheduled",
            metadata={"target_id": job.target.target_id, "target_type": job.target.target_type, "run_type": job.run_type},
        )
        self._record("job_scheduled", job, run, "job scheduled")
        self._save_job(job, scheduled)
        self._save_run(run, scheduled)
        current_run = run
        key = job.lock_key()
        if job.concurrency_policy.prevent_overlapping and not self.lock.acquire(key):
            blocked = transition(run, "blocked", at=self.clock(), warning="duplicate concurrent run prevented")
            self._record("job_blocked", job, blocked, "duplicate concurrent run prevented")
            self._save_run(blocked, scheduled)
            self.runs.append(blocked)
            return blocked
        self._record("lock_acquired", job, run, key)
        try:
            claimed = transition(run, "claimed", at=self.clock())
            current_run = claimed
            self._record("job_claimed", job, claimed, "job claimed")
            running = transition(claimed, "running", at=self.clock())
            current_run = running
            self._record("job_started", job, running, "job started")
            context = PipelineContext()
            as_of = job_as_of(job, scheduled_for=scheduled)
            if as_of is not None:
                context.set("automation_as_of", as_of.isoformat())
            context.set("automation_job_id", job.job_id)
            context.set("automation_job_kind", job.job_kind)
            context.set("automation_target_id", job.target.target_id)
            context.set("automation_target_type", job.target.target_type)
            result = self.pipeline_executor(job, context)
            pipeline_run_id = result.run.run_id if result.run is not None else None
            attempt_id = _attempt_id(result)
            final_status = "partial" if getattr(result, "persistence_errors", None) else "succeeded"
            finished = transition(
                replace(running, pipeline_run_id=pipeline_run_id, operational_attempt_id=attempt_id),
                final_status,
                at=self.clock(),
            )
            self._record("job_partial" if final_status == "partial" else "job_succeeded", job, finished, final_status)
            self._save_run(finished, scheduled)
            self.runs.append(finished)
            return finished
        except BaseException as exc:
            if current_run.status != "running":
                current_run = replace(current_run, status="running", started_at=self.clock())
            failed = transition(current_run, "failed", at=self.clock(), error=f"{exc.__class__.__name__}: {exc}")
            self._record("job_failed", job, failed, failed.error_summary or "")
            self._save_run(failed, scheduled)
            self.runs.append(failed)
            return failed
        finally:
            if self.lock.locked(key):
                self.lock.release(key)
                self._record("lock_released", job, run, key)

    def cancel(self, run: AutomationRun, *, job: AutomationJob) -> AutomationRun:
        cancelled = transition(run, "cancelled", at=self.clock())
        self._record("job_cancelled", job, cancelled, "job cancelled")
        self._save_run(cancelled, run.scheduled_for)
        return cancelled

    def _record(self, event_type: str, job: AutomationJob, run: AutomationRun, detail: str) -> None:
        self.events.append(AutomationEvent(event_type=event_type, job_id=job.job_id, automation_run_id=run.automation_run_id, at=self.clock(), detail=detail))

    def _save_job(self, job: AutomationJob, created_at: datetime) -> None:
        if self.repositories is None:
            return
        from hunter.automation.persistence import job_to_record

        self.repositories.automation_jobs().save(job_to_record(job, created_at=created_at))

    def _save_run(self, run: AutomationRun, effective_at: datetime) -> None:
        if self.repositories is None:
            return
        from hunter.automation.persistence import run_to_record

        self.repositories.automation_runs().save(run_to_record(run, created_at=self.clock(), effective_at=effective_at))


def _run_id(job: AutomationJob, scheduled_for: datetime) -> str:
    return identity("automation-run", {"job_id": job.job_id, "target": job.target, "scheduled_for": scheduled_for, "run_type": job.run_type})


def _attempt_id(context: PipelineContext) -> str | None:
    for event in reversed(context.persistence_events):
        record_id = getattr(event, "record_id", None)
        if isinstance(record_id, str) and "operational-attempt" in record_id:
            return record_id
    return None


def _default_pipeline_executor(job: AutomationJob, context: PipelineContext) -> PipelineContext:
    from hunter.pipeline import PipelineOrchestrator

    return PipelineOrchestrator().run(context=context)
