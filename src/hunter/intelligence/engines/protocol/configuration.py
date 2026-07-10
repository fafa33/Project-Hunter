from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ProtocolEngineConfiguration:
    enabled: bool = True
    project: str = "global-crypto"
    protocol: str = "unknown"
    engine_id: str = "protocol-intelligence"
    priority: int = 40
    core_chains: tuple[str, ...] = ()
    core_deployments: tuple[str, ...] = ()
    freshness_days: int = 45
    recent_window_days: int = 30
    minimum_historical_depth_days: int = 90
    activity_quality_threshold: float = 0.65
    user_retention_threshold: float = 0.45
    organic_tvl_threshold: float = 0.60
    liquidity_depth_threshold: float = 0.50
    concentration_risk_threshold: float = 0.60
    incident_severity_threshold: float = 0.50
    incentive_dependence_threshold: float = 0.50
    emissions_dependence_threshold: float = 0.50
    treasury_runway_months_threshold: float = 12.0
    confidence_weights: dict[str, float] = field(default_factory=dict)
    provider_priorities: dict[str, int] = field(default_factory=dict)


class ProtocolEngineConfigurationLoader:
    def load(self, path: Path | None = None) -> ProtocolEngineConfiguration:
        config_path = path or Path("configs/protocol_engine.yaml")
        if not config_path.exists():
            return ProtocolEngineConfiguration()
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return ProtocolEngineConfiguration(
            enabled=bool(raw.get("enabled", True)),
            project=str(raw.get("project", "global-crypto")),
            protocol=str(raw.get("protocol", "unknown")),
            engine_id=str(raw.get("engine_id", "protocol-intelligence")),
            priority=int(raw.get("priority", 40)),
            core_chains=tuple(str(item).lower() for item in raw.get("core_chains", ())),
            core_deployments=tuple(str(item).lower() for item in raw.get("core_deployments", ())),
            freshness_days=int(raw.get("freshness_days", 45)),
            recent_window_days=int(raw.get("recent_window_days", 30)),
            minimum_historical_depth_days=int(raw.get("minimum_historical_depth_days", 90)),
            activity_quality_threshold=float(raw.get("activity_quality_threshold", 0.65)),
            user_retention_threshold=float(raw.get("user_retention_threshold", 0.45)),
            organic_tvl_threshold=float(raw.get("organic_tvl_threshold", 0.60)),
            liquidity_depth_threshold=float(raw.get("liquidity_depth_threshold", 0.50)),
            concentration_risk_threshold=float(raw.get("concentration_risk_threshold", 0.60)),
            incident_severity_threshold=float(raw.get("incident_severity_threshold", 0.50)),
            incentive_dependence_threshold=float(raw.get("incentive_dependence_threshold", 0.50)),
            emissions_dependence_threshold=float(raw.get("emissions_dependence_threshold", 0.50)),
            treasury_runway_months_threshold=float(raw.get("treasury_runway_months_threshold", 12.0)),
            confidence_weights=_float_mapping(raw.get("confidence_weights", {})),
            provider_priorities={str(key): int(value) for key, value in raw.get("provider_priorities", {}).items()},
        )


def _float_mapping(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(key): float(item) for key, item in value.items()}
