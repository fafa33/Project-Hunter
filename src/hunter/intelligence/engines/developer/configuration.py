from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class DeveloperEngineConfiguration:
    enabled: bool = True
    project: str = "global-crypto"
    engine_id: str = "developer-intelligence"
    priority: int = 30
    core_repositories: tuple[str, ...] = ()
    include_archived_repositories: bool = False
    filter_bots: bool = True
    bot_patterns: tuple[str, ...] = ("bot", "[bot]", "dependabot", "renovate")
    freshness_days: int = 45
    recent_window_days: int = 30
    minimum_historical_depth_days: int = 90
    contributor_concentration_risk: float = 0.65
    indicator_thresholds: dict[str, float] = field(default_factory=dict)
    confidence_weights: dict[str, float] = field(default_factory=dict)


class DeveloperEngineConfigurationLoader:
    def load(self, path: Path | None = None) -> DeveloperEngineConfiguration:
        config_path = path or Path("configs/developer_engine.yaml")
        if not config_path.exists():
            return DeveloperEngineConfiguration()
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return DeveloperEngineConfiguration(
            enabled=bool(raw.get("enabled", True)),
            project=str(raw.get("project", "global-crypto")),
            engine_id=str(raw.get("engine_id", "developer-intelligence")),
            priority=int(raw.get("priority", 30)),
            core_repositories=tuple(str(item) for item in raw.get("core_repositories", ())),
            include_archived_repositories=bool(raw.get("include_archived_repositories", False)),
            filter_bots=bool(raw.get("filter_bots", True)),
            bot_patterns=tuple(
                str(item).lower() for item in raw.get("bot_patterns", ("bot", "[bot]", "dependabot", "renovate"))
            ),
            freshness_days=int(raw.get("freshness_days", 45)),
            recent_window_days=int(raw.get("recent_window_days", 30)),
            minimum_historical_depth_days=int(raw.get("minimum_historical_depth_days", 90)),
            contributor_concentration_risk=float(raw.get("contributor_concentration_risk", 0.65)),
            indicator_thresholds=_float_mapping(raw.get("indicator_thresholds", {})),
            confidence_weights=_float_mapping(raw.get("confidence_weights", {})),
        )


def _float_mapping(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(key): float(item) for key, item in value.items()}
