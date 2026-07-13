from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml


@dataclass(frozen=True)
class WeightConfig:
    version: str
    weights: dict[str, float]
    active: bool = True
    minimum_historical_sample_size: int = 30
    calibration_policy: str = "recommend_only"

    def __post_init__(self) -> None:
        version = self.version.strip()
        if not version:
            msg = "weight version is required"
            raise ValueError(msg)
        weights = {str(engine): round(float(weight), 6) for engine, weight in self.weights.items()}
        if not weights:
            msg = "weight configuration must include weights"
            raise ValueError(msg)
        if any(weight < 0.0 for weight in weights.values()):
            msg = "weights must be non-negative"
            raise ValueError(msg)
        total = round(sum(weights.values()), 6)
        if total != 1.0:
            msg = f"weights must sum to 1.0, got {total:.6f}"
            raise ValueError(msg)
        if self.minimum_historical_sample_size < 1:
            msg = "minimum_historical_sample_size must be positive"
            raise ValueError(msg)
        if self.calibration_policy != "recommend_only":
            msg = "calibration_policy must be recommend_only"
            raise ValueError(msg)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "weights", MappingProxyType(dict(sorted(weights.items()))))


def load_weight_config(path: str | Path = "configs/weights.yaml") -> WeightConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        msg = "weight configuration must be a mapping"
        raise ValueError(msg)
    return weight_config_from_mapping(payload)


def weight_config_from_mapping(payload: dict[str, Any]) -> WeightConfig:
    weights = payload.get("weights", {})
    if not isinstance(weights, dict):
        msg = "weights must be a mapping"
        raise ValueError(msg)
    return WeightConfig(
        version=str(payload.get("version", "hunter-score-v3.0.0-baseline")),
        active=bool(payload.get("active", True)),
        minimum_historical_sample_size=int(payload.get("minimum_historical_sample_size", 30)),
        calibration_policy=str(payload.get("calibration_policy", "recommend_only")),
        weights={str(engine): float(weight) for engine, weight in weights.items()},
    )
