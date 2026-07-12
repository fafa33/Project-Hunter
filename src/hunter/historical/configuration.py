from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from hunter.historical.models import HistoricalValidationCase


@dataclass(frozen=True)
class HistoricalValidationConfig:
    evaluation_windows: tuple[int, ...]
    minimum_sample_size: int
    success_threshold: float
    failure_threshold: float
    benchmarks: tuple[str, ...]
    calibration_buckets: tuple[float, ...]
    acceptable_freshness: float
    required_evidence: tuple[str, ...]
    maximum_missing_evidence: int
    leakage_rules: tuple[str, ...]
    snapshot_versioning: bool
    challenge_cases: tuple[HistoricalValidationCase, ...]


def load_historical_validation_config(
    path: str | Path = "configs/historical_validation.yaml",
) -> HistoricalValidationConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cases = tuple(_case(item) for item in payload.get("challenge_cases", ()) if isinstance(item, dict))
    return HistoricalValidationConfig(
        evaluation_windows=tuple(int(item) for item in payload.get("evaluation_windows", (7, 30, 90, 180, 365, 730))),
        minimum_sample_size=int(payload.get("minimum_sample_size", 30)),
        success_threshold=float(payload.get("success_threshold", 0.0)),
        failure_threshold=float(payload.get("failure_threshold", -0.5)),
        benchmarks=tuple(str(item) for item in payload.get("benchmarks", ("bitcoin", "ethereum"))),
        calibration_buckets=tuple(
            float(item) for item in payload.get("calibration_buckets", (0.0, 0.25, 0.5, 0.75, 1.0))
        ),
        acceptable_freshness=float(payload.get("acceptable_freshness", 0.5)),
        required_evidence=tuple(str(item) for item in payload.get("required_evidence", ())),
        maximum_missing_evidence=int(payload.get("maximum_missing_evidence", 21)),
        leakage_rules=tuple(str(item) for item in payload.get("leakage_rules", ())),
        snapshot_versioning=bool(payload.get("snapshot_versioning", True)),
        challenge_cases=cases,
    )


def _case(payload: dict[str, Any]) -> HistoricalValidationCase:
    return HistoricalValidationCase(
        case_id=str(payload["case_id"]),
        project_id=str(payload["project_id"]),
        project_slug=str(payload.get("project_slug", payload["project_id"])),
        project_name=str(payload.get("project_name", payload["project_id"])),
        symbol=str(payload.get("symbol", "")),
        sector=str(payload.get("sector", "unknown")),
        case_type=str(payload.get("case_type", "NEUTRAL_CONTROL")),  # type: ignore[arg-type]
        evaluation_timestamp=_datetime(payload["evaluation_timestamp"]),
        historical_cutoff_timestamp=_datetime(
            payload.get("historical_cutoff_timestamp", payload["evaluation_timestamp"])
        ),
        project_lifecycle_state=str(payload.get("project_lifecycle_state", "active")),
        token_lifecycle_state=str(payload.get("token_lifecycle_state", "active")),
        current_project_id=payload.get("current_project_id"),
        historical_token_id=payload.get("historical_token_id"),
        current_token_id=payload.get("current_token_id"),
        migration_ratio=float(payload["migration_ratio"]) if payload.get("migration_ratio") is not None else None,
        migration_date=_datetime(payload["migration_date"]) if payload.get("migration_date") else None,
        continuity_status=str(payload.get("continuity_status", "continuous")),
    )


def _datetime(value: object) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
