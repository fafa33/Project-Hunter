from __future__ import annotations

from datetime import UTC, datetime

from hunter.dashboard.configuration import DashboardConfig
from hunter.dashboard.contracts import DashboardRepositoryProvider
from hunter.dashboard.models import DashboardMetric, DashboardPanel, DashboardRow, DashboardView
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import (
    AutomationRunRecord,
    FusedIntelligenceRecord,
    OperationalAttemptRecord,
    OpportunityTimingAssessmentRecord,
    PipelineRunRecord,
)


class DashboardDataProvider:
    def __init__(self, repositories: DashboardRepositoryProvider, config: DashboardConfig | None = None) -> None:
        self.repositories = repositories
        self.config = config or DashboardConfig()

    def build(self, *, generated_at: datetime | None = None) -> DashboardView:
        now = (generated_at or datetime.now(UTC)).astimezone(UTC)
        panels: list[DashboardPanel] = []
        if self.config.include_pipeline:
            panels.append(_pipeline_panel(self.repositories.pipeline_runs().query(_latest(self.config.max_rows))))
            panels.append(_attempt_panel(self.repositories.operational_attempts().query(_latest(self.config.max_rows))))
        if self.config.include_automation:
            panels.append(_automation_panel(self.repositories.automation_runs().query(_latest(self.config.max_rows))))
        if self.config.include_fusion:
            panels.append(_fusion_panel(self.repositories.fused_intelligence().query(_latest(self.config.max_rows))))
        if self.config.include_opportunity_timing:
            panels.append(
                _opportunity_panel(
                    self.repositories.opportunity_timing_assessments().query(_latest(self.config.max_rows))
                )
            )
        return DashboardView(
            view_id="project-hunter-dashboard",
            title=self.config.title,
            generated_at=now,
            panels=tuple(panels),
            metadata={"source": "persistence", "max_rows": self.config.max_rows},
        )


def _pipeline_panel(records: tuple[PipelineRunRecord, ...]) -> DashboardPanel:
    return DashboardPanel(
        panel_id="pipeline-runs",
        title="Pipeline Runs",
        kind="table",
        metrics=(
            DashboardMetric("total", "Total", len(records)),
            DashboardMetric("latest_status", "Latest Status", records[0].status if records else "none"),
        ),
        rows=tuple(
            DashboardRow(
                row_id=record.id,
                values={
                    "run_id": record.id,
                    "target": f"{record.target_type}:{record.target_id}",
                    "run_type": record.run_type,
                    "status": record.status,
                    "effective_at": record.effective_at.isoformat(),
                },
            )
            for record in records
        ),
    )


def _attempt_panel(records: tuple[OperationalAttemptRecord, ...]) -> DashboardPanel:
    return DashboardPanel(
        panel_id="operational-attempts",
        title="Operational Attempts",
        kind="table",
        metrics=_status_metrics(records),
        rows=tuple(
            DashboardRow(
                row_id=record.id,
                values={
                    "attempt_id": record.attempt_id,
                    "run_id": record.run_id,
                    "attempt_number": record.attempt_number,
                    "status": record.status,
                    "effective_at": record.effective_at.isoformat(),
                },
            )
            for record in records
        ),
    )


def _automation_panel(records: tuple[AutomationRunRecord, ...]) -> DashboardPanel:
    return DashboardPanel(
        panel_id="automation-runs",
        title="Automation Runs",
        kind="table",
        metrics=_status_metrics(records),
        rows=tuple(
            DashboardRow(
                row_id=record.id,
                values={
                    "automation_run_id": record.automation_run_id,
                    "job_id": record.job_id,
                    "status": record.status,
                    "pipeline_run_id": record.pipeline_run_id,
                    "operational_attempt_id": record.operational_attempt_id,
                    "scheduled_for": record.scheduled_for.isoformat(),
                },
            )
            for record in records
        ),
    )


def _fusion_panel(records: tuple[FusedIntelligenceRecord, ...]) -> DashboardPanel:
    return DashboardPanel(
        panel_id="fusion",
        title="Fusion",
        kind="table",
        metrics=(
            DashboardMetric("total", "Total", len(records)),
            DashboardMetric(
                "latest_target",
                "Latest Target",
                f"{records[0].target_type}:{records[0].target_id}" if records else "none",
            ),
        ),
        rows=tuple(
            DashboardRow(
                row_id=record.id,
                values={
                    "fused_id": record.id,
                    "target": f"{record.target_type}:{record.target_id}",
                    "sources": len(record.source_intelligence_ids),
                    "source_runs": len(record.source_run_ids),
                    "effective_at": record.effective_at.isoformat(),
                },
            )
            for record in records
        ),
    )


def _opportunity_panel(records: tuple[OpportunityTimingAssessmentRecord, ...]) -> DashboardPanel:
    return DashboardPanel(
        panel_id="opportunity-timing",
        title="Opportunity Timing",
        kind="table",
        metrics=(
            DashboardMetric("total", "Total", len(records)),
            DashboardMetric("latest_phase", "Latest Phase", records[0].opportunity_phase if records else "none"),
            DashboardMetric("latest_window", "Latest Window", records[0].opportunity_window if records else "none"),
        ),
        rows=tuple(
            DashboardRow(
                row_id=record.id,
                values={
                    "assessment_id": record.id,
                    "target": f"{record.target_type}:{record.target_id}",
                    "phase": record.opportunity_phase,
                    "window": record.opportunity_window,
                    "score": record.timing_score,
                    "effective_at": record.effective_at.isoformat(),
                },
            )
            for record in records
        ),
    )


def _status_metrics(records: tuple[object, ...]) -> tuple[DashboardMetric, ...]:
    statuses = [str(getattr(record, "status", "unknown")) for record in records]
    values = [DashboardMetric("total", "Total", len(statuses))]
    for status in sorted(set(statuses)):
        values.append(DashboardMetric(f"status_{status}", status.replace("_", " ").title(), statuses.count(status)))
    return tuple(values)


def _latest(limit: int) -> QuerySpec:
    return QuerySpec(limit=limit, sort_by="effective_at", direction="desc")
