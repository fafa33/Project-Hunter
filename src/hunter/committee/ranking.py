from __future__ import annotations

from typing import Literal

from hunter.committee.models import InvestmentCommitteeAssessment

CommitteeSortMode = Literal["committee", "committee-confidence", "consensus", "evidence-robustness", "thesis-fragility"]


def rank_investment_committee(
    assessments: tuple[InvestmentCommitteeAssessment, ...], *, sort: CommitteeSortMode = "committee"
) -> tuple[InvestmentCommitteeAssessment, ...]:
    if sort in {"committee", "committee-confidence"}:
        return tuple(
            sorted(assessments, key=lambda item: (-item.committee_confidence, -item.consensus_score, item.project_id))
        )
    if sort == "consensus":
        return tuple(
            sorted(assessments, key=lambda item: (-item.consensus_score, item.conflict_score, item.project_id))
        )
    if sort == "evidence-robustness":
        return tuple(
            sorted(
                assessments, key=lambda item: (-item.evidence_robustness, -item.committee_confidence, item.project_id)
            )
        )
    if sort == "thesis-fragility":
        return tuple(
            sorted(assessments, key=lambda item: (item.thesis_fragility, -item.committee_confidence, item.project_id))
        )
    msg = f"Unsupported committee ranking mode: {sort}"
    raise ValueError(msg)
