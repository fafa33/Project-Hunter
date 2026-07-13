from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import yaml

from hunter.onchain.configuration import OnChainConfig
from hunter.onchain.engine import CapitalFlowEngine
from hunter.onchain.repository import OnChainRepository

ONCHAIN_AUTOMATION_JOBS = (
    ("onchain-provider-health", "provider_health_check", "*/10 * * * *"),
    ("onchain-incremental-sync", "incremental_onchain_sync", "*/15 * * * *"),
    ("onchain-hourly-snapshot", "hourly_snapshot", "0 * * * *"),
    ("onchain-daily-consolidation", "daily_consolidation", "0 0 * * *"),
    ("onchain-surface-revalidation", "weekly_surface_revalidation", "0 0 * * 1"),
)


@dataclass(frozen=True)
class OnChainAutomationResult:
    installed: int
    created: int
    jobs: tuple[str, ...]


class OnChainAutomationManager:
    def __init__(self, config: OnChainConfig, repository: OnChainRepository | None = None) -> None:
        self.config = config
        self.repository = repository or OnChainRepository(
            str(config.retention.get("runtime_root", "data/onchain/runtime"))
        )
        self.path = self.repository.root / "automation.yaml"

    def install(self) -> OnChainAutomationResult:
        existing = self._read()
        before = {str(item["job_id"]) for item in existing}
        jobs = []
        for job_id, job_type, schedule in ONCHAIN_AUTOMATION_JOBS:
            jobs.append(
                {
                    "job_id": job_id,
                    "job_type": job_type,
                    "schedule": schedule,
                    "enabled": True,
                    "prevent_overlapping": True,
                    "installed_at": datetime.now(tz=UTC).isoformat(),
                    "last_attempted_run": None,
                    "last_successful_run": None,
                    "last_failure": None,
                    "next_scheduled_run": _next(schedule).isoformat(),
                }
            )
        merged = {str(item["job_id"]): item for item in existing}
        for job in jobs:
            merged.setdefault(str(job["job_id"]), job)
        self._write(tuple(merged.values()))
        return OnChainAutomationResult(
            installed=len(merged),
            created=len(set(merged) - before),
            jobs=tuple(sorted(merged)),
        )

    def status(self) -> tuple[dict[str, Any], ...]:
        rows = []
        checkpoints = {str(row.get("project")): row for row in self.repository.checkpoints()}
        states = self.repository.provider_states()
        active_provider = next((row.get("endpoint_identity") for row in states if row.get("status") == "healthy"), None)
        for job in self._read():
            rows.append(
                {
                    **job,
                    "active_provider": active_provider,
                    "checkpoint": ",".join(
                        f"{project}:{row.get('block_number')}" for project, row in checkpoints.items()
                    )
                    or None,
                    "project_freshness": "live" if checkpoints else "unavailable",
                    "missed_windows": _missed_windows(job),
                }
            )
        return tuple(rows)

    def run_now(self) -> tuple[dict[str, Any], ...]:
        jobs = list(self._read())
        now = datetime.now(tz=UTC).isoformat()
        snapshots = CapitalFlowEngine(self.config, repository=self.repository).sync()
        status = "succeeded" if any(snapshot.status == "live" for snapshot in snapshots) else "partial"
        for job in jobs:
            job["last_attempted_run"] = now
            job["last_successful_run"] = now if status == "succeeded" else job.get("last_successful_run")
            job["last_failure"] = None if status == "succeeded" else "provider_unavailable"
        self._write(tuple(jobs))
        return tuple({"job_id": job["job_id"], "status": status} for job in jobs)

    def failures(self) -> tuple[dict[str, Any], ...]:
        return tuple(job for job in self._read() if job.get("last_failure"))

    def set_enabled(self, enabled: bool) -> None:
        jobs = list(self._read())
        for job in jobs:
            job["enabled"] = enabled
        self._write(tuple(jobs))

    def _read(self) -> tuple[dict[str, Any], ...]:
        if not self.path.exists():
            return ()
        payload = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return tuple(dict(item) for item in payload.get("jobs", ()))

    def _write(self, jobs: tuple[dict[str, Any], ...]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump({"jobs": list(jobs)}, sort_keys=True), encoding="utf-8")


def worker_startup_command(config_path: str = "configs/automation.yaml") -> str:
    return f".venv/bin/hunter --config {config_path} automation start --max-iterations 0"


def _next(schedule: str) -> datetime:
    now = datetime.now(tz=UTC)
    if schedule.startswith("*/10"):
        return now + timedelta(minutes=10)
    if schedule.startswith("*/15"):
        return now + timedelta(minutes=15)
    if schedule == "0 * * * *":
        return now + timedelta(hours=1)
    if schedule == "0 0 * * *":
        return now + timedelta(days=1)
    return now + timedelta(days=7)


def _missed_windows(job: dict[str, Any]) -> int:
    attempted = job.get("last_attempted_run")
    if not attempted:
        return 0
    then = datetime.fromisoformat(str(attempted))
    hours = max(int((datetime.now(tz=UTC) - then).total_seconds() // 3600), 0)
    return hours
