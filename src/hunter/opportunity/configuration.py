from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class OpportunityTimingConfig:
    enabled: bool = False
    required_historical_depth: int = 3
    min_confirmation_groups: int = 2
    required_categories: tuple[str, ...] = ("macro", "whale", "developer", "protocol", "news", "narrative", "social", "on-chain")
    phase_thresholds: tuple[tuple[str, float], ...] = (
        ("invalidated", 0.0),
        ("too_early", 20.0),
        ("forming", 40.0),
        ("early_entry", 60.0),
        ("confirmed_entry", 75.0),
        ("expansion", 90.0),
    )
    window_thresholds: tuple[tuple[str, float], ...] = (
        ("closed", 20.0),
        ("watch", 40.0),
        ("opening", 60.0),
        ("open", 75.0),
        ("strengthening", 90.0),
    )
    risk_weights: tuple[tuple[str, float], ...] = field(default_factory=lambda: (("contradiction", 0.25), ("missing_evidence", 0.2), ("dependency", 0.2), ("divergence", 0.2), ("insufficient_history", 0.15)))
    confidence_weights: tuple[tuple[str, float], ...] = field(default_factory=lambda: (("history", 0.2), ("diversity", 0.2), ("coverage", 0.2), ("independence", 0.15), ("freshness", 0.1), ("fusion", 0.15)))
    contradiction_penalty: float = 18.0
    missing_evidence_penalty: float = 3.0
    divergence_penalty: float = 15.0
    acceleration_bonus: float = 12.0
    confirmation_bonus: float = 18.0
    persistence_bonus: float = 10.0
    model_version: str = "opportunity-timing-model-v1"

    def __post_init__(self) -> None:
        for name in ("required_categories",):
            object.__setattr__(self, name, tuple(str(item) for item in getattr(self, name)))
        for name in ("phase_thresholds", "window_thresholds", "risk_weights", "confidence_weights"):
            raw = getattr(self, name)
            values = raw.items() if isinstance(raw, Mapping) else raw
            object.__setattr__(self, name, tuple(sorted((str(key), float(value)) for key, value in values)))


def load_opportunity_timing_config(path: Path) -> OpportunityTimingConfig:
    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        msg = "Opportunity Timing configuration must be a mapping"
        raise ValueError(msg)
    return opportunity_timing_config_from_mapping(payload)


def opportunity_timing_config_from_mapping(payload: dict[str, Any]) -> OpportunityTimingConfig:
    return OpportunityTimingConfig(
        enabled=bool(payload.get("enabled", False)),
        required_historical_depth=int(payload.get("required_historical_depth", 3)),
        min_confirmation_groups=int(payload.get("min_confirmation_groups", 2)),
        required_categories=tuple(str(item) for item in payload.get("required_categories", OpportunityTimingConfig().required_categories)),
        phase_thresholds=_mapping_tuple(payload.get("phase_thresholds"), OpportunityTimingConfig().phase_thresholds),
        window_thresholds=_mapping_tuple(payload.get("window_thresholds"), OpportunityTimingConfig().window_thresholds),
        risk_weights=_mapping_tuple(payload.get("risk_weights"), OpportunityTimingConfig().risk_weights),
        confidence_weights=_mapping_tuple(payload.get("confidence_weights"), OpportunityTimingConfig().confidence_weights),
        contradiction_penalty=float(payload.get("contradiction_penalty", 18.0)),
        missing_evidence_penalty=float(payload.get("missing_evidence_penalty", 3.0)),
        divergence_penalty=float(payload.get("divergence_penalty", 15.0)),
        acceleration_bonus=float(payload.get("acceleration_bonus", 12.0)),
        confirmation_bonus=float(payload.get("confirmation_bonus", 18.0)),
        persistence_bonus=float(payload.get("persistence_bonus", 10.0)),
        model_version=str(payload.get("model_version", "opportunity-timing-model-v1")),
    )


def _mapping_tuple(value: Any, default: tuple[tuple[str, float], ...]) -> tuple[tuple[str, float], ...]:
    if isinstance(value, dict):
        return tuple(sorted((str(key), float(item)) for key, item in value.items()))
    return default
