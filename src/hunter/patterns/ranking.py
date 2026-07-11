from __future__ import annotations

from typing import Literal

from hunter.patterns.models import PatternMatchingAssessment

PatternSortMode = Literal["similarity", "historical", "pattern"]


def rank_pattern_assessments(
    assessments: tuple[PatternMatchingAssessment, ...], *, sort: PatternSortMode = "similarity"
) -> tuple[PatternMatchingAssessment, ...]:
    if sort == "similarity":
        return tuple(
            sorted(
                assessments, key=lambda item: (-item.overall_similarity, -item.historical_confidence, item.target_id)
            )
        )
    if sort == "historical":
        return tuple(
            sorted(
                assessments, key=lambda item: (-item.historical_confidence, -item.overall_similarity, item.target_id)
            )
        )
    if sort == "pattern":
        return tuple(
            sorted(
                assessments, key=lambda item: (-len(item.positive_matches), len(item.negative_matches), item.target_id)
            )
        )
    msg = f"Unsupported pattern ranking mode: {sort}"
    raise ValueError(msg)
