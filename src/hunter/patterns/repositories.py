from __future__ import annotations

from hunter.patterns.contracts import PatternAssessmentRepository, PatternInputRepository
from hunter.patterns.models import PatternInputSet, PatternMatchingAssessment


class InMemoryPatternInputRepository(PatternInputRepository):
    def __init__(self, inputs: tuple[PatternInputSet, ...] = ()) -> None:
        self._inputs = tuple(inputs)

    def latest_for_target(self, target_id: str) -> PatternInputSet | None:
        scoped = [item for item in self._inputs if item.target_id == target_id]
        scoped.sort(key=lambda item: item.effective_at, reverse=True)
        return scoped[0] if scoped else None


class InMemoryPatternAssessmentRepository(PatternAssessmentRepository):
    def __init__(self) -> None:
        self._assessments: dict[str, PatternMatchingAssessment] = {}

    def save(self, assessment: PatternMatchingAssessment) -> PatternMatchingAssessment:
        existing = self._assessments.get(assessment.assessment_id)
        if existing is not None:
            return existing
        self._assessments[assessment.assessment_id] = assessment
        return assessment

    def latest_for_target(self, target_id: str) -> PatternMatchingAssessment | None:
        scoped = [item for item in self._assessments.values() if item.target_id == target_id]
        scoped.sort(key=lambda item: item.effective_at, reverse=True)
        return scoped[0] if scoped else None
