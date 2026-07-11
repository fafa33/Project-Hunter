from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.automation import AutomationJobRunner, AutomationSchedule, AutomationScheduler, InProcessAutomationLock
from hunter.automation.configuration import automation_config_from_mapping
from hunter.automation.exceptions import AutomationLifecycleError, AutomationReplaySafetyError
from hunter.automation.lifecycle import transition
from hunter.automation.models import (
    AsOfPolicy,
    AutomationJob,
    AutomationRun,
    ConcurrencyPolicy,
    TargetSelection,
    is_due,
    next_scheduled_at,
)
from hunter.automation.persistence import job_to_record, run_to_record
from hunter.cli import main
from hunter.persistence import AutomationJobRecord, AutomationRunRecord, record_from_json, record_to_json
from hunter.persistence.sql import UnitOfWork, create_schema, create_sqlite_engine
from hunter.persistence.sql.session import SessionFactory
from hunter.plugins.contracts import PipelineContext

NOW = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def test_config_loading_and_supported_job_shape() -> None:
    config = automation_config_from_mapping(
        {
            "enabled": True,
            "timezone": "UTC",
            "polling_interval_seconds": 30,
            "jobs": [
                {
                    "job_id": "job-a",
                    "schedule": {"type": "hourly"},
                    "target": {"type": "project", "id": "project-a"},
                    "run_type": "scheduled",
                    "job_kind": "current_state_pipeline",
                }
            ],
        }
    )

    assert config.enabled is True
    assert config.polling_interval_seconds == 30
    assert config.jobs[0].job_id == "job-a"


def test_schedule_parsing_timezone_and_due_rules() -> None:
    hourly = AutomationSchedule("hourly")
    six = AutomationSchedule("every_6_hours")
    daily = AutomationSchedule("daily")
    weekly = AutomationSchedule("weekly")
    cron = AutomationSchedule("cron", expression="0 12 * * *")
    once = AutomationSchedule("once", run_at=NOW)

    assert next_scheduled_at(hourly, after=NOW, timezone="UTC") == NOW + timedelta(hours=1)
    assert is_due(hourly, at=NOW, timezone="UTC")
    assert is_due(six, at=NOW, timezone="UTC")
    assert is_due(daily, at=NOW, timezone="UTC")
    assert is_due(weekly, at=NOW, timezone="UTC")
    assert is_due(cron, at=NOW.replace(hour=12), timezone="UTC")
    assert is_due(once, at=NOW + timedelta(minutes=1), timezone="UTC")


def test_disabled_jobs_and_scheduler_run_due() -> None:
    enabled = _job("enabled", schedule=AutomationSchedule("once", run_at=NOW))
    disabled = replace(_job("disabled", schedule=AutomationSchedule("once", run_at=NOW)), enabled=False)
    runner = AutomationJobRunner(pipeline_executor=_successful_executor, clock=lambda: NOW)
    scheduler = AutomationScheduler((enabled, disabled), runner, clock=lambda: NOW)

    scheduler.start()
    runs = scheduler.run_due(at=NOW)
    scheduler.stop()

    assert len(runs) == 1
    assert runs[0].status == "succeeded"
    assert any(event.event_type == "scheduler_started" for event in scheduler.status().events)
    assert any(event.event_type == "job_skipped" for event in scheduler.status().events)


def test_run_once_duplicate_prevention_and_lock_release() -> None:
    job = _job("job-lock")
    lock = InProcessAutomationLock()
    assert lock.acquire(job.lock_key()) is True
    runner = AutomationJobRunner(pipeline_executor=_successful_executor, lock=lock, clock=lambda: NOW)

    blocked = runner.run_once(job, scheduled_for=NOW)
    lock.release(job.lock_key())
    succeeded = runner.run_once(job, scheduled_for=NOW + timedelta(minutes=1))

    assert blocked.status == "blocked"
    assert succeeded.status == "succeeded"
    assert lock.locked(job.lock_key()) is False


def test_lifecycle_transitions_success_partial_failed_and_cancelled() -> None:
    run = _run("run-1", "scheduled")
    claimed = transition(run, "claimed", at=NOW)
    running = transition(claimed, "running", at=NOW)
    assert transition(running, "succeeded", at=NOW).status == "succeeded"
    assert transition(running, "partial", at=NOW).status == "partial"
    assert transition(running, "failed", at=NOW, error="boom").error_summary == "boom"
    assert transition(running, "cancelled", at=NOW).status == "cancelled"
    with pytest.raises(AutomationLifecycleError):
        transition(run, "succeeded", at=NOW)


def test_success_partial_failed_and_cancel_paths() -> None:
    success_runner = AutomationJobRunner(pipeline_executor=_successful_executor, clock=lambda: NOW)
    partial_runner = AutomationJobRunner(pipeline_executor=_partial_executor, clock=lambda: NOW)
    failed_runner = AutomationJobRunner(pipeline_executor=_failed_executor, clock=lambda: NOW)
    job = _job("job-run")

    success = success_runner.run_once(job, scheduled_for=NOW)
    partial = partial_runner.run_once(job, scheduled_for=NOW)
    failed = failed_runner.run_once(job, scheduled_for=NOW)
    cancelled = success_runner.cancel(_run("cancel-me", "running"), job=job)

    assert success.status == "succeeded"
    assert partial.status == "partial"
    assert failed.status == "failed"
    assert cancelled.status == "cancelled"


def test_current_state_and_replay_as_of_policies() -> None:
    current = _job("current", as_of_policy=AsOfPolicy("current"))
    replay = _job("replay", run_type="replay", as_of_policy=AsOfPolicy("replay", NOW), job_kind="historical_replay")

    assert AutomationJobRunner(pipeline_executor=_successful_executor, clock=lambda: NOW).run_once(current, scheduled_for=NOW).status == "succeeded"
    assert AutomationJobRunner(pipeline_executor=_successful_executor, clock=lambda: NOW).run_once(replay, scheduled_for=NOW).status == "succeeded"
    with pytest.raises(ValueError):
        AsOfPolicy("replay")
    bad = replace(current, run_type="replay", job_kind="historical_replay")
    with pytest.raises(AutomationReplaySafetyError):
        AutomationJobRunner(pipeline_executor=_successful_executor, clock=lambda: NOW).run_once(bad, scheduled_for=NOW)


def test_persisted_automation_job_and_run_records() -> None:
    job = _job("job-persist")
    run = _run("run-persist", "succeeded")
    job_record = job_to_record(job, created_at=NOW)
    run_record = run_to_record(run, created_at=NOW, effective_at=NOW)

    assert isinstance(record_from_json(record_to_json(job_record)), AutomationJobRecord)
    assert isinstance(record_from_json(record_to_json(run_record)), AutomationRunRecord)

    engine = create_sqlite_engine(":memory:")
    create_schema(engine)
    with UnitOfWork(SessionFactory(engine)) as uow:
        repositories = uow.repositories
        assert repositories is not None
        repositories.automation_jobs().save(job_record)
        repositories.automation_runs().save(run_record)
        assert repositories.automation_jobs().load(job_record.id) == job_record
        assert repositories.automation_runs().load(run_record.id) == run_record


def test_pipeline_and_attempt_linkage_and_restart_recovery() -> None:
    runner = AutomationJobRunner(pipeline_executor=_successful_executor, clock=lambda: NOW)
    scheduler = AutomationScheduler((_job("job-recover"),), runner, clock=lambda: NOW)

    scheduler.recover()
    run = runner.run_once(_job("job-recover"), scheduled_for=NOW)

    assert run.pipeline_run_id == "pipeline-run:test"
    assert run.operational_attempt_id == "operational-attempt:test"
    assert any(event.detail == "scheduler recovered" for event in scheduler.status().events)


def test_cli_commands(tmp_path) -> None:
    config = tmp_path / "automation.yaml"
    config.write_text(
        """
enabled: true
timezone: UTC
jobs:
  - job_id: job-cli
    schedule:
      type: once
      run_at: "2026-01-05T00:00:00+00:00"
    target:
      type: project
      id: project-a
    run_type: scheduled
""",
    )

    assert main(["--config", str(config), "automation", "list-jobs"]) == 0
    assert main(["--config", str(config), "automation", "show-job", "job-cli"]) == 0
    assert main(["--config", str(config), "automation", "status"]) == 0
    assert main(["--config", str(config), "automation", "start"]) == 0
    assert main(["--config", str(config), "automation", "cancel", "run-1"]) == 0
    assert main(["--config", str(config), "automation", "run-once", "job-cli"]) == 0


def _job(
    job_id: str,
    *,
    schedule: AutomationSchedule | None = None,
    run_type: str = "scheduled",
    as_of_policy: AsOfPolicy | None = None,
    job_kind: str = "current_state_pipeline",
) -> AutomationJob:
    return AutomationJob(
        job_id=job_id,
        name=job_id,
        enabled=True,
        schedule=schedule or AutomationSchedule("hourly"),
        timezone="UTC",
        target=TargetSelection("project", "project-a"),
        run_type=run_type,
        as_of_policy=as_of_policy or AsOfPolicy("current"),
        concurrency_policy=ConcurrencyPolicy(True),
        job_kind=job_kind,  # type: ignore[arg-type]
    )


def _run(run_id: str, status: str) -> AutomationRun:
    return AutomationRun(automation_run_id=run_id, job_id="job", scheduled_for=NOW, status=status)  # type: ignore[arg-type]


def _successful_executor(job: AutomationJob, context: PipelineContext) -> PipelineContext:
    del job
    context.ensure_run(target_id="project-a", target_type="project")
    assert context.run is not None
    object.__setattr__(context.run, "run_id", "pipeline-run:test")
    context.persistence_events.append(type("Event", (), {"record_id": "operational-attempt:test"})())
    return context


def _partial_executor(job: AutomationJob, context: PipelineContext) -> PipelineContext:
    context = _successful_executor(job, context)
    context.persistence_errors.append("partial")
    return context


def _failed_executor(job: AutomationJob, context: PipelineContext) -> PipelineContext:
    del job, context
    raise RuntimeError("pipeline failed")
