from __future__ import annotations

from hunter.opportunity.configuration import OpportunityTimingConfig
from hunter.opportunity.models import OpportunityPhase, OpportunityWindow, RiskState, TemporalComparison


def classify_window(
    score: float,
    phase: OpportunityPhase,
    risk: RiskState,
    temporal: TemporalComparison,
    config: OpportunityTimingConfig | None = None,
) -> OpportunityWindow:
    active_config = config or OpportunityTimingConfig()
    if phase in {"invalidated", "insufficient_evidence"} or risk.score >= 0.8:
        return "invalid"
    if phase in {"exit_risk"}:
        return "closing"
    if phase in {"deteriorating"}:
        return "weakening"
    if temporal.change < -0.05:
        return "weakening"
    configured = _threshold_label(score, active_config.window_thresholds)
    if configured in {"closed", "watch", "opening", "open", "strengthening", "weakening", "closing", "invalid"}:
        return configured  # type: ignore[return-value]
    return "open"


def _threshold_label(score: float, thresholds: tuple[tuple[str, float], ...]) -> str:
    for label, upper_bound in thresholds:
        if score < upper_bound:
            return label
    return thresholds[-1][0]
