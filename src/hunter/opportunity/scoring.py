from __future__ import annotations

from hunter.opportunity.configuration import OpportunityTimingConfig
from hunter.opportunity.models import (
    AccelerationState,
    ConfirmationState,
    DivergenceState,
    RiskState,
    TemporalComparison,
)
from hunter.persistence.records import FusedIntelligenceRecord


def timing_score(
    records: tuple[FusedIntelligenceRecord, ...],
    temporal: TemporalComparison,
    confirmation: ConfirmationState,
    acceleration: AccelerationState,
    divergence: DivergenceState,
    risk: RiskState,
    config: OpportunityTimingConfig,
) -> float:
    fusion = sum(float(record.confidence.get("score", 0.0) or 0.0) for record in records) / max(1, len(records))
    score = fusion * 55
    score += confirmation.score * config.confirmation_bonus
    if acceleration.state == "positive_acceleration":
        score += config.acceleration_bonus
    elif acceleration.state in {"negative_acceleration", "reversal"}:
        score -= config.acceleration_bonus
    score += temporal.persistence * config.persistence_bonus
    score -= divergence.severity * config.divergence_penalty
    score -= risk.score * 20
    score -= (
        max((float(record.contradictions.get("severity", 0.0) or 0.0) for record in records), default=0.0)
        * config.contradiction_penalty
    )
    missing_count = max(
        (len(record.missing_evidence.get("missing_categories", ()) or ()) for record in records), default=0
    )
    score -= missing_count * config.missing_evidence_penalty
    if temporal.historical_depth < config.required_historical_depth:
        score -= 8
    return round(max(0.0, min(100.0, score)), 4)
