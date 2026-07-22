from __future__ import annotations

from typing import Any

from hunter.committee.repository import InvestmentCommitteeRepository


class CommitteeDashboardProjection:
    """Read-only dashboard projection; never recomputes analytical scores."""

    def __init__(self, repository: InvestmentCommitteeRepository) -> None:
        self.repository = repository

    def snapshot(self, *, limit: int = 10) -> dict[str, Any]:
        cycle = self.repository.latest_cycle()
        opportunities = self.repository.top_opportunities(limit=limit)
        champion = None
        runner_up = None
        if cycle is not None:
            selected = cycle.get("selected_project_id")
            runner = cycle.get("runner_up_project_id")
            champion = next((item for item in opportunities if item.get("project_id") == selected), None)
            runner_up = next((item for item in opportunities if item.get("project_id") == runner), None)
        return {
            "schema": "committee-dashboard.v1",
            "cycle": cycle,
            "champion": champion,
            "runner_up": runner_up,
            "top_opportunities": opportunities,
            "count": len(opportunities),
        }
