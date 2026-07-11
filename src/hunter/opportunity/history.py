from __future__ import annotations

from datetime import datetime

from hunter.opportunity.models import HistoricalComparison
from hunter.persistence.records import OpportunityTimingAssessmentRecord, OpportunityTimingSnapshotRecord


def compare_history(
    history: tuple[OpportunityTimingAssessmentRecord | OpportunityTimingSnapshotRecord, ...],
    *,
    as_of: datetime,
) -> tuple[HistoricalComparison, ...]:
    scoped = tuple(item for item in history if item.effective_at <= as_of)
    if not scoped:
        return (
            HistoricalComparison(
                prior_phases=(),
                phase_transitions=(),
                window_duration=0,
                false_starts=0,
                prior_confirmations=0,
                deterioration_after_confirmation=False,
                similarity_summary="No prior timing history for target.",
            ),
        )
    ordered = tuple(sorted(scoped, key=lambda item: item.effective_at))
    phases = tuple(getattr(item, "opportunity_phase", "") for item in ordered if getattr(item, "opportunity_phase", ""))
    transitions = tuple(f"{left}->{right}" for left, right in zip(phases, phases[1:], strict=False) if left != right)
    false_starts = sum(1 for item in transitions if item in {"forming->too_early", "early_entry->too_early", "confirmed_entry->deteriorating"})
    prior_confirmations = sum(1 for phase in phases if phase in {"confirmed_entry", "expansion"})
    deteriorated = any(item in {"confirmed_entry->deteriorating", "expansion->exit_risk", "confirmed_entry->exit_risk"} for item in transitions)
    open_duration = sum(1 for item in ordered if getattr(item, "opportunity_window", "") in {"opening", "open", "strengthening"})
    return (
        HistoricalComparison(
            prior_phases=phases,
            phase_transitions=transitions,
            window_duration=open_duration,
            false_starts=false_starts,
            prior_confirmations=prior_confirmations,
            deterioration_after_confirmation=deteriorated,
            similarity_summary=f"{len(ordered)} prior timing records with {len(transitions)} phase transitions.",
        ),
    )
