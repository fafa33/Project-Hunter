from __future__ import annotations

from typing import Literal

from hunter.necessity.models import TechnologyNecessityAssessment

NecessitySortMode = Literal["necessity", "gap", "rotation", "dependency"]


def rank_necessity_assessments(
    assessments: tuple[TechnologyNecessityAssessment, ...], *, sort: NecessitySortMode = "necessity"
) -> tuple[TechnologyNecessityAssessment, ...]:
    if sort == "necessity":
        return tuple(sorted(assessments, key=lambda item: (-item.overall_necessity, item.technology_id)))
    if sort == "gap":
        return tuple(
            sorted(assessments, key=lambda item: (-item.necessity_gap, -item.overall_necessity, item.technology_id))
        )
    if sort == "rotation":
        return tuple(
            sorted(
                assessments,
                key=lambda item: (-item.capital_rotation_score, -item.overall_necessity, item.technology_id),
            )
        )
    if sort == "dependency":
        return tuple(
            sorted(
                assessments, key=lambda item: (-item.dependency_strength, -item.overall_necessity, item.technology_id)
            )
        )
    msg = f"Unsupported necessity ranking mode: {sort}"
    raise ValueError(msg)
