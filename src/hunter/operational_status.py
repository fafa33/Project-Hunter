from __future__ import annotations

import json
import os
import platform
import sqlite3
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

from hunter.analytical_store_health import PROJECTION_VERSION, inspect_registered_analytical_stores
from hunter.automation.configuration import load_automation_config
from hunter.automation.models import AutomationJob, next_scheduled_at

HealthState = Literal["HEALTHY", "DEGRADED", "FAILED"]


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str], Path], CommandResult]


def build_status(
    *,
    root: Path | None = None,
    config_path: Path | None = None,
    now: datetime | None = None,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    runtime_root = (root or Path.cwd()).resolve()
    observed_at = now or datetime.now(UTC)
    runner = command_runner or _run_command
    config_file = _resolve(runtime_root, config_path or Path("configs/automation.yaml"))
    jobs = _load_jobs(config_file)
    scheduler = _scheduler_status(runtime_root, runner)
    automation = _automation_status(jobs, runtime_root=runtime_root, now=observed_at)
    discovery = _discovery_status(runtime_root)
    corpus = _corpus_status(runtime_root)
    database = _database_status(runtime_root)
    validation = _validation_status(runtime_root, observed_at)
    providers = _provider_status(runtime_root)
    logs = _logs_status(runtime_root)
    analytical_stores = {
        "projection_version": PROJECTION_VERSION,
        "stores": [
            item.as_dict() for item in inspect_registered_analytical_stores(root=runtime_root, observed_at=observed_at)
        ],
    }
    health = _health(scheduler, automation, database, corpus)
    return {
        "automation": automation,
        "analytical_stores": analytical_stores,
        "corpus": corpus,
        "database": database,
        "discovery": discovery,
        "general": _general_status(runtime_root, runner),
        "health": health,
        "logs": logs,
        "providers": providers,
        "scheduler": scheduler,
        "validation": validation,
    }


def render_text(status: Mapping[str, Any]) -> str:
    lines = [
        "GENERAL",
        f"Version: {status['general']['version']}",
        f"Branch: {status['general']['branch']}",
        f"Commit: {status['general']['commit']}",
        f"Repository path: {status['general']['repository_path']}",
        f"Runtime path: {status['general']['runtime_path']}",
        f"Python version: {status['general']['python_version']}",
        "",
        "SCHEDULER",
        f"State: {status['scheduler']['state']}",
        f"PID: {status['scheduler']['pid']}",
        f"Uptime: {status['scheduler']['uptime']}",
        f"LaunchAgent: {status['scheduler']['launchagent_status']}",
        f"Health: {status['scheduler']['health']}",
        "",
        "AUTOMATION",
    ]
    for job in status["automation"]["jobs"]:
        lines.append(
            f"- {job['job_id']}: {job['state']}, running={job['running']}, "
            f"last_execution={job['last_execution']}, next_scheduled_execution={job['next_scheduled_execution']}, "
            f"last_result={job['last_result']}, failure_count={job['failure_count']}"
        )
    lines.extend(
        [
            "",
            "ANALYTICAL STORES",
            *(
                f"- {item['store_id']}: {item['state']} ({item['reason']})"
                for item in status["analytical_stores"]["stores"]
            ),
            "",
            "DISCOVERY",
            f"Candidate queue size: {status['discovery']['candidate_queue_size']}",
            f"Pending discovery jobs: {status['discovery']['pending_discovery_jobs']}",
            f"Source health: {status['discovery']['source_health_summary']}",
            "",
            "CORPUS",
            f"Operational corpus: {status['corpus']['operational_corpus_status']}",
            f"Validation corpus: {status['corpus']['validation_corpus_status']}",
            f"Latest update: {status['corpus']['latest_update_timestamp']}",
            "",
            "DATABASE",
            f"Operational database reachable: {status['database']['operational_database_reachable']}",
            f"Historical database reachable: {status['database']['historical_database_reachable']}",
            f"Checkpoint database healthy: {status['database']['checkpoint_database_healthy']}",
            "",
            "VALIDATION",
            f"Open predictions: {status['validation']['open_predictions']}",
            f"Closed predictions: {status['validation']['closed_predictions']}",
            f"Due predictions: {status['validation']['due_predictions']}",
            f"Pending evaluations: {status['validation']['pending_evaluations']}",
            "",
            "PROVIDERS",
            f"Provider health: {status['providers']['provider_health_summary']}",
            "",
            "LOGS",
            f"Latest successful activity: {status['logs']['latest_successful_activity']}",
            f"Latest warning: {status['logs']['latest_warning']}",
            f"Latest error: {status['logs']['latest_error']}",
            "",
            "HEALTH",
            str(status["health"]),
        ]
    )
    return "\n".join(lines)


def render_json(status: Mapping[str, Any]) -> str:
    return json.dumps(status, sort_keys=True, separators=(",", ":"))


def exit_code(status: Mapping[str, Any]) -> int:
    if status["health"] == "HEALTHY":
        return 0
    if status["health"] == "DEGRADED":
        return 1
    return 2


def _general_status(root: Path, runner: CommandRunner) -> dict[str, str]:
    return {
        "branch": _git(root, runner, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit": _git(root, runner, "rev-parse", "HEAD"),
        "python_version": platform.python_version(),
        "repository_path": str(root),
        "runtime_path": str(root),
        "version": _version(),
    }


def _scheduler_status(root: Path, runner: CommandRunner) -> dict[str, Any]:
    launchctl = runner(("launchctl", "print", f"gui/{os.getuid()}/com.project-hunter.scheduler"), root)
    text = launchctl.stdout if launchctl.returncode == 0 else ""
    pid = _launch_value(text, "pid")
    state = _launch_value(text, "state")
    launch_status = "loaded" if launchctl.returncode == 0 else "unavailable"
    process = _process(pid, root, runner) if pid is not None else {}
    running = state == "running" and pid is not None
    return {
        "command": process.get("command"),
        "health": "healthy" if running else "stopped",
        "launchagent_status": launch_status,
        "pid": pid,
        "running": running,
        "state": "Running" if running else "Stopped",
        "uptime": process.get("uptime"),
    }


def _automation_status(jobs: tuple[AutomationJob, ...], *, runtime_root: Path, now: datetime) -> dict[str, Any]:
    runs = _latest_automation_runs(_automation_runs(runtime_root / "data" / "data_ops.sqlite"))
    by_job: dict[str, list[dict[str, Any]]] = {}
    for item in runs:
        by_job.setdefault(str(item.get("job_id", "")), []).append(item)
    rows = []
    for job in sorted(jobs, key=lambda item: item.job_id):
        job_runs = sorted(by_job.get(job.job_id, ()), key=lambda item: str(item.get("finished_at") or ""))
        latest = job_runs[-1] if job_runs else {}
        last_result = latest.get("status")
        rows.append(
            {
                "enabled": job.enabled,
                "failure_count": sum(1 for item in job_runs if item.get("status") == "failed"),
                "job_id": job.job_id,
                "last_execution": latest.get("finished_at") or latest.get("started_at") or latest.get("scheduled_for"),
                "last_result": last_result,
                "name": job.name,
                "next_scheduled_execution": _iso(next_scheduled_at(job.schedule, after=now, timezone=job.timezone)),
                "running": any(item.get("status") in {"scheduled", "claimed", "running"} for item in job_runs),
                "state": "enabled" if job.enabled else "disabled",
            }
        )
    return {"jobs": rows, "job_count": len(rows)}


def _discovery_status(root: Path) -> dict[str, Any]:
    db_path = root / "data" / "discovery" / "runtime" / "candidates.sqlite"
    queue_size = _sqlite_count(db_path, "candidates")
    pending = sum(
        1
        for item in _automation_runs(root / "data" / "data_ops.sqlite")
        if str(item.get("job_id", "")).startswith("discovery-")
        and item.get("status") in {"scheduled", "claimed", "running"}
    )
    source_health_path = root / "data" / "onchain" / "runtime" / "provider_status.jsonl"
    source_health_rows = _read_jsonl(source_health_path)
    source_health = len(source_health_rows) if source_health_path.exists() else None
    return {
        "active_sources": len(
            {str(row.get("state_id") or row.get("provider") or row.get("network")) for row in source_health_rows}
        ),
        "candidate_queue_size": queue_size,
        "pending_discovery_jobs": pending,
        "source_health_summary": f"records={source_health}" if source_health is not None else "unavailable",
    }


def _corpus_status(root: Path) -> dict[str, Any]:
    corpus = root / "data" / "operational_corpus"
    executions = corpus / "executions.jsonl"
    fallback_operational = root / "data" / "data_ops" / "runs.jsonl"
    historical_validation = root / "data" / "historical_validation" / "runs.jsonl"
    validation_samples = corpus / "validation_samples.jsonl"
    operational_path = executions if executions.exists() else fallback_operational
    validation_path = validation_samples if validation_samples.exists() else historical_validation
    latest = _latest_mtime(corpus)
    if latest is None:
        latest = max(
            (
                item
                for item in (_file_mtime(fallback_operational), _file_mtime(historical_validation))
                if item is not None
            ),
            default=None,
        )
    return {
        "latest_update_timestamp": _iso(latest),
        "operational_corpus_path": str(operational_path),
        "operational_corpus_records": _jsonl_count(operational_path) if operational_path.is_file() else None,
        "operational_corpus_status": "available" if operational_path.exists() else "missing",
        "validation_corpus_path": str(validation_path),
        "validation_corpus_records": _jsonl_count(validation_path) if validation_path.is_file() else None,
        "validation_corpus_status": "available" if validation_path.exists() else "missing",
    }


def _database_status(root: Path) -> dict[str, Any]:
    operational = root / "data" / "data_ops.sqlite"
    historical = root / "data" / "historical_validation" / "runs.jsonl"
    checkpoints = (
        root / "data" / "acquisition" / "checkpoints.jsonl",
        root / "data" / "onchain" / "runtime" / "checkpoints.jsonl",
    )
    return {
        "checkpoint_database_healthy": any(path.exists() for path in checkpoints),
        "historical_database_reachable": historical.exists(),
        "operational_database_reachable": _sqlite_reachable(operational),
    }


def _validation_status(root: Path, now: datetime) -> dict[str, int]:
    predictions = _read_jsonl(root / "data" / "operational_corpus" / "predictions.jsonl")
    closures = {
        str(item.get("prediction_id"))
        for item in _read_jsonl(root / "data" / "operational_corpus" / "prediction_closures.jsonl")
        if item.get("prediction_id")
    }
    open_predictions = [item for item in predictions if str(item.get("prediction_id")) not in closures]
    due = [item for item in open_predictions if _due(item.get("evaluation_horizon_at"), now)]
    return {
        "closed_predictions": len(closures),
        "due_predictions": len(due),
        "open_predictions": len(open_predictions),
        "pending_evaluations": len(due),
    }


def _provider_status(root: Path) -> dict[str, Any]:
    rows = _read_jsonl(root / "data" / "onchain" / "runtime" / "provider_status.jsonl")
    if not rows:
        return {"provider_health_summary": "unavailable", "providers": []}
    providers = [
        {
            "last_failure": row.get("checked_at") if row.get("failure_type") else None,
            "last_success": row.get("last_successful_request"),
            "latency": row.get("latency_ms"),
            "provider": row.get("provider"),
            "source_id": row.get("state_id") or row.get("endpoint_identity") or row.get("network"),
            "status": row.get("status", "unknown"),
        }
        for row in rows
    ]
    return {"provider_health_summary": f"records={len(rows)}", "providers": providers}


def _logs_status(root: Path) -> dict[str, Any]:
    executions = _read_jsonl(root / "data" / "operational_corpus" / "executions.jsonl")
    if not executions:
        executions = _read_jsonl(root / "data" / "data_ops" / "runs.jsonl")
    latest_success = _latest_by_status(executions, {"succeeded", "partial"})
    latest_warning = next((item for item in reversed(executions) if item.get("warning_summary")), None)
    latest_error = next(
        (
            item
            for item in reversed(executions)
            if item.get("failure_summary") or item.get("failures") or item.get("error")
        ),
        None,
    )
    return {
        "latest_error": _activity(latest_error),
        "latest_successful_activity": _activity(latest_success),
        "latest_warning": _activity(latest_warning),
    }


def _health(
    scheduler: Mapping[str, Any],
    automation: Mapping[str, Any],
    database: Mapping[str, Any],
    corpus: Mapping[str, Any],
) -> HealthState:
    jobs = automation["jobs"]
    if any(job["last_result"] == "failed" or job["failure_count"] for job in jobs):
        return "FAILED"
    if not scheduler["running"] or not database["operational_database_reachable"]:
        return "DEGRADED"
    if not database["historical_database_reachable"] or not database["checkpoint_database_healthy"]:
        return "DEGRADED"
    if corpus["operational_corpus_status"] != "available" or corpus["validation_corpus_status"] != "available":
        return "DEGRADED"
    return "HEALTHY"


def _automation_runs(db_path: Path) -> tuple[dict[str, Any], ...]:
    rows = _persistence_payloads(db_path, "automation-run")
    jsonl_rows = _read_jsonl(db_path.parent / "data_ops" / "runs.jsonl")
    return tuple(row for row in (*rows, *jsonl_rows) if isinstance(row, dict))


def _latest_automation_runs(runs: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    latest: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    for run in runs:
        run_id = run.get("automation_run_id")
        if not run_id:
            passthrough.append(run)
            continue
        key = str(run_id)
        if key not in latest or _sort_time(run) >= _sort_time(latest[key]):
            latest[key] = run
    return (*latest.values(), *passthrough)


def _persistence_payloads(db_path: Path, record_type: str) -> tuple[dict[str, Any], ...]:
    if not db_path.exists():
        return ()
    try:
        with sqlite3.connect(_sqlite_uri(db_path), uri=True) as connection:
            rows = connection.execute(
                "select payload from persistence_records where record_type = ? and deleted_at is null",
                (record_type,),
            ).fetchall()
    except sqlite3.Error:
        return ()
    payloads = []
    for (payload,) in rows:
        try:
            item = json.loads(str(payload))
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            fields = item.get("fields")
            if isinstance(fields, dict):
                item = fields
            payloads.append(item)
    return tuple(payloads)


def _sqlite_count(db_path: Path, table: str) -> int | None:
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(_sqlite_uri(db_path), uri=True) as connection:
            return int(connection.execute(f"select count(*) from {table}").fetchone()[0])
    except sqlite3.Error:
        return None


def _sqlite_reachable(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(_sqlite_uri(db_path), uri=True) as connection:
            connection.execute("select 1").fetchone()
    except sqlite3.Error:
        return False
    return True


def _sqlite_uri(db_path: Path) -> str:
    return f"file:{db_path}?mode=ro&immutable=1"


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        return ()
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return tuple(rows)


def _jsonl_count(path: Path) -> int | None:
    if not path.exists():
        return None
    return len(_read_jsonl(path))


def _latest_mtime(path: Path) -> datetime | None:
    if not path.exists():
        return None
    files = [item for item in path.rglob("*") if item.is_file()]
    if not files:
        return None
    latest = max(item.stat().st_mtime for item in files)
    return datetime.fromtimestamp(latest, UTC)


def _file_mtime(path: Path) -> datetime | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, UTC)


def _latest_by_status(rows: tuple[dict[str, Any], ...], statuses: set[str]) -> dict[str, Any] | None:
    for item in reversed(rows):
        if (item.get("execution_status") or item.get("status")) in statuses:
            return item
    return None


def _sort_time(item: Mapping[str, Any]) -> str:
    return str(
        item.get("created_at")
        or item.get("finished_at")
        or item.get("started_at")
        or item.get("scheduled_for")
        or item.get("effective_at")
        or ""
    )


def _activity(item: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "at": item.get("finished_at") or item.get("recorded_at"),
        "status": item.get("execution_status") or item.get("status"),
        "target_id": item.get("target_id") or item.get("operation") or item.get("job_id"),
    }


def _due(value: object, now: datetime) -> bool:
    if value is None:
        return False
    try:
        horizon = datetime.fromisoformat(str(value))
    except ValueError:
        return False
    if horizon.tzinfo is None:
        horizon = horizon.replace(tzinfo=UTC)
    return horizon <= now


def _process(pid: int, root: Path, runner: CommandRunner) -> dict[str, str | None]:
    result = runner(("ps", "-p", str(pid), "-o", "etime=,command="), root)
    if result.returncode != 0 or not result.stdout.strip():
        return {"command": None, "uptime": None}
    line = result.stdout.strip().splitlines()[0]
    parts = line.strip().split(maxsplit=1)
    if len(parts) == 1:
        return {"command": None, "uptime": parts[0]}
    return {"command": parts[1], "uptime": parts[0]}


def _launch_value(text: str, key: str) -> int | str | None:
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith(f"{key} = "):
            continue
        value = line.split(" = ", 1)[1]
        if value.isdigit():
            return int(value)
        return value
    return None


def _git(root: Path, runner: CommandRunner, *args: str) -> str:
    result = runner(("git", *args), root)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _run_command(command: Sequence[str], cwd: Path) -> CommandResult:
    try:
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    except OSError as exc:
        return CommandResult(127, "", str(exc))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _load_jobs(path: Path) -> tuple[AutomationJob, ...]:
    if not path.exists():
        return ()
    try:
        return load_automation_config(path).jobs
    except (OSError, ValueError):
        return ()


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _version() -> str:
    try:
        return metadata.version("project-hunter")
    except metadata.PackageNotFoundError:
        return "unknown"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
