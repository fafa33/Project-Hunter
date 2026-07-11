from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hunter.patterns.models import HistoricalProjectPattern

DEFAULT_DIMENSION_WEIGHTS = {
    "fundamentals": 0.09,
    "valuation": 0.08,
    "revenue": 0.06,
    "developer_activity": 0.09,
    "tokenomics": 0.06,
    "whale_behaviour": 0.07,
    "macro_alignment": 0.07,
    "future_demand": 0.08,
    "opportunity_timing": 0.1,
    "probability": 0.1,
    "validation_health": 0.07,
    "backtesting_reliability": 0.06,
    "evidence_quality": 0.04,
    "risk": 0.02,
    "confidence": 0.01,
}

DEFAULT_LABEL_THRESHOLDS = {
    "Insufficient Evidence": 0.0,
    "No Reliable Match": 0.2,
    "Weak Match": 0.35,
    "Moderate Match": 0.5,
    "Strong Match": 0.68,
    "Very Strong Match": 0.82,
    "Exceptional Match": 0.92,
}

DEFAULT_CONTEXT_WEIGHTS = {
    "current_macro_conditions": 0.22,
    "current_technology_trends": 0.18,
    "current_capital_rotation": 0.18,
    "current_institutional_adoption": 0.14,
    "current_regulatory_environment": 0.12,
    "current_future_demand": 0.1,
    "current_sector_strength": 0.06,
}


@dataclass(frozen=True)
class PatternConfig:
    enabled: bool = True
    top_match_limit: int = 5
    minimum_source_records: int = 1
    missing_evidence_penalty: float = 0.2
    historical_weight: float = 0.65
    context_weight: float = 0.35
    dimension_weights: tuple[tuple[str, float], ...] = field(
        default_factory=lambda: tuple(sorted(DEFAULT_DIMENSION_WEIGHTS.items()))
    )
    label_thresholds: tuple[tuple[str, float], ...] = field(
        default_factory=lambda: tuple(sorted(DEFAULT_LABEL_THRESHOLDS.items(), key=lambda item: item[1]))
    )
    context_weights: tuple[tuple[str, float], ...] = field(
        default_factory=lambda: tuple(sorted(DEFAULT_CONTEXT_WEIGHTS.items()))
    )
    engine_dimension_map: tuple[tuple[str, str], ...] = (
        ("macro", "macro_alignment"),
        ("future", "future_demand"),
        ("demand", "future_demand"),
        ("whale", "whale_behaviour"),
        ("developer", "developer_activity"),
        ("validation", "validation_health"),
        ("valuation", "valuation"),
        ("comparative", "valuation"),
        ("mispricing", "valuation"),
        ("asymmetry", "risk"),
        ("probability", "probability"),
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "dimension_weights", _weights(self.dimension_weights))
        object.__setattr__(self, "label_thresholds", _thresholds(self.label_thresholds))
        object.__setattr__(self, "context_weights", _weights(self.context_weights))
        object.__setattr__(
            self,
            "engine_dimension_map",
            tuple(sorted((str(key), str(value)) for key, value in self.engine_dimension_map)),
        )
        if self.top_match_limit < 1:
            msg = "top_match_limit must be positive"
            raise ValueError(msg)
        if self.minimum_source_records < 0:
            msg = "minimum_source_records must be non-negative"
            raise ValueError(msg)
        if self.missing_evidence_penalty < 0.0 or self.missing_evidence_penalty > 1.0:
            msg = "missing_evidence_penalty must be between 0.0 and 1.0"
            raise ValueError(msg)
        if (
            self.historical_weight < 0.0
            or self.context_weight < 0.0
            or self.historical_weight + self.context_weight <= 0.0
        ):
            msg = "historical/context weights must be non-negative and include positive weight"
            raise ValueError(msg)


@dataclass(frozen=True)
class HistoricalPatternLibrary:
    projects: tuple[HistoricalProjectPattern, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "projects", tuple(sorted(self.projects, key=lambda item: item.project_id)))
        if not self.projects:
            msg = "historical pattern library must include projects"
            raise ValueError(msg)


def load_pattern_config(path: str | Path = "configs/patterns.yaml") -> PatternConfig:
    config_path = Path(path)
    if not config_path.exists():
        return PatternConfig()
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return PatternConfig(
        enabled=bool(payload.get("enabled", True)),
        top_match_limit=int(payload.get("top_match_limit", 5)),
        minimum_source_records=int(payload.get("minimum_source_records", 1)),
        missing_evidence_penalty=float(payload.get("missing_evidence_penalty", 0.2)),
        historical_weight=float(payload.get("historical_weight", 0.65)),
        context_weight=float(payload.get("context_weight", 0.35)),
        dimension_weights=_float_items(payload.get("dimension_weights", {}))
        or tuple(sorted(DEFAULT_DIMENSION_WEIGHTS.items())),
        label_thresholds=_float_items(payload.get("label_thresholds", {}))
        or tuple(sorted(DEFAULT_LABEL_THRESHOLDS.items(), key=lambda item: item[1])),
        context_weights=_float_items(payload.get("context_weights", {}))
        or tuple(sorted(DEFAULT_CONTEXT_WEIGHTS.items())),
        engine_dimension_map=_string_items(payload.get("engine_dimension_map", {}))
        or PatternConfig().engine_dimension_map,
    )


def load_historical_library(path: str | Path = "configs/historical_projects.yaml") -> HistoricalPatternLibrary:
    config_path = Path(path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    projects = []
    for item in payload.get("projects", []):
        projects.append(
            HistoricalProjectPattern(
                project_id=str(item["project_id"]),
                name=str(item["name"]),
                outcome=str(item.get("outcome", "successful")),
                dimensions={str(key): float(value) for key, value in item.get("dimensions", {}).items()},
                context_dimensions={
                    str(key): float(value) for key, value in item.get("context_dimensions", {}).items()
                },
                warning_patterns=tuple(str(value) for value in item.get("warning_patterns", ())),
                metadata=item.get("metadata", {}),
            )
        )
    return HistoricalPatternLibrary(tuple(projects))


def _weights(values: tuple[tuple[str, float], ...]) -> tuple[tuple[str, float], ...]:
    normalized = tuple(sorted((str(name), max(0.0, float(weight))) for name, weight in values))
    total = sum(weight for _, weight in normalized)
    if total <= 0.0:
        msg = "dimension weights must include positive weight"
        raise ValueError(msg)
    return tuple((name, round(weight / total, 6)) for name, weight in normalized)


def _thresholds(values: tuple[tuple[str, float], ...]) -> tuple[tuple[str, float], ...]:
    normalized = tuple(sorted(((str(label), float(score)) for label, score in values), key=lambda item: item[1]))
    scores = [score for _, score in normalized]
    if any(score < 0.0 or score > 1.0 for score in scores) or scores != sorted(scores):
        msg = "label thresholds must be ordered values between 0.0 and 1.0"
        raise ValueError(msg)
    return normalized


def _float_items(value: Any) -> tuple[tuple[str, float], ...]:
    if not isinstance(value, dict):
        return ()
    return tuple((str(key), float(item)) for key, item in value.items())


def _string_items(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        return ()
    return tuple((str(key), str(item)) for key, item in value.items())
