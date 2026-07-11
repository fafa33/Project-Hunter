from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from hunter.automation.models import (
    AsOfPolicy,
    AutomationJob,
    AutomationSchedule,
    ConcurrencyPolicy,
    PipelineOptions,
    TargetSelection,
)


@dataclass(frozen=True)
class AutomationConfig:
    enabled: bool = False
    timezone: str = "UTC"
    polling_interval_seconds: int = 60
    jobs: tuple[AutomationJob, ...] = ()


def load_automation_config(path: Path) -> AutomationConfig:
    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        msg = "Automation configuration must be a mapping"
        raise ValueError(msg)
    return automation_config_from_mapping(payload)


def automation_config_from_mapping(payload: dict[str, Any]) -> AutomationConfig:
    timezone = str(payload.get("timezone", "UTC"))
    jobs = tuple(
        _job_from_mapping(item, default_timezone=timezone) for item in payload.get("jobs", ()) if isinstance(item, dict)
    )
    return AutomationConfig(
        enabled=bool(payload.get("enabled", False)),
        timezone=timezone,
        polling_interval_seconds=int(payload.get("polling_interval_seconds", 60)),
        jobs=jobs,
    )


def _job_from_mapping(payload: dict[str, Any], *, default_timezone: str) -> AutomationJob:
    schedule_payload = payload.get("schedule", {})
    target_payload = payload.get("target", {})
    options_payload = payload.get("pipeline_options", {})
    as_of_payload = payload.get("as_of_policy", {})
    concurrency_payload = payload.get("concurrency_policy", {})
    return AutomationJob(
        job_id=str(payload["job_id"]),
        name=str(payload.get("name", payload["job_id"])),
        enabled=bool(payload.get("enabled", True)),
        schedule=AutomationSchedule(
            schedule_type=str(schedule_payload.get("type", "daily")),  # type: ignore[arg-type]
            expression=schedule_payload.get("expression"),
            run_at=_datetime(schedule_payload.get("run_at")),
        ),
        timezone=str(payload.get("timezone", default_timezone)),
        target=TargetSelection(
            target_type=str(target_payload.get("type", "project")),
            target_id=str(target_payload.get("id", "global-crypto")),
        ),
        run_type=str(payload.get("run_type", "scheduled")),
        pipeline_options=PipelineOptions(
            run_intelligence=bool(options_payload.get("run_intelligence", True)),
            run_fusion=bool(options_payload.get("run_fusion", False)),
            run_opportunity_timing=bool(options_payload.get("run_opportunity_timing", False)),
            run_investment_committee=bool(options_payload.get("run_investment_committee", False)),
            selected_engines=tuple(str(item) for item in options_payload.get("selected_engines", ())),
            generate_reports=bool(options_payload.get("generate_reports", False)),
            evaluate_alerts=bool(options_payload.get("evaluate_alerts", False)),
        ),
        persistence_policy=str(payload.get("persistence_policy", "atomic")),
        as_of_policy=AsOfPolicy(
            mode=str(as_of_payload.get("mode", "current")),
            as_of=_datetime(as_of_payload.get("as_of")),
        ),
        timeout_seconds=int(payload["timeout_seconds"]) if payload.get("timeout_seconds") is not None else None,
        concurrency_policy=ConcurrencyPolicy(
            prevent_overlapping=bool(concurrency_payload.get("prevent_overlapping", True)),
            scope=str(concurrency_payload.get("scope", "job_target")),
        ),
        job_kind=str(payload.get("job_kind", "current_state_pipeline")),  # type: ignore[arg-type]
        metadata={str(key): value for key, value in payload.get("metadata", {}).items()},
    )


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
