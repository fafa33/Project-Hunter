from __future__ import annotations

from hunter.necessity.contracts import TechnologyNecessityAssessmentRepository, TechnologyNecessityInputRepository
from hunter.necessity.models import TechnologyNecessityAssessment, TechnologyNecessityInputSet


class InMemoryTechnologyNecessityInputRepository(TechnologyNecessityInputRepository):
    def __init__(self, inputs: tuple[TechnologyNecessityInputSet, ...] = ()) -> None:
        self._inputs = tuple(inputs)

    def latest_for_technology(self, technology_id: str) -> TechnologyNecessityInputSet | None:
        scoped = [item for item in self._inputs if item.technology_id == technology_id]
        scoped.sort(key=lambda item: item.effective_at, reverse=True)
        return scoped[0] if scoped else None


class InMemoryTechnologyNecessityAssessmentRepository(TechnologyNecessityAssessmentRepository):
    def __init__(self) -> None:
        self._assessments: dict[str, TechnologyNecessityAssessment] = {}

    def save(self, assessment: TechnologyNecessityAssessment) -> TechnologyNecessityAssessment:
        existing = self._assessments.get(assessment.assessment_id)
        if existing is not None:
            return existing
        self._assessments[assessment.assessment_id] = assessment
        return assessment

    def latest_for_technology(self, technology_id: str) -> TechnologyNecessityAssessment | None:
        scoped = [item for item in self._assessments.values() if item.technology_id == technology_id]
        scoped.sort(key=lambda item: item.effective_at, reverse=True)
        return scoped[0] if scoped else None
