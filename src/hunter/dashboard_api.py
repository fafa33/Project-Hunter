from __future__ import annotations

import json
import platform
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.dashboard_prediction_evaluation import build_prediction_evaluation_projection
from hunter.operational_status import CommandRunner, build_status

SCHEMA_VERSION = "dashboard-api.v1"


def build_dashboard_api(
    *,
    root: Path | None = None,
    config_path: Path | None = None,
    now: datetime | None = None,
    command_runner: CommandRunner | None = None,
    prediction_evaluation_config_path: Path | None = None,
) -> dict[str, Any]:
    observed_at = now or datetime.now(UTC)
    repository_root = root or Path.cwd()
    status = build_status(root=root, config_path=config_path, now=observed_at, command_runner=command_runner)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": observed_at.isoformat(),
        "system": _system(status),
        "scheduler": _scheduler(status),
        "automation": _automation(status),
        "jobs": _jobs(status),
        "providers": _providers(status),
        "discovery": _discovery(status),
        "validation": _validation(status),
        "predictions": _predictions(
            status,
            canonical_evaluation=build_prediction_evaluation_projection(
                config_path=prediction_evaluation_config_path
                or repository_root / "configs" / "prediction_evaluation_persistence.yaml",
                root=repository_root,
                as_of=observed_at,
            ),
        ),
        "corpus": _corpus(status),
        "database": _database(status),
        "logs": _logs(status),
        "health": _health(status),
    }


def render_dashboard_api(payload: Mapping[str, Any], *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(payload, indent=2, sort_keys=True)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _system(status: Mapping[str, Any]) -> dict[str, Any]:
    general = status["general"]
    return {
        "branch": general["branch"],
        "commit": general["commit"],
        "platform": platform.platform(),
        "python_version": general["python_version"],
        "repository_path": general["repository_path"],
        "runtime_path": general["runtime_path"],
        "version": general["version"],
    }


def _scheduler(status: Mapping[str, Any]) -> dict[str, Any]:
    scheduler = status["scheduler"]
    return {
        "health": scheduler["health"],
        "launch_status": scheduler["launchagent_status"],
        "pid": scheduler["pid"],
        "running": scheduler["running"],
        "uptime": scheduler["uptime"],
    }


def _automation(status: Mapping[str, Any]) -> dict[str, Any]:
    jobs = status["automation"]["jobs"]
    return {
        "enabled_jobs": sum(1 for job in jobs if job["enabled"]),
        "job_count": status["automation"]["job_count"],
        "running_jobs": sum(1 for job in jobs if job["running"]),
    }


def _jobs(status: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "enabled": job["enabled"],
            "failure_count": job["failure_count"],
            "job_id": job["job_id"],
            "last_execution": job["last_execution"],
            "last_result": job["last_result"],
            "name": job["name"],
            "next_execution": job["next_scheduled_execution"],
            "running": job["running"],
        }
        for job in status["automation"]["jobs"]
    ]


def _providers(status: Mapping[str, Any]) -> dict[str, Any]:
    providers = status["providers"]
    return {
        "items": providers.get("providers", ()),
        "summary": providers["provider_health_summary"],
    }


def _discovery(status: Mapping[str, Any]) -> dict[str, Any]:
    discovery = status["discovery"]
    return {
        "active_sources": discovery.get("active_sources", 0),
        "pending_jobs": discovery["pending_discovery_jobs"],
        "queue_size": discovery["candidate_queue_size"],
        "source_health": discovery["source_health_summary"],
    }


def _validation(status: Mapping[str, Any]) -> dict[str, Any]:
    validation = status["validation"]
    return {
        "closed_predictions": validation["closed_predictions"],
        "due_evaluations": validation["due_predictions"],
        "open_predictions": validation["open_predictions"],
        "pending_evaluations": validation["pending_evaluations"],
    }


def _predictions(status: Mapping[str, Any], *, canonical_evaluation: Mapping[str, Any]) -> dict[str, Any]:
    validation = status["validation"]
    return {
        "closed": validation["closed_predictions"],
        "due": validation["due_predictions"],
        "open": validation["open_predictions"],
        "canonical_evaluation": dict(canonical_evaluation),
    }


def _corpus(status: Mapping[str, Any]) -> dict[str, Any]:
    corpus = status["corpus"]
    return {
        "last_update": corpus["latest_update_timestamp"],
        "operational": _operational_corpus(status),
        "validation": _validation_corpus(status),
    }


def _operational_corpus(status: Mapping[str, Any]) -> dict[str, Any]:
    corpus = status["corpus"]
    return _corpus_source(
        last_update=corpus["latest_update_timestamp"],
        path=corpus.get("operational_corpus_path"),
        record_count=corpus.get("operational_corpus_records"),
        source_status=corpus["operational_corpus_status"],
    )


def _validation_corpus(status: Mapping[str, Any]) -> dict[str, Any]:
    corpus = status["corpus"]
    return _corpus_source(
        last_update=corpus["latest_update_timestamp"],
        path=corpus.get("validation_corpus_path"),
        record_count=corpus.get("validation_corpus_records"),
        source_status=corpus["validation_corpus_status"],
    )


def _corpus_source(
    *,
    last_update: object,
    path: object,
    record_count: object,
    source_status: object,
) -> dict[str, Any]:
    state, error = _corpus_source_state(source_status=source_status, record_count=record_count)
    return {
        "authority_classification": "operational-only",
        "error": error,
        "last_update": last_update,
        "path": path,
        "read_only": True,
        "record_count": record_count,
        "status": state,
    }


def _corpus_source_state(*, source_status: object, record_count: object) -> tuple[str, str | None]:
    if source_status == "missing":
        return "unavailable", None
    if source_status != "available":
        return "error", f"unsupported source status: {source_status!r}"
    if not isinstance(record_count, int) or isinstance(record_count, bool) or record_count < 0:
        return "error", "source is present but its record count is unavailable"
    if record_count == 0:
        return "empty", None
    return "available", None


def _database(status: Mapping[str, Any]) -> dict[str, Any]:
    database = status["database"]
    reachable = database["operational_database_reachable"] and database["historical_database_reachable"]
    return {
        "checkpoint_status": "healthy" if database["checkpoint_database_healthy"] else "missing",
        "health": "healthy" if reachable and database["checkpoint_database_healthy"] else "degraded",
        "reachable": reachable,
        "repositories": {
            "checkpoint": database["checkpoint_database_healthy"],
            "historical": database["historical_database_reachable"],
            "operational": database["operational_database_reachable"],
        },
    }


def _logs(status: Mapping[str, Any]) -> dict[str, Any]:
    logs = status["logs"]
    return {
        "latest_activity": logs["latest_successful_activity"],
        "latest_error": logs["latest_error"],
        "latest_warning": logs["latest_warning"],
    }


def _health(status: Mapping[str, Any]) -> dict[str, Any]:
    state = status["health"]
    explanations = []
    if not status["scheduler"]["running"]:
        explanations.append("scheduler is not running")
    if not status["database"]["operational_database_reachable"]:
        explanations.append("operational database is unreachable")
    if not status["database"]["historical_database_reachable"]:
        explanations.append("historical database is unreachable")
    if not status["database"]["checkpoint_database_healthy"]:
        explanations.append("checkpoint database is unavailable")
    operational_state, _ = _corpus_source_state(
        source_status=status["corpus"]["operational_corpus_status"],
        record_count=status["corpus"].get("operational_corpus_records"),
    )
    if operational_state in {"unavailable", "error"}:
        explanations.append("operational corpus is unavailable")
    validation_state, _ = _corpus_source_state(
        source_status=status["corpus"]["validation_corpus_status"],
        record_count=status["corpus"].get("validation_corpus_records"),
    )
    if validation_state in {"unavailable", "error"}:
        explanations.append("validation corpus is unavailable")
    failed_jobs = [job["job_id"] for job in status["automation"]["jobs"] if job["last_result"] == "failed"]
    if failed_jobs:
        explanations.append(f"failed jobs: {','.join(sorted(failed_jobs))}")
    if not explanations:
        explanations.append("runtime operational checks passed")
    return {
        "explanation": explanations,
        "state": state,
        "analytical_stores": status["analytical_stores"],
    }
