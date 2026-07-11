from __future__ import annotations

from hunter.opportunity.models import DivergenceState
from hunter.persistence.records import FusedIntelligenceRecord


def assess_divergence(records: tuple[FusedIntelligenceRecord, ...]) -> DivergenceState:
    strengths: dict[str, list[float]] = {}
    for record in records:
        for signal in record.unified_signals:
            category = str(signal.get("category", "")).strip()
            if category:
                strengths.setdefault(category, []).append(float(signal.get("strength", 0.0) or 0.0))
    avg = {key: sum(values) / len(values) for key, values in strengths.items() if values}
    divergences: list[str] = []
    if avg.get("social", 0.0) >= 0.75 and max(avg.get("protocol", 0.0), avg.get("on-chain", 0.0), avg.get("developer", 0.0)) < 0.45:
        divergences.append("social_excitement_without_fundamental_confirmation")
    if min(avg.get("protocol", 1.0), avg.get("developer", 1.0)) >= 0.65 and avg.get("social", 0.0) < 0.4:
        divergences.append("fundamentals_without_market_attention")
    if avg.get("whale", 0.0) >= 0.7 and avg.get("on-chain", 0.0) < 0.45:
        divergences.append("whale_accumulation_without_broad_adoption")
    if avg.get("narrative", 0.0) >= 0.7 and avg.get("developer", 0.0) < 0.45:
        divergences.append("narrative_acceleration_without_developer_activity")
    if avg.get("on-chain", 0.0) >= 0.65 and avg.get("macro", 1.0) < 0.4:
        divergences.append("adoption_growth_under_macro_headwinds")
    if avg.get("social", 0.0) >= 0.85 and min(avg.get("protocol", 1.0), avg.get("developer", 1.0)) >= 0.55:
        divergences.append("social_saturation_while_fundamentals_remain_early")
    severity = min(1.0, len(divergences) / 4)
    return DivergenceState(tuple(divergences), severity, f"{len(divergences)} deterministic divergence patterns detected.")
