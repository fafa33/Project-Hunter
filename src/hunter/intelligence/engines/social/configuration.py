from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SocialEngineConfiguration:
    enabled: bool = True
    project: str = "global-crypto"
    engine_id: str = "social-intelligence"
    priority: int = 70
    freshness_days: int = 30
    minimum_historical_depth_days: int = 60
    author_quality_threshold: float = 0.45
    influence_threshold: float = 0.60
    engagement_quality_threshold: float = 0.50
    community_growth_threshold: float = 0.40
    sentiment_momentum_threshold: float = 0.20
    saturation_threshold: float = 0.75
    bot_risk_threshold: float = 0.50
    spam_risk_threshold: float = 0.50
    coordination_risk_threshold: float = 0.50
    promotion_risk_threshold: float = 0.50
    include_reposts: bool = False
    duplicate_detection: bool = True
    languages: tuple[str, ...] = ()
    required_platforms: tuple[str, ...] = ()
    platform_priorities: dict[str, int] = field(default_factory=dict)
    confidence_weights: dict[str, float] = field(default_factory=dict)


class SocialEngineConfigurationLoader:
    def load(self, path: Path | None = None) -> SocialEngineConfiguration:
        config_path = path or Path("configs/social_engine.yaml")
        if not config_path.exists():
            return SocialEngineConfiguration()
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return SocialEngineConfiguration(
            enabled=bool(raw.get("enabled", True)),
            project=str(raw.get("project", "global-crypto")),
            engine_id=str(raw.get("engine_id", "social-intelligence")),
            priority=int(raw.get("priority", 70)),
            freshness_days=int(raw.get("freshness_days", 30)),
            minimum_historical_depth_days=int(raw.get("minimum_historical_depth_days", 60)),
            author_quality_threshold=float(raw.get("author_quality_threshold", 0.45)),
            influence_threshold=float(raw.get("influence_threshold", 0.60)),
            engagement_quality_threshold=float(raw.get("engagement_quality_threshold", 0.50)),
            community_growth_threshold=float(raw.get("community_growth_threshold", 0.40)),
            sentiment_momentum_threshold=float(raw.get("sentiment_momentum_threshold", 0.20)),
            saturation_threshold=float(raw.get("saturation_threshold", 0.75)),
            bot_risk_threshold=float(raw.get("bot_risk_threshold", 0.50)),
            spam_risk_threshold=float(raw.get("spam_risk_threshold", 0.50)),
            coordination_risk_threshold=float(raw.get("coordination_risk_threshold", 0.50)),
            promotion_risk_threshold=float(raw.get("promotion_risk_threshold", 0.50)),
            include_reposts=bool(raw.get("include_reposts", False)),
            duplicate_detection=bool(raw.get("duplicate_detection", True)),
            languages=tuple(str(item).lower() for item in raw.get("languages", ())),
            required_platforms=tuple(str(item).lower() for item in raw.get("required_platforms", ())),
            platform_priorities={str(key): int(value) for key, value in raw.get("platform_priorities", {}).items()},
            confidence_weights=_float_mapping(raw.get("confidence_weights", {})),
        )


def _float_mapping(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(key): float(item) for key, item in value.items()}
