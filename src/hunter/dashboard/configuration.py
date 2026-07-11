from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class DashboardConfig:
    enabled: bool = False
    title: str = "Project Hunter Dashboard"
    output_path: str = "dashboard.html"
    sqlite_path: str | None = None
    max_rows: int = 20
    include_automation: bool = True
    include_pipeline: bool = True
    include_fusion: bool = True
    include_opportunity_timing: bool = True
    include_committee: bool = True

    def __post_init__(self) -> None:
        if self.max_rows < 1:
            msg = "max_rows must be positive"
            raise ValueError(msg)


def load_dashboard_config(path: Path) -> DashboardConfig:
    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        msg = "Dashboard configuration must be a mapping"
        raise ValueError(msg)
    return dashboard_config_from_mapping(payload)


def dashboard_config_from_mapping(payload: dict[str, Any]) -> DashboardConfig:
    return DashboardConfig(
        enabled=bool(payload.get("enabled", False)),
        title=str(payload.get("title", "Project Hunter Dashboard")),
        output_path=str(payload.get("output_path", "dashboard.html")),
        sqlite_path=str(payload["sqlite_path"]) if payload.get("sqlite_path") is not None else None,
        max_rows=int(payload.get("max_rows", 20)),
        include_automation=bool(payload.get("include_automation", True)),
        include_pipeline=bool(payload.get("include_pipeline", True)),
        include_fusion=bool(payload.get("include_fusion", True)),
        include_opportunity_timing=bool(payload.get("include_opportunity_timing", True)),
        include_committee=bool(payload.get("include_committee", True)),
    )
