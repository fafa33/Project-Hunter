from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from hunter.automation import load_automation_config

SUFFICIENCY_JOB_IDS = (
    "sufficiency-requirement-validation",
    "sufficiency-availability-refresh",
    "sufficiency-stale-evidence-detection",
    "sufficiency-cross-source-disagreement-detection",
    "sufficiency-assessment-refresh",
    "sufficiency-degraded-mode-report-refresh",
)


class DataSufficiencyAutomationManager:
    def __init__(self, path: str | Path = "configs/automation.yaml") -> None:
        self.path = Path(path)

    def install(self) -> tuple[str, ...]:
        payload = _load_yaml(self.path)
        existing_jobs = {str(job.get("job_id")): job for job in payload.get("jobs", ()) if isinstance(job, dict)}
        for job in _sufficiency_jobs():
            existing_jobs[str(job["job_id"])] = job
        payload["enabled"] = True
        payload["timezone"] = str(payload.get("timezone", "UTC"))
        payload["polling_interval_seconds"] = int(payload.get("polling_interval_seconds", 60))
        payload["jobs"] = list(existing_jobs.values())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return SUFFICIENCY_JOB_IDS

    def status(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"installed_jobs": 0, "expected_jobs": len(SUFFICIENCY_JOB_IDS), "jobs": []}
        config = load_automation_config(self.path)
        jobs = tuple(job for job in config.jobs if job.job_id in SUFFICIENCY_JOB_IDS)
        return {
            "installed_jobs": len(jobs),
            "expected_jobs": len(SUFFICIENCY_JOB_IDS),
            "jobs": [
                {
                    "job_id": job.job_id,
                    "enabled": job.enabled,
                    "schedule": job.schedule.schedule_type,
                    "job_kind": job.job_kind,
                    "target": job.target.target_id,
                    "scheduler_role": job.metadata.get("scheduler_role"),
                    "provider_dependency": job.metadata.get("provider_dependency"),
                }
                for job in jobs
            ],
        }


def _sufficiency_jobs() -> tuple[dict[str, Any], ...]:
    return (
        _job("sufficiency-requirement-validation", "Data Sufficiency requirement validation", "daily"),
        _job("sufficiency-availability-refresh", "Data Sufficiency availability refresh", "every_6_hours"),
        _job("sufficiency-stale-evidence-detection", "Data Sufficiency stale evidence detection", "every_6_hours"),
        _job(
            "sufficiency-cross-source-disagreement-detection",
            "Data Sufficiency cross-source disagreement detection",
            "daily",
        ),
        _job("sufficiency-assessment-refresh", "Data Sufficiency assessment refresh", "daily"),
        _job("sufficiency-degraded-mode-report-refresh", "Data Sufficiency degraded-mode report refresh", "daily"),
    )


def _job(job_id: str, name: str, schedule_type: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "name": name,
        "enabled": True,
        "job_kind": "generate_reports",
        "schedule": {"type": schedule_type},
        "timezone": "UTC",
        "target": {"type": "operation", "id": job_id},
        "run_type": "scheduled",
        "pipeline_options": {
            "run_intelligence": False,
            "run_fusion": False,
            "run_opportunity_timing": False,
            "run_investment_committee": False,
            "generate_reports": False,
        },
        "persistence_policy": "atomic",
        "as_of_policy": {"mode": "current"},
        "timeout_seconds": 1800,
        "concurrency_policy": {"prevent_overlapping": True, "scope": "job_target"},
        "metadata": {
            "scheduler_role": "operational_only",
            "provider_dependency": False,
            "fabricates_evidence": False,
            "records_metrics": True,
        },
    }


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"enabled": True, "timezone": "UTC", "polling_interval_seconds": 60, "jobs": []}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        msg = "automation config must be a mapping"
        raise ValueError(msg)
    payload.setdefault("jobs", [])
    return payload
