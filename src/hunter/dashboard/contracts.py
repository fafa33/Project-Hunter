from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.dashboard.models import DashboardView
from hunter.persistence.repositories import (
    AutomationJobRepository,
    AutomationRunRepository,
    FusedIntelligenceRepository,
    OperationalAttemptRepository,
    OpportunityTimingAssessmentRepository,
    PipelineRunRepository,
    SnapshotRepository,
)


@runtime_checkable
class DashboardRepositoryProvider(Protocol):
    def pipeline_runs(self) -> PipelineRunRepository:
        raise NotImplementedError

    def operational_attempts(self) -> OperationalAttemptRepository:
        raise NotImplementedError

    def automation_jobs(self) -> AutomationJobRepository:
        raise NotImplementedError

    def automation_runs(self) -> AutomationRunRepository:
        raise NotImplementedError

    def fused_intelligence(self) -> FusedIntelligenceRepository:
        raise NotImplementedError

    def opportunity_timing_assessments(self) -> OpportunityTimingAssessmentRepository:
        raise NotImplementedError

    def snapshots(self) -> SnapshotRepository:
        raise NotImplementedError


@runtime_checkable
class DashboardRenderer(Protocol):
    def render(self, view: DashboardView) -> str:
        raise NotImplementedError
