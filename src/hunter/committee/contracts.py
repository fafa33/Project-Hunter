from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.committee.models import CommitteeInputSet, InvestmentCommitteeAssessment


@runtime_checkable
class CommitteeInputRepository(Protocol):
    def latest_for_project(self, project_id: str) -> CommitteeInputSet | None:
        raise NotImplementedError

    def all_latest(self) -> tuple[CommitteeInputSet, ...]:
        raise NotImplementedError


@runtime_checkable
class InvestmentCommitteeAssessmentRepository(Protocol):
    def save(self, assessment: InvestmentCommitteeAssessment) -> InvestmentCommitteeAssessment:
        raise NotImplementedError

    def latest_for_project(self, project_id: str) -> InvestmentCommitteeAssessment | None:
        raise NotImplementedError

    def history_for_project(self, project_id: str) -> tuple[InvestmentCommitteeAssessment, ...]:
        raise NotImplementedError
