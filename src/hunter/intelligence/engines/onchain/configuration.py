from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class OnchainEngineConfiguration:
    enabled: bool = True
    project: str = "global-crypto"
    engine_id: str = "onchain-intelligence"
    priority: int = 68
    freshness_days: int = 30
    minimum_historical_depth_days: int = 90
    address_growth_threshold: float = 0.1
    retention_threshold: float = 0.5
    flow_threshold: float = 0.05
    exchange_flow_threshold: float = 0.05
    bridge_flow_threshold: float = 0.05
    staking_flow_threshold: float = 0.05
    holder_concentration_threshold: float = 0.45
    contract_activity_threshold: float = 0.1
    application_concentration_threshold: float = 0.55
    anomaly_threshold: float = 0.45
    detected_anomaly_threshold: float = 0.7
    sybil_risk_threshold: float = 0.35
    wash_activity_threshold: float = 0.35
    bot_risk_threshold: float = 0.35
    attribution_quality_threshold: float = 0.5
    minimum_chain_coverage: int = 1
    duplicate_detection: bool = True
    overlap_detection: bool = True
    preserve_denominations: bool = True
    chain_priorities: dict[str, float] = field(default_factory=dict)
    provider_priorities: dict[str, float] = field(default_factory=dict)
    confidence_weights: dict[str, float] = field(default_factory=dict)


class OnchainEngineConfigurationLoader:
    def __init__(self, default_path: Path | None = None) -> None:
        self.default_path = default_path or Path("configs/onchain_engine.yaml")

    def load(self, path: Path | None = None) -> OnchainEngineConfiguration:
        target = path or self.default_path
        if not target.exists():
            return OnchainEngineConfiguration()
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        return OnchainEngineConfiguration(**_known_fields(raw))


def _known_fields(raw: dict[str, Any]) -> dict[str, Any]:
    allowed = set(OnchainEngineConfiguration.__dataclass_fields__)
    return {key: value for key, value in raw.items() if key in allowed}
