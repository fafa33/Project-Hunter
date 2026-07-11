from __future__ import annotations

from hunter.opportunity.configuration import OpportunityTimingConfig
from hunter.opportunity.models import (
    AccelerationState,
    ConfirmationState,
    OpportunityPhase,
    RiskState,
    TemporalComparison,
)


def classify_phase(
    score: float,
    confirmation: ConfirmationState,
    acceleration: AccelerationState,
    risk: RiskState,
    temporal: TemporalComparison,
    config: OpportunityTimingConfig | None = None,
) -> OpportunityPhase:
    active_config = config or OpportunityTimingConfig()
    if temporal.historical_depth == 0:
        return "insufficient_evidence"
    if risk.score >= 0.8:
        return "invalidated"
    if temporal.deterioration and score < 45:
        return "exit_risk"
    if temporal.deterioration:
        return "deteriorating"
    configured = _threshold_label(score, active_config.phase_thresholds)
    if configured == "confirmed_entry" and not confirmation.confirmed:
        return "early_entry"
    if configured == "expansion":
        return "expansion" if acceleration.state == "positive_acceleration" else "mature"
    if configured in {"too_early", "forming", "early_entry", "confirmed_entry", "expansion", "mature", "crowded"}:
        return configured  # type: ignore[return-value]
    return "crowded" if risk.score > 0.35 else "expansion"


def _threshold_label(score: float, thresholds: tuple[tuple[str, float], ...]) -> str:
    for label, upper_bound in thresholds:
        if score < upper_bound:
            return label
    return thresholds[-1][0]
