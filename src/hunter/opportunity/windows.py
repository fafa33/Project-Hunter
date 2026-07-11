from __future__ import annotations

from hunter.opportunity.models import OpportunityPhase, OpportunityWindow, RiskState, TemporalComparison


def classify_window(score: float, phase: OpportunityPhase, risk: RiskState, temporal: TemporalComparison) -> OpportunityWindow:
    if phase in {"invalidated", "insufficient_evidence"} or risk.score >= 0.8:
        return "invalid"
    if phase in {"exit_risk"}:
        return "closing"
    if phase in {"deteriorating"}:
        return "weakening"
    if score < 20:
        return "closed"
    if score < 40:
        return "watch"
    if score < 60:
        return "opening"
    if score < 75:
        return "open"
    if temporal.change < -0.05:
        return "weakening"
    return "strengthening" if temporal.change > 0.05 else "open"
