from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DISCOVERY_JOB_IDS = (
    "discovery-source-health",
    "discovery-market-run",
    "discovery-market-run-backfill",
    "discovery-identity-resolution",
    "discovery-candidate-screening",
    "discovery-queue-refresh",
)


def install_discovery_jobs(path: Path = Path("configs/automation.yaml")) -> tuple[str, ...]:
    payload = _load_yaml(path)
    existing_jobs = {str(job.get("job_id")): job for job in payload.get("jobs", ()) if isinstance(job, dict)}
    for job in _discovery_jobs():
        existing_jobs[str(job["job_id"])] = job
    payload["enabled"] = True
    payload["timezone"] = str(payload.get("timezone", "UTC"))
    payload["polling_interval_seconds"] = int(payload.get("polling_interval_seconds", 60))
    payload["jobs"] = list(existing_jobs.values())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return DISCOVERY_JOB_IDS


def discovery_automation_status(path: Path = Path("configs/automation.yaml")) -> dict[str, object]:
    payload = _load_yaml(path)
    jobs = tuple(
        job for job in payload.get("jobs", ()) if isinstance(job, dict) and job.get("job_id") in DISCOVERY_JOB_IDS
    )
    return {
        "installed_jobs": len(jobs),
        "expected_jobs": len(DISCOVERY_JOB_IDS),
        "job_ids": tuple(str(job["job_id"]) for job in jobs),
    }


def _discovery_jobs() -> tuple[dict[str, Any], ...]:
    return (
        _job("discovery-source-health", "Discovery source health check", "hourly", "discovery_source_health"),
        _job(
            "discovery-market-run",
            "Global discovery market run",
            "cron",
            "discovery_market_run",
            {
                "free_capacity_observation_window_hours": 24,
                "discovery_modes": ["incremental"],
                "budget_aware_resumable": True,
                "fair_network_rotation": True,
                "no_parallel_daemon": True,
                "indefinite_operation": True,
            },
            expression="*/15 * * * *",
        ),
        _job(
            "discovery-market-run-backfill",
            "Bounded discovery market backfill",
            "cron",
            "discovery_market_run_backfill",
            {
                "discovery_modes": ["backfill"],
                "bounded_contracts_per_cycle": 5,
                "does_not_starve_incremental": True,
            },
            expression="0 * * * *",
        ),
        # Staggered six-hour handoff: discovery -> identity -> screening -> queue.
        _job(
            "discovery-identity-resolution",
            "Bounded candidate identity resolution",
            "cron",
            "discovery_identity_resolve",
            {
                "evidence_gated": True,
                "bounded_fair_batch": True,
                "no_parallel_daemon": True,
            },
            expression="5 */6 * * *",
        ),
        _job(
            "discovery-candidate-screening",
            "Discovery candidate screening",
            "cron",
            "discovery_screen",
            expression="10 */6 * * *",
        ),
        _job(
            "discovery-queue-refresh",
            "Discovery queue refresh",
            "cron",
            "discovery_queue_refresh",
            expression="15 */6 * * *",
        ),
    )


def _job(
    job_id: str,
    name: str,
    schedule_type: str,
    operation: str,
    metadata: dict[str, Any] | None = None,
    *,
    expression: str | None = None,
) -> dict[str, Any]:
    schedule: dict[str, Any] = {"type": schedule_type}
    if expression is not None:
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
        "timeout_seconds": 3600,
        "concurrency_policy": {"prevent_overlapping": True, "scope": "job_target"},
        "metadata": {"operation": operation, "records_metrics": True, **(metadata or {})},
    }


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        msg = "automation configuration must be a mapping"
        raise ValueError(msg)
    return payload
