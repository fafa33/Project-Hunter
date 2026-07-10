from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class FusionWeightingConfig:
    default_engine_weight: float = 1.0
    engine_weights: tuple[tuple[str, float], ...] | Mapping[str, float] = field(default_factory=tuple)
    dependency_penalty: float = 0.2
    contradiction_penalty: float = 0.25
    missing_evidence_penalty: float = 0.15
    corroboration_bonus: float = 0.1

    def __post_init__(self) -> None:
        raw = self.engine_weights.items() if isinstance(self.engine_weights, Mapping) else self.engine_weights
        object.__setattr__(self, "engine_weights", tuple(sorted((str(key), float(value)) for key, value in raw)))


@dataclass(frozen=True)
class FusionConfig:
    strategy: str = "weighted-corroboration-v1"
    required_categories: tuple[str, ...] = ()
    weighting: FusionWeightingConfig = field(default_factory=FusionWeightingConfig)


def load_fusion_config(path: Path) -> FusionConfig:
    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        msg = "Fusion configuration must be a mapping"
        raise ValueError(msg)
    return fusion_config_from_mapping(payload)


def fusion_config_from_mapping(payload: dict[str, Any]) -> FusionConfig:
    weighting_payload = payload.get("weighting", {})
    if not isinstance(weighting_payload, dict):
        weighting_payload = {}
    weights = weighting_payload.get("engine_weights", {})
    if not isinstance(weights, dict):
        weights = {}
    return FusionConfig(
        strategy=str(payload.get("strategy", "weighted-corroboration-v1")),
        required_categories=tuple(str(item) for item in payload.get("required_categories", ())),
        weighting=FusionWeightingConfig(
            default_engine_weight=float(weighting_payload.get("default_engine_weight", 1.0)),
            engine_weights=tuple(sorted((str(key), float(value)) for key, value in weights.items())),
            dependency_penalty=float(weighting_payload.get("dependency_penalty", 0.2)),
            contradiction_penalty=float(weighting_payload.get("contradiction_penalty", 0.25)),
            missing_evidence_penalty=float(weighting_payload.get("missing_evidence_penalty", 0.15)),
            corroboration_bonus=float(weighting_payload.get("corroboration_bonus", 0.1)),
        ),
    )
