from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.opportunity.metrics import OpportunityMetricSnapshot


@runtime_checkable
class OpportunityMetricRepository(Protocol):
    def latest_for_project(self, project_id: str) -> OpportunityMetricSnapshot | None:
        raise NotImplementedError

    def history_for_project(self, project_id: str, *, limit: int | None = None) -> tuple[OpportunityMetricSnapshot, ...]:
        raise NotImplementedError
