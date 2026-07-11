from __future__ import annotations

from datetime import datetime

from hunter.automation.models import AutomationJob, AutomationRun
from hunter.execution.identity import identity
from hunter.persistence.records import AutomationJobRecord, AutomationRunRecord


def job_to_record(job: AutomationJob, *, created_at: datetime) -> AutomationJobRecord:
    return AutomationJobRecord(
        id=job.job_id,
        created_at=created_at,
        effective_at=created_at,
        job_id=job.job_id,
        name=job.name,
        enabled=job.enabled,
        schedule={"type": job.schedule.schedule_type, "expression": job.schedule.expression, "run_at": job.schedule.run_at.isoformat() if job.schedule.run_at else None},
        timezone=job.timezone,
        target={"type": job.target.target_type, "id": job.target.target_id},
        run_type=job.run_type,
        pipeline_options=job.pipeline_options.__dict__,
        persistence_policy=job.persistence_policy,
        as_of_policy={"mode": job.as_of_policy.mode, "as_of": job.as_of_policy.as_of.isoformat() if job.as_of_policy.as_of else None},
        timeout_seconds=job.timeout_seconds,
        concurrency_policy=job.concurrency_policy.__dict__,
        metadata=job.metadata,
    )


def run_to_record(run: AutomationRun, *, created_at: datetime, effective_at: datetime) -> AutomationRunRecord:
    return AutomationRunRecord(
        id=identity(
            "automation-run-state",
            {
                "automation_run_id": run.automation_run_id,
                "job_id": run.job_id,
                "scheduled_for": run.scheduled_for,
                "status": run.status,
            },
        ),
        created_at=created_at,
        effective_at=effective_at,
        automation_run_id=run.automation_run_id,
        job_id=run.job_id,
        pipeline_run_id=run.pipeline_run_id,
        operational_attempt_id=run.operational_attempt_id,
        scheduled_for=run.scheduled_for,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status,
        error_summary=run.error_summary,
        warning_summary=run.warning_summary,
        metadata=run.metadata,
    )
