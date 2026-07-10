from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Confidence:
    score: float
    completeness: float
    evidence_quality: float
    freshness: float
    uncertainty: float

    @classmethod
    def calculate(
        cls,
        *,
        completeness: float,
        evidence_quality: float,
        freshness: float,
        uncertainty: float,
    ) -> Confidence:
        normalized_completeness = clamp(completeness)
        normalized_quality = clamp(evidence_quality)
        normalized_freshness = clamp(freshness)
        normalized_uncertainty = clamp(uncertainty)
        score = (
            (0.35 * normalized_completeness)
            + (0.30 * normalized_quality)
            + (0.20 * normalized_freshness)
            + (0.15 * (1.0 - normalized_uncertainty))
        )
        return cls(
            score=round(clamp(score), 4),
            completeness=round(normalized_completeness, 4),
            evidence_quality=round(normalized_quality, 4),
            freshness=round(normalized_freshness, 4),
            uncertainty=round(normalized_uncertainty, 4),
        )


def clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)

