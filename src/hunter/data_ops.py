from __future__ import annotations

import contextlib
import io
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from hunter.acquisition.repositories import FileAcquisitionRepository
from hunter.automation import AutomationJobRunner, AutomationScheduler, load_automation_config
from hunter.automation.models import AutomationPipelinePlan
from hunter.persistence.models import QuerySpec
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.plugins.contracts import PipelineContext

DATA_OPS_JOB_IDS = (
    "discovery-market-run",
    "discovery-candidate-screening",
    "discovery-queue-refresh",
    "dataops-coingecko-market-sync",
    "dataops-defillama-protocol-sync",
    "dataops-github-developer-sync",
    "dataops-narrative-source-validation",
    "dataops-narrative-evidence-sync",
    "dataops-technology-graph-rebuild",
    "dataops-economic-graph-rebuild",
    "dataops-scenario-refresh",
    "dataops-market-validation-run",
    "dataops-committee-evaluation",
)

OPERATION_COMMANDS: dict[str, tuple[str, ...]] = {
    "discovery_market_run": ("discovery", "run", "--limit", "250"),
    "discovery_screen": ("discovery", "screen"),
    "discovery_queue_refresh": ("discovery", "queue", "refresh"),
    "coingecko_market_sync": ("coingecko", "sync"),
    "defillama_protocol_sync": ("defillama", "sync"),
    "github_developer_sync": ("github", "sync"),
    "narrative_source_validation": ("sources", "validate"),
    "narrative_evidence_sync": ("narrative", "sync"),
    "technology_graph_rebuild": ("graph", "build"),
    "economic_graph_rebuild": ("economic", "build"),
    "scenario_refresh": ("scenario", "run"),
    "market_validation_run": ("market-validation", "run"),
    "committee_evaluation": ("committee", "evaluate"),
}


@dataclass(frozen=True)
class DataOpsRunDetail:
    run_id: str
    job_id: str
    operation: str
    started_at: datetime
    finished_at: datetime
    status: str
    duration_seconds: float
    records_accepted: int
    records_rejected: int
    coverage_before: str
    coverage_after: str
    attempts: int
    error: str = ""


class DataOpsExecutor:
    def __call__(self, plan: AutomationPipelinePlan, context: PipelineContext) -> PipelineContext:
        operation = str(plan.target.metadata.get("operation") or plan.target.target_id)
        command = OPERATION_COMMANDS[operation]
        before = _evidence_coverage()
        accepted_before, rejected_before = _evidence_counts()
        started = datetime.now(tz=UTC)
        attempts = 0
        status = "succeeded"
        error = ""
        for attempt in range(1, 3):
            attempts = attempt
            exit_code, output = _run_hunter(command)
            if exit_code == 0:
                break
            status = "failed"
            error = _sanitize_error(output)
        accepted_after, rejected_after = _evidence_counts()
        after = _evidence_coverage()
        finished = datetime.now(tz=UTC)
        detail = DataOpsRunDetail(
            run_id=context.ensure_run(
                run_type=plan.run_type,
                target_id=plan.target.target_id,
                target_type=plan.target.target_type,
            ).run_id,
            job_id=plan.job_id,
            operation=operation,
            started_at=started,
            finished_at=finished,
            status=status,
            duration_seconds=round((finished - started).total_seconds(), 4),
            records_accepted=max(0, accepted_after - accepted_before),
            records_rejected=max(0, rejected_after - rejected_before),
            coverage_before=before,
            coverage_after=after,
            attempts=attempts,
            error=error,
        )
        _append_run_detail(detail)
        context.set("data_ops_detail", asdict(detail))
        if status != "succeeded":
            msg = error or f"data operation failed: {operation}"
            raise RuntimeError(msg)
        return context


def install_data_ops_jobs(path: Path = Path("configs/automation.yaml")) -> tuple[str, ...]:
    payload = _load_yaml(path)
    existing_jobs = {str(job.get("job_id")): job for job in payload.get("jobs", ()) if isinstance(job, dict)}
    for job in _data_ops_jobs():
        existing_jobs[str(job["job_id"])] = job
    payload["enabled"] = True
    payload["timezone"] = str(payload.get("timezone", "UTC"))
    payload["polling_interval_seconds"] = int(payload.get("polling_interval_seconds", 60))
    payload["jobs"] = list(existing_jobs.values())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    _persist_job_definitions(path)
    return DATA_OPS_JOB_IDS


def run_data_ops_now(path: Path = Path("configs/automation.yaml")) -> tuple[Any, ...]:
    config = load_automation_config(path)
    jobs = tuple(job for job in config.jobs if job.job_id in DATA_OPS_JOB_IDS)
    engine = create_sqlite_engine("data/data_ops.sqlite")
    create_schema(engine)
    session = SessionFactory(engine).create()
    try:
        repositories = RepositoryFactory(session)
        runner = AutomationJobRunner(pipeline_executor=DataOpsExecutor(), repositories=repositories)
        scheduler = AutomationScheduler(jobs, runner, polling_interval_seconds=config.polling_interval_seconds)
        scheduler.start()
        runs = tuple(runner.run_once(job) for job in jobs)
        session.commit()
        return runs
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def data_ops_status(path: Path = Path("configs/automation.yaml")) -> dict[str, Any]:
    config = load_automation_config(path)
    jobs = tuple(job for job in config.jobs if job.job_id in DATA_OPS_JOB_IDS)
    records = _automation_run_records()
    latest_by_job: dict[str, Any] = {}
    for record in records:
        if record.job_id in DATA_OPS_JOB_IDS:
            latest_by_job[record.job_id] = record
    return {
        "jobs": len(jobs),
        "runs": len(records),
        "latest_by_job": latest_by_job,
        "details": _run_details(),
    }


def data_ops_history() -> tuple[DataOpsRunDetail, ...]:
    return _run_details()


def data_ops_failures() -> tuple[DataOpsRunDetail, ...]:
    return tuple(item for item in _run_details() if item.status != "succeeded")


def _persist_job_definitions(path: Path) -> None:
    config = load_automation_config(path)
    engine = create_sqlite_engine("data/data_ops.sqlite")
    create_schema(engine)
    session = SessionFactory(engine).create()
    try:
        repositories = RepositoryFactory(session)
        runner = AutomationJobRunner(pipeline_executor=DataOpsExecutor(), repositories=repositories)
        now = datetime.now(tz=UTC)
        for job in config.jobs:
            if job.job_id in DATA_OPS_JOB_IDS:
                runner._save_job(job, now)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _automation_run_records() -> tuple[Any, ...]:
    engine = create_sqlite_engine("data/data_ops.sqlite")
    create_schema(engine)
    session = SessionFactory(engine).create()
    try:
        return (
            RepositoryFactory(session)
            .automation_runs()
            .query(QuerySpec(record_kind="automation-run", filters=(), sort_by="created_at", direction="asc"))
        )
    finally:
        session.close()


def _data_ops_jobs() -> tuple[dict[str, Any], ...]:
    return (
        _job("discovery-market-run", "Global discovery market run", "every_6_hours", "discovery_market_run"),
        _job("discovery-candidate-screening", "Discovery candidate screening", "every_6_hours", "discovery_screen"),
        _job("discovery-queue-refresh", "Discovery queue refresh", "every_6_hours", "discovery_queue_refresh"),
        _job("dataops-coingecko-market-sync", "CoinGecko market sync", "every_6_hours", "coingecko_market_sync"),
        _job(
            "dataops-defillama-protocol-sync",
            "DefiLlama protocol sync",
            "cron",
            "defillama_protocol_sync",
            expression="0 */12 * * *",
        ),
        _job("dataops-github-developer-sync", "GitHub developer sync", "daily", "github_developer_sync"),
        _job(
            "dataops-narrative-source-validation",
            "Narrative source validation",
            "daily",
            "narrative_source_validation",
        ),
        _job("dataops-narrative-evidence-sync", "Narrative evidence sync", "every_6_hours", "narrative_evidence_sync"),
        _job(
            "dataops-technology-graph-rebuild",
            "Technology graph rebuild",
            "daily",
            "technology_graph_rebuild",
            depends_on="dataops-coingecko-market-sync,dataops-defillama-protocol-sync,dataops-github-developer-sync,dataops-narrative-evidence-sync",
        ),
        _job(
            "dataops-economic-graph-rebuild",
            "Economic graph rebuild",
            "daily",
            "economic_graph_rebuild",
            depends_on="dataops-coingecko-market-sync,dataops-defillama-protocol-sync",
        ),
        _job(
            "dataops-scenario-refresh",
            "Scenario refresh",
            "daily",
            "scenario_refresh",
            depends_on="dataops-technology-graph-rebuild,dataops-economic-graph-rebuild",
        ),
        _job(
            "dataops-market-validation-run",
            "Market validation run",
            "daily",
            "market_validation_run",
            depends_on="dataops-scenario-refresh",
        ),
        _job(
            "dataops-committee-evaluation",
            "Committee evaluation",
            "daily",
            "committee_evaluation",
            depends_on="dataops-market-validation-run",
        ),
    )


def _job(
    job_id: str,
    name: str,
    schedule_type: str,
    operation: str,
    *,
    expression: str | None = None,
    depends_on: str = "",
) -> dict[str, Any]:
    schedule = {"type": schedule_type}
    if expression:
        schedule["expression"] = expression
    return {
        "job_id": job_id,
        "name": name,
        "enabled": True,
        "job_kind": "ingest_project_data",
        "schedule": schedule,
        "timezone": "UTC",
        "target": {"type": "operation", "id": operation},
        "run_type": "scheduled",
        "pipeline_options": {
            "run_intelligence": False,
            "run_fusion": False,
            "run_opportunity_timing": False,
            "run_investment_committee": False,
        },
        "persistence_policy": "atomic",
        "as_of_policy": {"mode": "current"},
        "timeout_seconds": 7200,
        "concurrency_policy": {"prevent_overlapping": True, "scope": "job_target"},
        "metadata": {"operation": operation, "depends_on": depends_on, "records_metrics": True},
    }


def _run_hunter(command: tuple[str, ...]) -> tuple[int, str]:
    from hunter.cli import main

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        try:
            code = main(list(command))
        except SystemExit as exc:
            code = int(exc.code) if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


def _evidence_counts() -> tuple[int, int]:
    repository = FileAcquisitionRepository()
    valid = sum(1 for item in repository.validations.values() if item.status == "valid")
    rejected = sum(1 for item in repository.validations.values() if item.status != "valid")
    return valid, rejected


def _evidence_coverage() -> str:
    code, output = _run_hunter(("evidence", "coverage"))
    if code != 0:
        return "unavailable"
    return " ".join(output.strip().split())


def _append_run_detail(detail: DataOpsRunDetail) -> None:
    path = Path("data/data_ops/runs.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(detail)
    payload["started_at"] = detail.started_at.isoformat()
    payload["finished_at"] = detail.finished_at.isoformat()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True))
        handle.write("\n")


def _run_details() -> tuple[DataOpsRunDetail, ...]:
    path = Path("data/data_ops/runs.jsonl")
    if not path.exists():
        return ()
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        rows.append(
            DataOpsRunDetail(
                run_id=str(payload["run_id"]),
                job_id=str(payload["job_id"]),
                operation=str(payload["operation"]),
                started_at=datetime.fromisoformat(str(payload["started_at"])),
                finished_at=datetime.fromisoformat(str(payload["finished_at"])),
                status=str(payload["status"]),
                duration_seconds=float(payload["duration_seconds"]),
                records_accepted=int(payload["records_accepted"]),
                records_rejected=int(payload["records_rejected"]),
                coverage_before=str(payload["coverage_before"]),
                coverage_after=str(payload["coverage_after"]),
                attempts=int(payload["attempts"]),
                error=str(payload.get("error", "")),
            )
        )
    return tuple(rows)


def _sanitize_error(output: str) -> str:
    cleaned = " ".join(output.strip().split())
    if not cleaned:
        return "command failed"
    return cleaned[:300]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"enabled": True, "timezone": "UTC", "polling_interval_seconds": 60, "jobs": []}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        msg = "automation config must be a mapping"
        raise ValueError(msg)
    payload.setdefault("jobs", [])
    return payload
