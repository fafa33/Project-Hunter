from __future__ import annotations

from hunter.opportunity.models import (
    AccelerationState,
    ConfirmationState,
    OpportunityPhase,
    RiskState,
    TemporalComparison,
)


def classify_phase(score: float, confirmation: ConfirmationState, acceleration: AccelerationState, risk: RiskState, temporal: TemporalComparison) -> OpportunityPhase:
    if temporal.historical_depth == 0:
        return "insufficient_evidence"
    if risk.score >= 0.8:
        return "invalidated"
    if temporal.deterioration and score < 45:
        return "exit_risk"
    if temporal.deterioration:
        return "deteriorating"
    if score < 20:
        return "too_early"
    if score < 40:
        return "forming"
    if score < 60:
        return "early_entry"
    if score < 75:
        return "confirmed_entry" if confirmation.confirmed else "early_entry"
    if score < 90:
        return "expansion" if acceleration.state == "positive_acceleration" else "mature"
    return "crowded" if risk.score > 0.35 else "expansion"
