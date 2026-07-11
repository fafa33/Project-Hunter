from __future__ import annotations

from hunter.probability.contracts import ProbabilityAssessmentRepository, ProbabilityInputRepository
from hunter.probability.models import ProbabilityAssessment, ProbabilityInputSet


class InMemoryProbabilityInputRepository(ProbabilityInputRepository):
    def __init__(self, inputs: tuple[ProbabilityInputSet, ...] = ()) -> None:
        self._inputs = tuple(inputs)

    def latest_for_target(self, target_id: str) -> ProbabilityInputSet | None:
        scoped = [item for item in self._inputs if item.target_id == target_id]
        scoped.sort(key=lambda item: item.effective_at, reverse=True)
        return scoped[0] if scoped else None

    def history_for_target(self, target_id: str, *, limit: int | None = None) -> tuple[ProbabilityInputSet, ...]:
        scoped = [item for item in self._inputs if item.target_id == target_id]
        scoped.sort(key=lambda item: item.effective_at, reverse=True)
        return tuple(scoped[:limit])


class InMemoryProbabilityAssessmentRepository(ProbabilityAssessmentRepository):
    def __init__(self) -> None:
        self._assessments: dict[str, ProbabilityAssessment] = {}

    def save(self, assessment: ProbabilityAssessment) -> ProbabilityAssessment:
        existing = self._assessments.get(assessment.assessment_id)
        if existing is not None:
            return existing
        self._assessments[assessment.assessment_id] = assessment
        return assessment

    def latest_for_target(self, target_id: str) -> ProbabilityAssessment | None:
        scoped = [item for item in self._assessments.values() if item.target_id == target_id]
        scoped.sort(key=lambda item: item.effective_at, reverse=True)
        return scoped[0] if scoped else None
