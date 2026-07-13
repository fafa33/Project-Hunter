from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreContribution:
    engine: str
    raw_score: float
    normalized_score: float
    base_weight: float
    adjusted_weight: float
    weighted_contribution: float
    confidence: float
    freshness: float
    evidence_coverage: float
    scoring_version: str


@dataclass(frozen=True)
class WeightedScore:
    hunter_score: float
    final_score: float
    scoring_version: str
    contributions: tuple[ScoreContribution, ...]


@dataclass(frozen=True)
class WeightRecommendation:
    status: str
    scoring_version: str
    sample_size: int
    minimum_sample_size: int
    recommended_adjustments: dict[str, float]
