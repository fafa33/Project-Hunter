from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_COMPONENT_WEIGHTS = {
    "technology_necessity": 0.12,
    "infrastructure_criticality": 0.11,
    "replacement_difficulty": 0.08,
    "adoption_momentum": 0.08,
    "developer_adoption": 0.07,
    "enterprise_adoption": 0.06,
    "institutional_adoption": 0.06,
    "government_adoption": 0.04,
    "market_awareness": 0.06,
    "technology_maturity": 0.06,
    "capital_attraction": 0.08,
    "future_relevance": 0.1,
    "dependency_strength": 0.06,
    "evidence_confidence": 0.02,
}

DEFAULT_LABEL_THRESHOLDS = {
    "Insufficient Evidence": 0.0,
    "Low Necessity": 0.15,
    "Legacy": 0.25,
    "Declining": 0.35,
    "Mature": 0.45,
    "Established": 0.55,
    "Growing Necessity": 0.65,
    "Emerging Necessity": 0.75,
    "Critical Infrastructure": 0.86,
}

DEFAULT_ROTATION_WEIGHTS = {
    "capital_entering": 0.24,
    "capital_leaving_inverse": 0.12,
    "institutional_rotation": 0.16,
    "developer_rotation": 0.14,
    "narrative_rotation": 0.12,
    "infrastructure_rotation": 0.14,
    "sector_rotation": 0.08,
}


@dataclass(frozen=True)
class TechnologyNecessityConfig:
    enabled: bool = True
    minimum_source_records: int = 1
    missing_evidence_penalty: float = 0.2
    component_weights: tuple[tuple[str, float], ...] = field(
        default_factory=lambda: tuple(sorted(DEFAULT_COMPONENT_WEIGHTS.items()))
    )
    label_thresholds: tuple[tuple[str, float], ...] = field(
        default_factory=lambda: tuple(sorted(DEFAULT_LABEL_THRESHOLDS.items(), key=lambda item: item[1]))
    )
    engine_component_map: tuple[tuple[str, str], ...] = (
        ("macro", "technology_necessity"),
        ("future", "future_relevance"),
        ("demand", "future_relevance"),
        ("developer", "developer_adoption"),
        ("whale", "capital_attraction"),
        ("validation", "technology_maturity"),
        ("probability", "technology_necessity"),
        ("opportunity", "adoption_momentum"),
        ("pattern", "market_awareness"),
        ("valuation", "market_awareness"),
        ("mispricing", "market_awareness"),
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "component_weights", _weights(self.component_weights))
        object.__setattr__(self, "label_thresholds", _thresholds(self.label_thresholds))
        object.__setattr__(
            self,
            "engine_component_map",
            tuple(sorted((str(key), str(value)) for key, value in self.engine_component_map)),
        )
        if self.minimum_source_records < 0:
            msg = "minimum_source_records must be non-negative"
            raise ValueError(msg)
        if self.missing_evidence_penalty < 0.0 or self.missing_evidence_penalty > 1.0:
            msg = "missing_evidence_penalty must be between 0.0 and 1.0"
            raise ValueError(msg)


@dataclass(frozen=True)
class CapitalRotationConfig:
    weights: tuple[tuple[str, float], ...] = field(
        default_factory=lambda: tuple(sorted(DEFAULT_ROTATION_WEIGHTS.items()))
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "weights", _weights(self.weights))


@dataclass(frozen=True)
class TechnologyGraphConfig:
    categories: tuple[str, ...]
    dependencies: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "categories", tuple(sorted(str(item) for item in self.categories)))
        object.__setattr__(
            self, "dependencies", tuple(sorted((str(left), str(right)) for left, right in self.dependencies))
        )

    def dependency_strength(self, technology_id: str) -> float:
        if not self.dependencies:
            return 0.0
        related = sum(1 for left, right in self.dependencies if technology_id in {left, right})
        return round(min(1.0, related / max(1, len(self.categories))), 4)


def load_technology_necessity_config(
    path: str | Path = "configs/technology_necessity.yaml",
) -> TechnologyNecessityConfig:
    payload = _load(path)
    return TechnologyNecessityConfig(
        enabled=bool(payload.get("enabled", True)),
        minimum_source_records=int(payload.get("minimum_source_records", 1)),
        missing_evidence_penalty=float(payload.get("missing_evidence_penalty", 0.2)),
        component_weights=_float_items(payload.get("component_weights", {}))
        or tuple(sorted(DEFAULT_COMPONENT_WEIGHTS.items())),
        label_thresholds=_float_items(payload.get("label_thresholds", {}))
        or tuple(sorted(DEFAULT_LABEL_THRESHOLDS.items(), key=lambda item: item[1])),
        engine_component_map=_string_items(payload.get("engine_component_map", {}))
        or TechnologyNecessityConfig().engine_component_map,
    )


def load_capital_rotation_config(path: str | Path = "configs/capital_rotation.yaml") -> CapitalRotationConfig:
    payload = _load(path)
    return CapitalRotationConfig(
        weights=_float_items(payload.get("weights", {})) or tuple(sorted(DEFAULT_ROTATION_WEIGHTS.items()))
    )


def load_technology_graph_config(path: str | Path = "configs/technology_graph.yaml") -> TechnologyGraphConfig:
    payload = _load(path)
    return TechnologyGraphConfig(
        categories=tuple(str(item) for item in payload.get("categories", ())),
        dependencies=tuple((str(item["from"]), str(item["to"])) for item in payload.get("dependencies", ())),
    )


def _load(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _weights(values: tuple[tuple[str, float], ...]) -> tuple[tuple[str, float], ...]:
    normalized = tuple(sorted((str(name), max(0.0, float(weight))) for name, weight in values))
    total = sum(weight for _, weight in normalized)
    if total <= 0.0:
        msg = "weights must include positive weight"
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
