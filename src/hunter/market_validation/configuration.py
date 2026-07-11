from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from hunter.market_validation.models import ProjectValidationTarget


@dataclass(frozen=True)
class MarketValidationConfig:
    run_id: str = "v1-real-market-validation"
    effective_at: datetime = datetime(2026, 7, 11, tzinfo=UTC)
    output_directory: str = "reports/market_validation"
    project_universe: tuple[ProjectValidationTarget, ...] = ()
    report_formats: tuple[str, ...] = ("csv", "markdown", "json")

    def __post_init__(self) -> None:
        if self.effective_at.tzinfo is None:
            msg = "effective_at must be timezone-aware"
            raise ValueError(msg)
        object.__setattr__(self, "effective_at", self.effective_at.astimezone(UTC))
        object.__setattr__(self, "project_universe", tuple(self.project_universe))
        object.__setattr__(self, "report_formats", tuple(sorted(str(item) for item in self.report_formats)))
        if len(self.project_universe) < 50:
            msg = "market validation requires at least 50 configured projects"
            raise ValueError(msg)


def load_market_validation_config(path: str | Path = "configs/market_validation.yaml") -> MarketValidationConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        msg = "market validation configuration must be a mapping"
        raise ValueError(msg)
    return market_validation_config_from_mapping(payload)


def market_validation_config_from_mapping(payload: dict[str, Any]) -> MarketValidationConfig:
    projects = tuple(
        ProjectValidationTarget(
            project_id=str(item["project_id"]),
            name=str(item.get("name", item["project_id"])),
            sector=str(item.get("sector", "unknown")),
            metadata={str(k): v for k, v in item.get("metadata", {}).items()},
        )
        for item in payload.get("project_universe", ())
        if isinstance(item, dict)
    )
    return MarketValidationConfig(
        run_id=str(payload.get("run_id", "v1-real-market-validation")),
        effective_at=_datetime(payload.get("effective_at")),
        output_directory=str(payload.get("output_directory", "reports/market_validation")),
        project_universe=projects,
        report_formats=tuple(str(item) for item in payload.get("report_formats", ("csv", "markdown", "json"))),
    )


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value is None:
        parsed = datetime(2026, 7, 11, tzinfo=UTC)
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        msg = "effective_at must be timezone-aware"
        raise ValueError(msg)
    return parsed.astimezone(UTC)
