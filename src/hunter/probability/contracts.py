from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.probability.models import ProbabilityAssessment, ProbabilityInputSet


@runtime_checkable
class ProbabilityInputRepository(Protocol):
    def latest_for_target(self, target_id: str) -> ProbabilityInputSet | None:
        raise NotImplementedError

    def history_for_target(self, target_id: str, *, limit: int | None = None) -> tuple[ProbabilityInputSet, ...]:
        raise NotImplementedError


@runtime_checkable
class ProbabilityAssessmentRepository(Protocol):
    def save(self, assessment: ProbabilityAssessment) -> ProbabilityAssessment:
        raise NotImplementedError

    def latest_for_target(self, target_id: str) -> ProbabilityAssessment | None:
        raise NotImplementedError
