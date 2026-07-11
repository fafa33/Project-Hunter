from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from hunter.cli import main
from hunter.dashboard import (
    DashboardConfig,
    DashboardDataProvider,
    HtmlDashboardRenderer,
    dashboard_config_from_mapping,
)
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import (
    AutomationRunRecord,
    FusedIntelligenceRecord,
    OperationalAttemptRecord,
    OpportunityTimingAssessmentRecord,
    PipelineRunRecord,
)
from hunter.persistence.sql import UnitOfWork, create_schema, create_sqlite_engine
from hunter.persistence.sql.session import SessionFactory

NOW = datetime(2026, 1, 5, tzinfo=UTC)


def test_dashboard_config_loading() -> None:
    config = dashboard_config_from_mapping({"enabled": True, "title": "Hunter", "max_rows": 5})

    assert config.enabled is True
    assert config.title == "Hunter"
    assert config.max_rows == 5


def test_dashboard_view_uses_persisted_records_only() -> None:
    view = DashboardDataProvider(_Repositories(), DashboardConfig(max_rows=5)).build(generated_at=NOW)

    assert view.title == "Project Hunter Dashboard"
    assert {panel.panel_id for panel in view.panels} == {
        "pipeline-runs",
        "operational-attempts",
        "automation-runs",
        "fusion",
        "opportunity-timing",
    }
    assert view.panels[0].rows[0].values["status"] == "succeeded"
    assert view.panels[-1].rows[0].values["phase"] == "forming"


def test_html_renderer_escapes_values_and_is_deterministic() -> None:
    view = DashboardDataProvider(_Repositories(), DashboardConfig(title="<Hunter>")).build(generated_at=NOW)
    renderer = HtmlDashboardRenderer()

    first = renderer.render(view)
    second = renderer.render(view)

    assert first == second
    assert "&lt;Hunter&gt;" in first
    assert "Pipeline Runs" in first
    assert "<script" not in first


def test_dashboard_cli_builds_static_html(tmp_path: Path) -> None:
    db_path = tmp_path / "hunter.sqlite"
    output = tmp_path / "dashboard.html"
    config_path = tmp_path / "dashboard.yaml"
    config_path.write_text(
        f"""
enabled: true
title: Test Dashboard
sqlite_path: "{db_path}"
output_path: "{output}"
max_rows: 5
""",
    )
    engine = create_sqlite_engine(db_path)
    create_schema(engine)
    with UnitOfWork(SessionFactory(engine)) as uow:
        assert uow.repositories is not None
        uow.repositories.pipeline_runs().save(_pipeline_record())

    assert main(["dashboard", "--dashboard-config", str(config_path), "build"]) == 0
    html = output.read_text()
    assert "Test Dashboard" in html
    assert "Pipeline Runs" in html


class _Repository:
    def __init__(self, records: tuple[object, ...]) -> None:
        self.records = records

    def query(self, spec: QuerySpec) -> tuple[object, ...]:
        records = sorted(self.records, key=lambda item: getattr(item, spec.sort_by), reverse=spec.direction == "desc")
        return tuple(records[: spec.limit]) if spec.limit is not None else tuple(records)


class _Repositories:
    def pipeline_runs(self) -> _Repository:
        return _Repository((_pipeline_record(),))

    def operational_attempts(self) -> _Repository:
        return _Repository((_attempt_record(),))

    def automation_runs(self) -> _Repository:
        return _Repository((_automation_record(),))

    def fused_intelligence(self) -> _Repository:
        return _Repository((_fusion_record(),))

    def opportunity_timing_assessments(self) -> _Repository:
        return _Repository((_opportunity_record(),))


def _pipeline_record() -> PipelineRunRecord:
    return PipelineRunRecord(
        id="pipeline-run:test",
        created_at=NOW,
        effective_at=NOW,
        run_type="scheduled",
        target_id="project-a",
        target_type="project",
        configuration_fingerprint="cfg",
        input_fingerprint="input",
        engine_manifest_fingerprint="manifest",
        status="succeeded",
    )


def _attempt_record() -> OperationalAttemptRecord:
    return OperationalAttemptRecord(
        id="operational-attempt:test",
        created_at=NOW,
        effective_at=NOW,
        attempt_id="attempt-a",
        run_id="pipeline-run:test",
        attempt_number=1,
        requested_at=NOW,
        status="succeeded",
    )


def _automation_record() -> AutomationRunRecord:
    return AutomationRunRecord(
        id="automation-run:test",
        created_at=NOW,
        effective_at=NOW,
        automation_run_id="automation-a",
        job_id="job-a",
        scheduled_for=NOW,
        status="succeeded",
        pipeline_run_id="pipeline-run:test",
        operational_attempt_id="attempt-a",
    )


def _fusion_record() -> FusedIntelligenceRecord:
    return FusedIntelligenceRecord(
        id="fused:test",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="pipeline-run:test",
        target_id="project-a",
        target_type="project",
        fusion_strategy="weighted",
        source_intelligence_ids=("intelligence:test",),
        source_run_ids=("pipeline-run:test",),
        confidence={"score": 0.7},
    )


def _opportunity_record() -> OpportunityTimingAssessmentRecord:
    return OpportunityTimingAssessmentRecord(
        id="opportunity:test",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="pipeline-run:test",
        target_id="project-a",
        target_type="project",
        source_fused_intelligence_ids=("fused:test",),
        source_run_ids=("pipeline-run:test",),
        configuration_fingerprint="cfg",
        model_fingerprint="model",
        historical_window=("2026-01-01T00:00:00+00:00", "2026-01-05T00:00:00+00:00"),
        opportunity_phase="forming",
        opportunity_window="watch",
        timing_score=52,
        confidence={"score": 0.6},
        evidence_quality=0.7,
        confirmation_state={},
        acceleration_state={},
        divergence_state={},
        risk_state={},
        expected_horizon="weeks",
        supporting_factors=(),
        opposing_factors=(),
        contradictions=(),
        missing_evidence=(),
        invalidation_conditions=(),
        historical_comparisons=(),
    )
