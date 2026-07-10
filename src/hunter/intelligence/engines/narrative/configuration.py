from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class NarrativeEngineConfiguration:
    enabled: bool = True
    project: str = "global-crypto"
    engine_id: str = "narrative-intelligence"
    priority: int = 60
    freshness_days: int = 30
    minimum_evidence_quality: float = 0.40
    duplicate_similarity_threshold: float = 0.90
    emerging_threshold: float = 0.25
    expansion_threshold: float = 0.45
    acceleration_threshold: float = 0.65
    saturation_threshold: float = 0.75
    confidence_weights: dict[str, float] = field(default_factory=dict)


class NarrativeEngineConfigurationLoader:
    def load(self, path: Path | None = None) -> NarrativeEngineConfiguration:
        config_path = path or Path("configs/narrative_engine.yaml")
        if not config_path.exists():
            return NarrativeEngineConfiguration()
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return NarrativeEngineConfiguration(
            enabled=bool(raw.get("enabled", True)),
            project=str(raw.get("project", "global-crypto")),
            engine_id=str(raw.get("engine_id", "narrative-intelligence")),
            priority=int(raw.get("priority", 60)),
            freshness_days=int(raw.get("freshness_days", 30)),
            minimum_evidence_quality=float(raw.get("minimum_evidence_quality", 0.40)),
            duplicate_similarity_threshold=float(raw.get("duplicate_similarity_threshold", 0.90)),
            emerging_threshold=float(raw.get("emerging_threshold", 0.25)),
            expansion_threshold=float(raw.get("expansion_threshold", 0.45)),
            acceleration_threshold=float(raw.get("acceleration_threshold", 0.65)),
            saturation_threshold=float(raw.get("saturation_threshold", 0.75)),
            confidence_weights=_float_mapping(raw.get("confidence_weights", {})),
        )


def _float_mapping(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(key): float(item) for key, item in value.items()}
