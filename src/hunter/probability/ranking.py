from __future__ import annotations

from typing import Literal

from hunter.probability.models import ProbabilityAssessment

ProbabilitySortMode = Literal["probability", "robustness", "consensus"]


def rank_probability_assessments(
    assessments: tuple[ProbabilityAssessment, ...], *, sort: ProbabilitySortMode = "probability"
) -> tuple[ProbabilityAssessment, ...]:
    if sort == "probability":
        return tuple(
            sorted(assessments, key=lambda item: (-item.probability_score, -item.decision_confidence, item.target_id))
        )
    if sort == "robustness":
        return tuple(
            sorted(assessments, key=lambda item: (-item.evidence_robustness, -item.probability_score, item.target_id))
        )
    if sort == "consensus":
        return tuple(sorted(assessments, key=lambda item: (-item.consensus_score, item.conflict_score, item.target_id)))
    msg = f"Unsupported probability ranking mode: {sort}"
    raise ValueError(msg)
