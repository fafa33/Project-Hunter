from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from hunter.automation.models import (
    AutomationJob,
    AutomationSchedule,
    ConcurrencyPolicy,
    PipelineOptions,
    TargetSelection,
)

COMPETITIVE_AUTOMATION_JOBS = (
    ("competitive-input-refresh", "Competitive Input Refresh", "daily"),
    ("competitive-relationship-build", "Competitive Relationship Build", "daily"),
    ("competitive-algorithmic-peer-set-refresh", "Competitive Algorithmic Peer Set Refresh", "daily"),
    ("competitive-conflict-detection", "Competitive Conflict Detection", "daily"),
    ("competitive-reporting", "Competitive Reporting", "daily"),
)


@dataclass(frozen=True)
class CompetitiveAutomationResult:
    installed: int
    created: int
    jobs: tuple[str, ...]


class CompetitiveAutomationManager:
    def __init__(self, path: str | Path = "configs/automation.yaml") -> None:
        self.path = Path(path)

    def job_definitions(self) -> tuple[AutomationJob, ...]:
        return tuple(_job(job_id, name, schedule) for job_id, name, schedule in COMPETITIVE_AUTOMATION_JOBS)

    def install(self) -> CompetitiveAutomationResult:
        payload = self._read_payload()
        existing = tuple(item for item in payload.get("jobs", ()) if isinstance(item, dict))
        before = {str(item["job_id"]) for item in existing if item.get("job_id") is not None}
        merged = {str(item["job_id"]): item for item in existing if item.get("job_id") is not None}
        for job in self.job_definitions():
            merged.setdefault(job.job_id, _job_to_config(job, installed_at=datetime.now(tz=UTC)))
        payload["enabled"] = bool(payload.get("enabled", True))
        payload["timezone"] = str(payload.get("timezone", "UTC"))
        payload["polling_interval_seconds"] = int(payload.get("polling_interval_seconds", 60))
        payload["jobs"] = list(merged.values())
        self._write_payload(payload)
        installed_jobs = tuple(sorted(job_id for job_id in merged if job_id.startswith("competitive-")))
        return CompetitiveAutomationResult(
            installed=len(installed_jobs),
            created=len(set(installed_jobs) - before),
            jobs=installed_jobs,
        )

    def status(self) -> tuple[dict[str, Any], ...]:
        jobs = tuple(item for item in self._read_payload().get("jobs", ()) if isinstance(item, dict))
        return tuple(job for job in jobs if str(job.get("job_id", "")).startswith("competitive-"))

    def _read_payload(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        payload = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            msg = "automation configuration must be a mapping"
            raise ValueError(msg)
        return dict(payload)

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _job(job_id: str, name: str, schedule_type: str) -> AutomationJob:
    return AutomationJob(
        job_id=job_id,
        name=name,
        enabled=True,
        schedule=AutomationSchedule(schedule_type=schedule_type),  # type: ignore[arg-type]
        timezone="UTC",
        target=TargetSelection("operation", job_id),
        run_type="competitive_intelligence_pipeline",
        pipeline_options=PipelineOptions(
            run_intelligence=False,
            run_fusion=False,
            run_opportunity_timing=False,
            generate_reports=job_id.endswith("reporting"),
        ),
        persistence_policy="atomic",
        concurrency_policy=ConcurrencyPolicy(prevent_overlapping=True, scope="job_target"),
        job_kind="generate_reports" if job_id.endswith("reporting") else "ingest_project_data",
        metadata={
            "pipeline_owner": "competitive_intelligence_pipeline",
            "scheduler_role": "operational_only",
            "operation": job_id,
        },
    )


def _job_to_config(job: AutomationJob, *, installed_at: datetime) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "job_id": job.job_id,
        "name": job.name,
        "enabled": job.enabled,
        "job_kind": job.job_kind,
        "schedule": {"type": job.schedule.schedule_type},
        "timezone": job.timezone,
        "target": {"type": job.target.target_type, "id": job.target.target_id},
        "run_type": job.run_type,
        "pipeline_options": {
            "run_intelligence": job.pipeline_options.run_intelligence,
            "run_fusion": job.pipeline_options.run_fusion,
            "run_opportunity_timing": job.pipeline_options.run_opportunity_timing,
            "run_investment_committee": job.pipeline_options.run_investment_committee,
            "selected_engines": list(job.pipeline_options.selected_engines),
            "generate_reports": job.pipeline_options.generate_reports,
            "evaluate_alerts": job.pipeline_options.evaluate_alerts,
        },
        "persistence_policy": job.persistence_policy,
        "as_of_policy": {"mode": job.as_of_policy.mode},
        "timeout_seconds": job.timeout_seconds or 3600,
        "concurrency_policy": {
            "prevent_overlapping": job.concurrency_policy.prevent_overlapping,
            "scope": job.concurrency_policy.scope,
        },
        "metadata": {
            **dict(job.metadata),
            "installed_at": installed_at.isoformat(),
        },
    }
    return payload
