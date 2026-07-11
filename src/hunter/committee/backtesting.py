from __future__ import annotations

from collections import Counter

from hunter.committee.models import CommitteeBacktestSummary, InvestmentCommitteeAssessment


def summarize_committee_backtest(
    assessments: tuple[InvestmentCommitteeAssessment, ...],
    *,
    successful_project_ids: tuple[str, ...] = (),
) -> CommitteeBacktestSummary:
    qualified = tuple(
        item
        for item in assessments
        if item.decision in {"HIGHEST_CONVICTION_CANDIDATE", "STRONG_CANDIDATE", "QUALIFIED_CANDIDATE"}
    )
    successes = set(successful_project_ids)
    champion_count = sum(1 for item in assessments if item.decision == "HIGHEST_CONVICTION_CANDIDATE")
    hits = sum(
        1 for item in assessments if item.decision == "HIGHEST_CONVICTION_CANDIDATE" and item.project_id in successes
    )
    false_positive = sum(1 for item in qualified if successes and item.project_id not in successes)
    false_negative = sum(1 for item in assessments if item.project_id in successes and item not in qualified)
    return CommitteeBacktestSummary(
        candidate_count=len(assessments),
        qualified_candidate_count=len(qualified),
        no_selection_count=sum(1 for item in assessments if item.decision == "NO_QUALIFIED_CANDIDATE"),
        decision_distribution=dict(Counter(item.decision for item in assessments)),
        champion_hit_rate=round(hits / champion_count, 4) if champion_count else 0.0,
        false_positive_count=false_positive,
        false_negative_count=false_negative,
        notes=("insufficient evaluation count" if len(assessments) < 30 else "deterministic historical summary",),
    )
