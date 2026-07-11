from __future__ import annotations

from datetime import datetime

from hunter.opportunity.configuration import OpportunityTimingConfig
from hunter.opportunity.models import ConfirmationState, RiskState, TemporalComparison
from hunter.persistence.records import FusedIntelligenceRecord


def calculate_confidence(
    records: tuple[FusedIntelligenceRecord, ...],
    temporal: TemporalComparison,
    confirmation: ConfirmationState,
    risk: RiskState,
    config: OpportunityTimingConfig,
    *,
    as_of: datetime,
) -> dict[str, float]:
    history = min(1.0, temporal.historical_depth / max(1, config.required_historical_depth))
    categories = {str(signal.get("category", "")) for record in records for signal in record.unified_signals if signal.get("category")}
    engines = {str(item.get("engine_id", "")) for record in records for item in record.contributions if item.get("engine_id")}
    canonical_groups = {
        str(item.get("canonical_key", ""))
        for record in records
        for item in record.canonical_evidence_groups
        if item.get("canonical_key") and not _is_dependent(item)
    }
    diversity = min(1.0, len(engines) / 4)
    coverage = min(1.0, len(categories) / max(1, len(config.required_categories)))
    independence = min(1.0, len(canonical_groups) / max(1, config.min_confirmation_groups))
    fusion = sum(float(record.confidence.get("score", 0.0) or 0.0) for record in records) / max(1, len(records))
    freshness = _freshness(records, as_of=as_of, config=config)
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


def _freshness(records: tuple[FusedIntelligenceRecord, ...], *, as_of: datetime, config: OpportunityTimingConfig) -> float:
    if not records:
        return 0.0
    values: list[float] = []
    for record in records:
        timestamps = [record.effective_at, *_effective_window(record)]
        newest = max(item for item in timestamps if item <= as_of)
        age_days = max(0.0, (as_of - newest).total_seconds() / 86400)
        if age_days <= config.freshness_grace_days:
            values.append(1.0)
        else:
            decay_days = max(1, config.freshness_window_days - config.freshness_grace_days)
            values.append(max(0.0, 1.0 - ((age_days - config.freshness_grace_days) / decay_days)))
    return round(sum(values) / len(values), 4)


def _effective_window(record: FusedIntelligenceRecord) -> tuple[datetime, ...]:
    parsed: list[datetime] = []
    for item in record.effective_window:
        try:
            value = datetime.fromisoformat(str(item))
        except ValueError:
            continue
        if value.tzinfo is not None:
            parsed.append(value)
    return tuple(parsed)


def _is_dependent(group: dict[str, object]) -> bool:
    return str(group.get("dependency_classification", "")) in {
        "shared-evidence-lineage",
        "shared-evidence-reference",
        "shared-evidence-id",
        "dependent",
    }
