from __future__ import annotations

from hunter.opportunity.configuration import OpportunityTimingConfig
from hunter.opportunity.models import AccelerationState
from hunter.persistence.records import FusedIntelligenceRecord


def assess_acceleration(
    records: tuple[FusedIntelligenceRecord, ...], config: OpportunityTimingConfig | None = None
) -> AccelerationState:
    rules = dict((config or OpportunityTimingConfig()).acceleration_rules)
    ordered = tuple(sorted(records, key=lambda item: (item.effective_at, item.id)))
    if len(ordered) < 3:
        return AccelerationState("insufficient_history", 0.0, "Fewer than three fused records are available.")
    scores = tuple(_score(item) for item in ordered)
    first_delta = scores[-2] - scores[-3]
    second_delta = scores[-1] - scores[-2]
    value = round(second_delta - first_delta, 4)
    if second_delta < rules.get("reversal_delta", -0.08) and first_delta > 0:
        state = "reversal"
    elif value > rules.get("positive_acceleration", 0.08):
        state = "positive_acceleration"
    elif value < rules.get("negative_acceleration", -0.08):
        state = "negative_acceleration"
    elif abs(second_delta) <= rules.get("stalled_delta", 0.02):
        state = "stalled_trend"
    else:
        state = "stable_trend"
    return AccelerationState(state, value, f"Latest score delta={second_delta:.2f}; acceleration={value:.2f}.")


def _score(record: FusedIntelligenceRecord) -> float:
    try:
        return max(0.0, min(1.0, float(record.confidence.get("score", 0.0))))
    except (TypeError, ValueError):
        return 0.0
