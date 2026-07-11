from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.patterns.models import PatternInputSet, PatternMatchingAssessment


@runtime_checkable
class PatternInputRepository(Protocol):
    def latest_for_target(self, target_id: str) -> PatternInputSet | None:
        raise NotImplementedError


@runtime_checkable
class PatternAssessmentRepository(Protocol):
    def save(self, assessment: PatternMatchingAssessment) -> PatternMatchingAssessment:
        raise NotImplementedError

    def latest_for_target(self, target_id: str) -> PatternMatchingAssessment | None:
        raise NotImplementedError
