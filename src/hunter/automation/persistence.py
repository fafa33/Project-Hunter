from __future__ import annotations

from datetime import datetime

from hunter.automation.models import (
    AsOfPolicy,
    AutomationJob,
    AutomationRun,
    AutomationSchedule,
    ConcurrencyPolicy,
    PipelineOptions,
    TargetSelection,
)
from hunter.execution.identity import identity
from hunter.persistence.records import AutomationJobRecord, AutomationRunRecord


def job_definition_id(job: AutomationJob) -> str:
    return identity(
        "automation-job-definition",
        {
            "job_id": job.job_id,
            "name": job.name,
            "enabled": job.enabled,
            "schedule": {
                "type": job.schedule.schedule_type,
                "expression": job.schedule.expression,
                "run_at": job.schedule.run_at,
            },
            "timezone": job.timezone,
            "target": {"type": job.target.target_type, "id": job.target.target_id},
            "run_type": job.run_type,
            "pipeline_options": job.pipeline_options,
            "persistence_policy": job.persistence_policy,
            "as_of_policy": job.as_of_policy,
            "timeout_seconds": job.timeout_seconds,
            "concurrency_policy": job.concurrency_policy,
            "job_kind": job.job_kind,
            "metadata": dict(job.metadata),
            "identity_schema_version": "automation-job-definition-v1",
        },
    )


def job_to_record(job: AutomationJob, *, created_at: datetime) -> AutomationJobRecord:
    return AutomationJobRecord(
        id=job_definition_id(job),
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
        metadata={**dict(job.metadata), "job_kind": job.job_kind},
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
        metadata=dict(run.metadata),
    )


def job_from_record(record: AutomationJobRecord) -> AutomationJob:
    return AutomationJob(
        job_id=record.job_id,
        name=record.name,
        enabled=record.enabled,
        schedule=AutomationSchedule(
            schedule_type=str(record.schedule.get("type", "daily")),  # type: ignore[arg-type]
            expression=record.schedule.get("expression"),
            run_at=_datetime_or_none(record.schedule.get("run_at")),
        ),
        timezone=record.timezone,
        target=TargetSelection(str(record.target.get("type", "project")), str(record.target.get("id", "global-crypto"))),
        run_type=record.run_type,
        pipeline_options=PipelineOptions(
            run_intelligence=bool(record.pipeline_options.get("run_intelligence", True)),
            run_fusion=bool(record.pipeline_options.get("run_fusion", False)),
            run_opportunity_timing=bool(record.pipeline_options.get("run_opportunity_timing", False)),
            selected_engines=tuple(str(item) for item in record.pipeline_options.get("selected_engines", ())),
            generate_reports=bool(record.pipeline_options.get("generate_reports", False)),
            evaluate_alerts=bool(record.pipeline_options.get("evaluate_alerts", False)),
        ),
        persistence_policy=record.persistence_policy,
        as_of_policy=AsOfPolicy(
            mode=str(record.as_of_policy.get("mode", "current")),
            as_of=_datetime_or_none(record.as_of_policy.get("as_of")),
        ),
        timeout_seconds=record.timeout_seconds,
        concurrency_policy=ConcurrencyPolicy(
            prevent_overlapping=bool(record.concurrency_policy.get("prevent_overlapping", True)),
            scope=str(record.concurrency_policy.get("scope", "job_target")),
        ),
        job_kind=str(record.metadata.get("job_kind", "current_state_pipeline")),  # type: ignore[arg-type]
        metadata={key: value for key, value in record.metadata.items() if key != "job_kind"},
    )


def run_from_record(record: AutomationRunRecord) -> AutomationRun:
    return AutomationRun(
        automation_run_id=record.automation_run_id,
        job_id=record.job_id,
        scheduled_for=record.scheduled_for,
        status=record.status,  # type: ignore[arg-type]
        pipeline_run_id=record.pipeline_run_id,
        operational_attempt_id=record.operational_attempt_id,
        started_at=record.started_at,
        finished_at=record.finished_at,
        error_summary=record.error_summary,
        warning_summary=record.warning_summary,
        metadata=record.metadata,
    )


def _datetime_or_none(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
