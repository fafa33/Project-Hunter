from __future__ import annotations

from hunter.opportunity.models import ConfirmationState, DivergenceState, RiskState, TemporalComparison
from hunter.persistence.records import FusedIntelligenceRecord


def assess_risk(
    records: tuple[FusedIntelligenceRecord, ...],
    confirmation: ConfirmationState,
    divergence: DivergenceState,
    temporal: TemporalComparison,
) -> RiskState:
    risks: list[str] = []
    contradiction = max((float(record.contradictions.get("severity", 0.0) or 0.0) for record in records), default=0.0)
    dependency = max((float(record.dependencies.get("penalty", 0.0) or 0.0) for record in records), default=0.0)
    missing = max((float(record.missing_evidence.get("severity", 0.0) or 0.0) for record in records), default=0.0)
    categories = {str(signal.get("category", "")) for record in records for signal in record.unified_signals}
    if "social" in categories and divergence.severity > 0:
        risks.append("narrative_saturation")
        risks.append("social_manipulation")
    if "whale" in categories and not confirmation.confirmed:
        risks.append("concentration")
    if "developer" in categories and temporal.deterioration:
        risks.append("declining_developer_activity")
    if "protocol" in categories and contradiction > 0.35:
        risks.append("protocol_weakness")
    if "macro" in categories and contradiction > 0.25:
        risks.append("macro_headwinds")
    if dependency > 0.3:
        risks.append("evidence_dependency")
    if missing > 0.3:
        risks.append("insufficient_historical_confirmation")
    if temporal.deterioration:
        risks.append("negative_capital_flows")
    if not records:
        risks.append("insufficient_historical_confirmation")
    score = min(
        1.0,
        contradiction * 0.25
        + dependency * 0.2
        + missing * 0.2
        + divergence.severity * 0.2
        + (0.15 if temporal.historical_depth < 3 else 0.0),
    )
    return RiskState(tuple(risks), score, f"{len(risks)} timing-specific risks identified.")
