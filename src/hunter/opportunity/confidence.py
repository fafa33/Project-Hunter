from __future__ import annotations

from hunter.opportunity.configuration import OpportunityTimingConfig
from hunter.opportunity.models import ConfirmationState, RiskState, TemporalComparison
from hunter.persistence.records import FusedIntelligenceRecord


def calculate_confidence(records: tuple[FusedIntelligenceRecord, ...], temporal: TemporalComparison, confirmation: ConfirmationState, risk: RiskState, config: OpportunityTimingConfig) -> dict[str, float]:
    history = min(1.0, temporal.historical_depth / max(1, config.required_historical_depth))
    categories = {str(signal.get("category", "")) for record in records for signal in record.unified_signals if signal.get("category")}
    engines = {str(item.get("engine_id", "")) for record in records for item in record.contributions if item.get("engine_id")}
    canonical_groups = {str(item.get("canonical_key", "")) for record in records for item in record.canonical_evidence_groups if item.get("canonical_key")}
    diversity = min(1.0, len(engines) / 4)
    coverage = min(1.0, len(categories) / max(1, len(config.required_categories)))
    independence = min(1.0, len(canonical_groups) / max(1, config.min_confirmation_groups))
    fusion = sum(float(record.confidence.get("score", 0.0) or 0.0) for record in records) / max(1, len(records))
    freshness = 1.0
    weights = dict(config.confidence_weights)
    score = (
        history * weights.get("history", 0.2)
        + diversity * weights.get("diversity", 0.2)
        + coverage * weights.get("coverage", 0.2)
        + independence * weights.get("independence", 0.15)
        + freshness * weights.get("freshness", 0.1)
        + fusion * weights.get("fusion", 0.15)
    )
    score = max(0.0, min(1.0, score + confirmation.score * 0.05 - risk.score * 0.2))
    return {
        "score": round(score, 4),
        "historical_depth": round(history, 4),
        "source_diversity": round(diversity, 4),
        "category_coverage": round(coverage, 4),
        "canonical_evidence_independence": round(independence, 4),
        "data_freshness": round(freshness, 4),
        "fusion_confidence": round(fusion, 4),
        "snapshot_completeness": round(history, 4),
    }
