from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class NewsEngineConfiguration:
    enabled: bool = True
    project: str = "global-crypto"
    engine_id: str = "news-intelligence"
    priority: int = 50
    freshness_days: int = 14
    minimum_source_quality: float = 0.45
    duplicate_title_similarity: float = 0.92
    structural_severity_threshold: float = 0.65
    rumor_confidence_penalty: float = 0.30
    secondary_source_confidence_penalty: float = 0.15
    confidence_weights: dict[str, float] = field(default_factory=dict)
    source_priorities: dict[str, int] = field(default_factory=dict)


class NewsEngineConfigurationLoader:
    def load(self, path: Path | None = None) -> NewsEngineConfiguration:
        config_path = path or Path("configs/news_engine.yaml")
        if not config_path.exists():
            return NewsEngineConfiguration()
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return NewsEngineConfiguration(
            enabled=bool(raw.get("enabled", True)),
            project=str(raw.get("project", "global-crypto")),
            engine_id=str(raw.get("engine_id", "news-intelligence")),
            priority=int(raw.get("priority", 50)),
            freshness_days=int(raw.get("freshness_days", 14)),
            minimum_source_quality=float(raw.get("minimum_source_quality", 0.45)),
            duplicate_title_similarity=float(raw.get("duplicate_title_similarity", 0.92)),
            structural_severity_threshold=float(raw.get("structural_severity_threshold", 0.65)),
            rumor_confidence_penalty=float(raw.get("rumor_confidence_penalty", 0.30)),
            secondary_source_confidence_penalty=float(raw.get("secondary_source_confidence_penalty", 0.15)),
            confidence_weights=_float_mapping(raw.get("confidence_weights", {})),
            source_priorities={str(key): int(value) for key, value in raw.get("source_priorities", {}).items()},
        )


def _float_mapping(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(key): float(item) for key, item in value.items()}
