from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime

from hunter.automation.contracts import AutomationRepositoryFactoryProtocol, ClockFn, PipelineExecutor
from hunter.automation.execution import AutomationPipelineExecutor
from hunter.automation.jobs import pipeline_plan_for_job, validate_job_for_execution
from hunter.automation.lifecycle import transition
from hunter.automation.locking import AutomationLock, InProcessAutomationLock
from hunter.automation.models import AutomationEvent, AutomationJob, AutomationRun
from hunter.automation.persistence import run_from_record, run_to_record
from hunter.execution.identity import identity
from hunter.persistence.models import QueryFilter, QuerySpec
from hunter.plugins.contracts import PipelineContext


class AutomationJobRunner:
    def __init__(
        self,
        *,
        pipeline_executor: PipelineExecutor | None = None,
        lock: AutomationLock | None = None,
        repositories: AutomationRepositoryFactoryProtocol | None = None,
        clock: ClockFn | None = None,
    ) -> None:
        self.pipeline_executor = pipeline_executor or AutomationPipelineExecutor()
        self.lock = lock or InProcessAutomationLock()
        self.repositories = repositories
        self.clock = clock or (lambda: datetime.now(UTC))
        self.events: list[AutomationEvent] = []
        self.runs: list[AutomationRun] = []
        self.cancelled_run_ids: set[str] = set()

    def run_once(self, job: AutomationJob, *, scheduled_for: datetime | None = None) -> AutomationRun:
        scheduled = scheduled_for or self.clock()
        validate_job_for_execution(job)
        plan = pipeline_plan_for_job(job, scheduled_for=scheduled)
        run = AutomationRun(
            automation_run_id=_run_id(job, scheduled),
            job_id=job.job_id,
            scheduled_for=scheduled,
            status="scheduled",
            metadata={
                "target_id": job.target.target_id,
                "target_type": job.target.target_type,
                "run_type": job.run_type,
            },
        )
        self._record("job_scheduled", job, run, "job scheduled")
        self._save_job(job, scheduled)
        self._save_run(run, scheduled)
        current_run = run
        key = job.lock_key()
        if run.automation_run_id in self.cancelled_run_ids:
            cancelled = transition(run, "cancelled", at=self.clock(), warning="run cancelled before execution")
            self._record("job_cancelled", job, cancelled, "run cancelled before execution")
            self._save_run(cancelled, scheduled)
            self.runs.append(cancelled)
            return cancelled
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
            context.set("automation_replay", plan.replay)
            context.set("automation_job_id", job.job_id)
            context.set("automation_job_kind", job.job_kind)
            context.set("automation_target_id", job.target.target_id)
            context.set("automation_target_type", job.target.target_type)
            result = self.pipeline_executor(plan, context)
            if _timed_out(job, running.started_at, self.clock()):
                timeout = transition(
                    running,
                    "failed",
                    at=self.clock(),
                    error=f"TimeoutError: automation job exceeded {job.timeout_seconds} seconds",
                )
                self._record("job_failed", job, timeout, timeout.error_summary or "timeout")
                self._record("job_timeout", job, timeout, timeout.error_summary or "timeout")
                self._save_run(timeout, scheduled)
                self.runs.append(timeout)
                return timeout
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
        self.cancelled_run_ids.add(run.automation_run_id)
        self._record("job_cancelled", job, cancelled, "job cancelled")
        self._save_run(cancelled, run.scheduled_for)
        key = job.lock_key()
        if self.lock.locked(key):
            self.lock.release(key)
            self._record("lock_released", job, cancelled, key)
        self.runs.append(cancelled)
        return cancelled

    def cancel_by_id(self, automation_run_id: str, *, jobs: Iterable[AutomationJob]) -> AutomationRun:
        run = self.load_latest_run(automation_run_id)
        if run is None:
            msg = f"Unknown automation run: {automation_run_id}"
            raise LookupError(msg)
        job = _job_for_run(run, jobs)
        return self.cancel(run, job=job)

    def load_latest_run(self, automation_run_id: str) -> AutomationRun | None:
        candidates = [run for run in self.runs if run.automation_run_id == automation_run_id]
        if self.repositories is not None:
            records = self.repositories.automation_runs().query(
                QuerySpec(
                    record_kind="automation-run",
                    filters=(QueryFilter("automation_run_id", automation_run_id),),
                    sort_by="created_at",
                    direction="desc",
                )
            )
            candidates.extend(run_from_record(record) for record in records)
        if not candidates:
            return None
        return sorted(
            candidates, key=lambda item: (item.finished_at or item.started_at or item.scheduled_for, item.status)
        )[-1]

    def _record(self, event_type: str, job: AutomationJob, run: AutomationRun, detail: str) -> None:
        self.events.append(
            AutomationEvent(
                event_type=event_type,
                job_id=job.job_id,
                automation_run_id=run.automation_run_id,
                at=self.clock(),
                detail=detail,
            )
        )

    def _save_job(self, job: AutomationJob, created_at: datetime) -> None:
        if self.repositories is None:
            return
        from hunter.automation.persistence import job_to_record

        self.repositories.automation_jobs().save(job_to_record(job, created_at=created_at))

    def _save_run(self, run: AutomationRun, effective_at: datetime) -> None:
        if self.repositories is None:
            return
        self.repositories.automation_runs().save(run_to_record(run, created_at=self.clock(), effective_at=effective_at))


def _run_id(job: AutomationJob, scheduled_for: datetime) -> str:
    return identity(
        "automation-run",
        {"job_id": job.job_id, "target": job.target, "scheduled_for": scheduled_for, "run_type": job.run_type},
    )


def _attempt_id(context: PipelineContext) -> str | None:
    for event in reversed(context.persistence_events):
        record_id = getattr(event, "record_id", None)
        if isinstance(record_id, str) and "operational-attempt" in record_id:
            return record_id
    return None


def _timed_out(job: AutomationJob, started_at: datetime | None, now: datetime) -> bool:
    if job.timeout_seconds is None or started_at is None:
        return False
    return (now - started_at).total_seconds() > job.timeout_seconds


def _job_for_run(run: AutomationRun, jobs: Iterable[AutomationJob]) -> AutomationJob:
    for job in jobs:
        if job.job_id == run.job_id:
            return job
    msg = f"Automation job not available for run: {run.job_id}"
    raise LookupError(msg)
