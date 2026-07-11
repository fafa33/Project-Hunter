from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_COMPONENT_WEIGHTS = {
    "fundamental_strength": 0.08,
    "valuation_quality": 0.08,
    "relative_valuation": 0.06,
    "mispricing_quality": 0.08,
    "macro_alignment": 0.08,
    "future_demand_alignment": 0.08,
    "opportunity_timing": 0.12,
    "whale_alignment": 0.06,
    "developer_strength": 0.08,
    "validation_health": 0.08,
    "backtesting_reliability": 0.08,
    "historical_consistency": 0.06,
    "evidence_quality": 0.06,
    "evidence_freshness": 0.04,
    "confidence": 0.04,
    "risk_balance": 0.04,
}

DEFAULT_LABEL_THRESHOLDS = {
    "Insufficient Evidence": 0.0,
    "Very Low Probability": 0.15,
    "Low Probability": 0.25,
    "Speculative": 0.35,
    "Neutral": 0.45,
    "Moderately Positive": 0.55,
    "High Probability": 0.68,
    "Very High Probability": 0.8,
    "Exceptional Probability": 0.9,
}


@dataclass(frozen=True)
class ProbabilityConfig:
    enabled: bool = True
    component_weights: tuple[tuple[str, float], ...] = field(
        default_factory=lambda: tuple(sorted(DEFAULT_COMPONENT_WEIGHTS.items()))
    )
    label_thresholds: tuple[tuple[str, float], ...] = field(
        default_factory=lambda: tuple(sorted(DEFAULT_LABEL_THRESHOLDS.items(), key=lambda item: item[1]))
    )
    minimum_evidence_records: int = 1
    missing_evidence_penalty: float = 0.2
    conflict_penalty: float = 0.2
    weak_backtesting_threshold: float = 0.5
    engine_component_map: tuple[tuple[str, str], ...] = (
        ("macro", "macro_alignment"),
        ("future", "future_demand_alignment"),
        ("demand", "future_demand_alignment"),
        ("whale", "whale_alignment"),
        ("developer", "developer_strength"),
        ("validation", "validation_health"),
        ("discovery", "fundamental_strength"),
        ("valuation", "valuation_quality"),
        ("comparative", "relative_valuation"),
        ("mispricing", "mispricing_quality"),
        ("asymmetry", "risk_balance"),
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "component_weights", _weights(self.component_weights))
        object.__setattr__(self, "label_thresholds", _thresholds(self.label_thresholds))
        object.__setattr__(
            self,
            "engine_component_map",
            tuple(sorted((str(key), str(value)) for key, value in self.engine_component_map)),
        )
        if self.minimum_evidence_records < 0:
            msg = "minimum_evidence_records must be non-negative"
            raise ValueError(msg)
        for name in ("missing_evidence_penalty", "conflict_penalty", "weak_backtesting_threshold"):
            value = getattr(self, name)
            if value < 0.0 or value > 1.0:
                msg = f"{name} must be between 0.0 and 1.0"
                raise ValueError(msg)


def load_probability_config(path: str | Path = "configs/probability.yaml") -> ProbabilityConfig:
    config_path = Path(path)
    if not config_path.exists():
        return ProbabilityConfig()
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return ProbabilityConfig(
        enabled=bool(payload.get("enabled", True)),
        component_weights=tuple((str(key), float(value)) for key, value in payload.get("component_weights", {}).items())
        or tuple(sorted(DEFAULT_COMPONENT_WEIGHTS.items())),
        label_thresholds=tuple((str(key), float(value)) for key, value in payload.get("label_thresholds", {}).items())
        or tuple(sorted(DEFAULT_LABEL_THRESHOLDS.items(), key=lambda item: item[1])),
        minimum_evidence_records=int(payload.get("minimum_evidence_records", 1)),
        missing_evidence_penalty=float(payload.get("missing_evidence_penalty", 0.2)),
        conflict_penalty=float(payload.get("conflict_penalty", 0.2)),
        weak_backtesting_threshold=float(payload.get("weak_backtesting_threshold", 0.5)),
        engine_component_map=_mapping_items(payload.get("engine_component_map", {}))
        or ProbabilityConfig().engine_component_map,
    )


def _weights(values: tuple[tuple[str, float], ...]) -> tuple[tuple[str, float], ...]:
    normalized = tuple(sorted((str(name), max(0.0, float(weight))) for name, weight in values))
    total = sum(weight for _, weight in normalized)
    if total <= 0.0:
        msg = "component weights must include positive weight"
        raise ValueError(msg)
    return tuple((name, round(weight / total, 6)) for name, weight in normalized)


def _thresholds(values: tuple[tuple[str, float], ...]) -> tuple[tuple[str, float], ...]:
    normalized = tuple(sorted(((str(label), float(score)) for label, score in values), key=lambda item: item[1]))
    scores = [score for _, score in normalized]
    if any(score < 0.0 or score > 1.0 for score in scores) or scores != sorted(scores):
        msg = "label thresholds must be ordered values between 0.0 and 1.0"
        raise ValueError(msg)
    return normalized


def _mapping_items(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        return ()
    return tuple((str(key), str(item)) for key, item in value.items())
