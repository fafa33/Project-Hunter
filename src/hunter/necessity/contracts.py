from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.necessity.models import TechnologyNecessityAssessment, TechnologyNecessityInputSet


@runtime_checkable
class TechnologyNecessityInputRepository(Protocol):
    def latest_for_technology(self, technology_id: str) -> TechnologyNecessityInputSet | None:
        raise NotImplementedError


@runtime_checkable
class TechnologyNecessityAssessmentRepository(Protocol):
    def save(self, assessment: TechnologyNecessityAssessment) -> TechnologyNecessityAssessment:
        raise NotImplementedError

    def latest_for_technology(self, technology_id: str) -> TechnologyNecessityAssessment | None:
        raise NotImplementedError
