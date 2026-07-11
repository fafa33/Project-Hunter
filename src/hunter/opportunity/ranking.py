from __future__ import annotations

from typing import Literal

from hunter.opportunity.models import OpportunityAssessment

OpportunitySortMode = Literal["opportunity", "conviction"]


def rank_opportunities(
    assessments: tuple[OpportunityAssessment, ...],
    *,
    sort: OpportunitySortMode = "opportunity",
) -> tuple[OpportunityAssessment, ...]:
    if sort == "opportunity":
        return tuple(
            sorted(assessments, key=lambda item: (-item.opportunity_score, -item.conviction_score, item.project_id))
        )
    if sort == "conviction":
        return tuple(
            sorted(assessments, key=lambda item: (-item.conviction_score, -item.opportunity_score, item.project_id))
        )
    msg = f"Unsupported opportunity ranking mode: {sort}"
    raise ValueError(msg)
