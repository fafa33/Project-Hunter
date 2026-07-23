from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from hunter.automation.configuration import automation_config_from_mapping
from hunter.automation.jobs import pipeline_plan_for_job
from hunter.committee.backtesting import summarize_committee_backtest
from hunter.committee.configuration import InvestmentCommitteeConfig, load_investment_committee_config
from hunter.committee.engine import InvestmentCommitteeEngine
from hunter.committee.ranking import rank_investment_committee
from hunter.committee.renderer import InvestmentCommitteeReportRenderer
from hunter.committee.repositories import assessment_to_record, champion_to_record, vote_to_record
from hunter.dashboard.configuration import DashboardConfig
from hunter.dashboard.data import DashboardDataProvider
from hunter.persistence.records import (
    EvidenceRecord,
    IntelligenceRecord,
    SnapshotRecord,
)
from hunter.persistence.serialization import record_from_json, record_to_json
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def committee_inputs(project_id: str = "alpha", *, stale: bool = False):
    generated_at = NOW - timedelta(days=60 if stale else 1)
    intelligence = tuple(
        intelligence_record(f"intelligence:identity-v1:{name}", name, generated_at=generated_at)
        for name in ("whale", "macro", "future", "validation")
    )
    snapshots = (
        snapshot_record("snapshot:identity-v1:valuation", {"valuation": 0.85, "risk": 0.18}),
        snapshot_record("snapshot:identity-v1:mispricing", {"mispricing_quality": 0.82}),
        snapshot_record("snapshot:identity-v1:asymmetry", {"asymmetry": 0.79}),
        snapshot_record("snapshot:identity-v1:backtesting", {"backtesting_reliability": 0.73}),
    )
    evidence = tuple(
        evidence_record(f"evidence:identity-v1:{index}", reliability=0.85, freshness=0.9) for index in range(3)
    )
    from hunter.committee.models import CommitteeInputSet

    return CommitteeInputSet(
        project_id=project_id,
        effective_at=NOW,
        intelligence=intelligence,
        snapshots=snapshots,
        evidence=evidence,
    )


def intelligence_record(record_id: str, engine_id: str, *, generated_at: datetime = NOW) -> IntelligenceRecord:
    return IntelligenceRecord(
        id=record_id,
        created_at=generated_at,
        effective_at=generated_at,
        pipeline_run_id="pipeline-run:identity-v1:committee",
        project="alpha",
        engine_id=engine_id,
        generated_at=generated_at,
        signal_ids=("signal:identity-v1:x",),
        evidence_ids=("evidence:identity-v1:x",),
        observation_ids=("observation:identity-v1:x",),
        insight_ids=("insight:identity-v1:x",),
        confidence={"overall": 0.84},
        engine_version="1.0.0",
        plugin_id=f"plugin-{engine_id}",
        plugin_version="1.0.0",
        signal_strengths=(0.84,),
        signal_confidences=(0.84,),
    )


def evidence_record(record_id: str, *, reliability: float, freshness: float) -> EvidenceRecord:
    return EvidenceRecord(
        id=record_id,
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="pipeline-run:identity-v1:committee",
        source="fixture",
        reference=record_id,
        collected_at=NOW,
        reliability=reliability,
        freshness=freshness,
        raw_data={"id": record_id},
    )


def snapshot_record(record_id: str, payload: dict[str, float]) -> SnapshotRecord:
    return SnapshotRecord(
        id=record_id,
        created_at=NOW,
        effective_at=NOW,
        snapshot_type="committee-input",
        target_id="alpha",
        record_ids=("evidence:identity-v1:x",),
        payload=payload,
    )


def test_committee_evaluation_is_deterministic_and_explainable() -> None:
    engine = InvestmentCommitteeEngine()

    first = engine.evaluate(committee_inputs())
    second = engine.evaluate(committee_inputs())

    assert first == second
    assert first.eligibility_state == "ELIGIBLE"
    assert first.decision in {"QUALIFIED_CANDIDATE", "STRONG_CANDIDATE", "WATCH_CLOSELY"}
    assert first.committee_confidence > 0
    assert first.invalidation_conditions
    assert all(vote.assessment_id == first.id for vote in first.votes)


def test_committee_abstains_for_missing_stale_and_low_confidence_inputs() -> None:
    stale = InvestmentCommitteeEngine().evaluate(committee_inputs(stale=True))
    weak = InvestmentCommitteeEngine().evaluate(
        committee_inputs(project_id="weak").__class__(
            project_id="weak",
            effective_at=NOW,
            intelligence=(intelligence_record("intelligence:identity-v1:weak", "whale"),),
            evidence=(evidence_record("evidence:identity-v1:weak", reliability=0.2, freshness=0.2),),
            snapshots=(snapshot_record("snapshot:identity-v1:weak", {"valuation": 0.1}),),
        )
    )

    assert "ABSTAIN_STALE" in {vote.vote for vote in stale.votes}
    assert weak.eligibility_state in {"INSUFFICIENT_EVIDENCE", "CONDITIONALLY_ELIGIBLE"}
    assert "ABSTAIN_MISSING" in {vote.vote for vote in weak.votes}


def test_champion_selection_allows_no_qualified_candidate_and_tie_rejection() -> None:
    engine = InvestmentCommitteeEngine(InvestmentCommitteeConfig())
    snapshot, assessments = engine.select_champion((committee_inputs("alpha"), committee_inputs("beta")))

    assert assessments
    assert snapshot.selected_project_id is None
    assert snapshot.decision == "NO_QUALIFIED_CANDIDATE"
    assert snapshot.no_selection_reason is not None


def test_persistence_records_round_trip_and_idempotent_sql_save() -> None:
    assessment = InvestmentCommitteeEngine().evaluate(committee_inputs())
    assessment_record = assessment_to_record(assessment)
    vote_record = vote_to_record(
        assessment.votes[0], project_id=assessment.project_id, effective_at=assessment.created_at
    )
    snapshot, _ = InvestmentCommitteeEngine().select_champion((committee_inputs(),))
    champion_record = champion_to_record(snapshot)

    assert record_from_json(record_to_json(assessment_record)) == assessment_record
    assert record_from_json(record_to_json(vote_record)) == vote_record
    assert record_from_json(record_to_json(champion_record)) == champion_record

    engine = create_sqlite_engine()
    create_schema(engine)
    with SessionFactory(engine).create() as session:
        factory = RepositoryFactory(session)
        assert factory.investment_committee_assessments().save(assessment_record) == assessment_record
        assert factory.investment_committee_assessments().save(assessment_record) == assessment_record
        assert factory.committee_votes().save(vote_record) == vote_record
        assert factory.cycle_champion_snapshots().save(champion_record) == champion_record


def test_ranking_report_dashboard_automation_and_backtest_integration() -> None:
    engine = InvestmentCommitteeEngine()
    alpha = engine.evaluate(committee_inputs("alpha"))
    beta = engine.evaluate(committee_inputs("beta", stale=True))
    ranked = rank_investment_committee((beta, alpha), sort="committee")
    report = InvestmentCommitteeReportRenderer().render_project_markdown(alpha)
    summary = summarize_committee_backtest((alpha, beta), successful_project_ids=())
    job = automation_config_from_mapping(
        {
            "jobs": [
                {
                    "job_id": "committee",
                    "schedule": {"type": "daily"},
                    "target": {"type": "project", "id": "alpha"},
                    "pipeline_options": {"run_investment_committee": True},
                }
            ]
        }
    ).jobs[0]
    plan = pipeline_plan_for_job(job, scheduled_for=NOW)

    assert ranked[0].id == alpha.id
    assert "Committee Decision" in report
    assert summary.candidate_count == 2
    assert plan.run_investment_committee is True


def test_dashboard_reads_persisted_committee_outputs_only() -> None:
    class Repo:
        def __init__(self, records: tuple[object, ...] = ()) -> None:
            self.records = records

        def query(self, spec: object) -> tuple[object, ...]:
            return self.records

    class Provider:
        def __init__(self, record: object) -> None:
            self.repo = Repo((record,))
            self.empty = Repo(())

        def pipeline_runs(self) -> Repo:
            return self.empty

        def operational_attempts(self) -> Repo:
            return self.empty

        def automation_jobs(self) -> Repo:
            return self.empty

        def automation_runs(self) -> Repo:
            return self.empty

        def investment_committee_assessments(self) -> Repo:
            return self.repo

        def cycle_champion_snapshots(self) -> Repo:
            return self.empty

        def fused_intelligence(self) -> Repo:
            return self.empty

        def opportunity_timing_assessments(self) -> Repo:
            return self.empty

        def snapshots(self) -> Repo:
            return self.empty

    record = assessment_to_record(InvestmentCommitteeEngine().evaluate(committee_inputs()))
    view = DashboardDataProvider(
        Provider(record),
        DashboardConfig(
            include_pipeline=False, include_automation=False, include_fusion=False, include_opportunity_timing=False
        ),
    ).build(generated_at=NOW)

    assert view.panels[0].panel_id == "investment-committee"
    assert view.panels[0].rows[0].values["decision"] == record.decision


def test_configuration_and_cli_contracts() -> None:
    config = load_investment_committee_config("configs/investment_committee.yaml")

    assert config.engine_weights
    assert Path("docs/INVESTMENT_COMMITTEE_ENGINE.md").exists()


def test_architecture_boundaries() -> None:
    src_files = tuple(Path("src").rglob("*.py"))
    committee_source = "\n".join(path.read_text() for path in Path("src/hunter/committee").rglob("*.py"))
    scheduler_source = Path("src/hunter/automation/scheduler.py").read_text()
    dashboard_source = Path("src/hunter/dashboard").read_text() if Path("src/hunter/dashboard").is_file() else ""
    dashboard_source += "\n".join(path.read_text() for path in Path("src/hunter/dashboard").rglob("*.py"))

    violations = [
        str(path)
        for path in src_files
        if "import sqlalchemy" in path.read_text() and "src/hunter/persistence/" not in str(path)
    ]
    assert violations == []
    assert "requests." not in committee_source
    assert "httpx." not in committee_source
    assert "expected return" not in committee_source.lower()
    assert "price target" not in committee_source.lower()
    assert "committee" not in scheduler_source.lower()
    assert "InvestmentCommitteeEngine" not in dashboard_source
