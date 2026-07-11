from __future__ import annotations

import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path

import yaml
from test_developer_intelligence_engine import developer_snapshot
from test_macro_intelligence_engine import macro_points
from test_narrative_intelligence_engine import narrative_records
from test_news_intelligence_engine import news_records
from test_onchain_intelligence_engine import onchain_records
from test_protocol_intelligence_engine import protocol_records
from test_social_intelligence_engine import social_records
from test_whale_intelligence_engine import whale_events

from hunter.cli import main
from hunter.committee.engine import InvestmentCommitteeEngine
from hunter.committee.ranking import rank_investment_committee
from hunter.committee.renderer import InvestmentCommitteeReportRenderer
from hunter.committee.repositories import assessment_to_record, champion_to_record, vote_to_record
from hunter.dashboard.configuration import DashboardConfig
from hunter.dashboard.data import DashboardDataProvider
from hunter.execution import FixedClock
from hunter.intelligence.engines.developer import DeveloperIntelligenceEngine
from hunter.intelligence.engines.macro import MacroIntelligenceEngine
from hunter.intelligence.engines.narrative import NarrativeIntelligenceEngine
from hunter.intelligence.engines.news import NewsIntelligenceEngine
from hunter.intelligence.engines.onchain import OnchainIntelligenceEngine
from hunter.intelligence.engines.protocol import ProtocolIntelligenceEngine
from hunter.intelligence.engines.social import SocialIntelligenceEngine
from hunter.intelligence.engines.whale import WhaleIntelligenceEngine
from hunter.intelligence.fusion.engine import CrossEngineFusionEngine, fused_intelligence_to_record
from hunter.intelligence.fusion.models import FusionTarget
from hunter.necessity.engine import TechnologyNecessityEngine
from hunter.necessity.models import TechnologyNecessityInputSet
from hunter.opportunity.engine import OpportunityTimingEngine, opportunity_assessment_to_record
from hunter.patterns.configuration import HistoricalPatternLibrary
from hunter.patterns.engine import PatternMatchingEngine
from hunter.patterns.models import HistoricalProjectPattern, PatternInputSet
from hunter.persistence.integration.adapter import PipelinePersistenceAdapter
from hunter.persistence.integration.policies import PersistencePolicy, PipelinePersistenceSettings
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.serialization import record_from_json, record_to_json
from hunter.persistence.sql import RepositoryFactory, SessionFactory, UnitOfWork, create_schema, create_sqlite_engine
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext
from hunter.probability.engine import ProbabilityEngine
from hunter.probability.models import ProbabilityInputSet

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
SUPPORTED_PROJECTS = (
    "bitcoin",
    "ethereum",
    "chainlink",
    "api3",
    "render",
    "bittensor",
    "sui",
    "arbitrum",
    "filecoin",
    "aave",
)


def test_v1_end_to_end_runtime_validation(tmp_path: Path) -> None:
    values = _runtime_values()
    first = _execute_platform(tmp_path / "first.sqlite", values)
    second = _execute_platform(tmp_path / "second.sqlite", values)

    assert first["engine_ids"] == second["engine_ids"]
    assert first["intelligence_ids"] == second["intelligence_ids"]
    assert first["fusion_id"] == second["fusion_id"]
    assert first["opportunity_id"] == second["opportunity_id"]
    assert first["probability_id"] == second["probability_id"]
    assert first["pattern_id"] == second["pattern_id"]
    assert first["necessity_id"] == second["necessity_id"]
    assert first["committee_id"] == second["committee_id"]
    assert first["ranking"] == second["ranking"]
    assert first["dashboard_panels"] == second["dashboard_panels"]
    assert first["report_sections"]
    assert first["runtime_seconds"] < 10.0
    assert first["peak_memory_bytes"] > 0
    assert first["database_record_count"] >= 8
    assert tuple(project for project in SUPPORTED_PROJECTS if project) == SUPPORTED_PROJECTS


def test_v1_cli_configuration_and_boundary_validation(tmp_path: Path) -> None:
    for command in (
        ("analyze",),
        ("discover",),
        ("validate",),
        ("whales",),
        ("rank",),
        ("committee", "ranking"),
        ("dashboard", "build", "--sqlite-path", ":memory:", "--output", str(tmp_path / "dashboard.html")),
        ("reports",),
        ("automation", "status"),
        ("backtesting",),
        ("alerts",),
    ):
        assert main(list(command)) == 0

    for config_path in Path("configs").glob("*.yaml"):
        assert yaml.safe_load(config_path.read_text(encoding="utf-8")) is not None

    source_files = tuple(Path("src/hunter").rglob("*.py"))
    sqlalchemy_violations = [
        str(path)
        for path in source_files
        if "sqlalchemy" in path.read_text(encoding="utf-8") and "src/hunter/persistence/" not in str(path)
    ]
    assert sqlalchemy_violations == []
    assert "InvestmentCommitteeEngine" not in Path("src/hunter/dashboard/data.py").read_text(encoding="utf-8")
    assert "committee" not in Path("src/hunter/automation/scheduler.py").read_text(encoding="utf-8").lower()
    assert "score" not in Path("src/hunter/automation/scheduler.py").read_text(encoding="utf-8").lower()
    all_source = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
    assert "import requests" not in all_source
    assert "import httpx" not in all_source


def _execute_platform(sqlite_path: Path, values: dict[str, object]) -> dict[str, object]:
    tracemalloc.start()
    started = time.perf_counter()
    engine = create_sqlite_engine(sqlite_path)
    create_schema(engine)
    session_factory = SessionFactory(engine)
    context = PipelineContext(clock=FixedClock(NOW), values=values)
    adapter = PipelinePersistenceAdapter(
        lambda: UnitOfWork(session_factory),
        PipelinePersistenceSettings(enabled=True, policy=PersistencePolicy.ATOMIC),
    )
    engines = _engines()

    PipelineOrchestrator().run(context=context, intelligence_engines=engines, persistence_adapter=adapter)

    assert context.run is not None
    target = FusionTarget(target_type="project", target_id="global-crypto")
    with session_factory.create() as session:
        repositories = RepositoryFactory(session)
        intelligence = repositories.intelligence().query(QuerySpec(record_kind="intelligence"))
        evidence = repositories.evidence().query(QuerySpec(record_kind="evidence"))
        snapshots = (
            repositories.snapshots().snapshot(
                _snapshot_spec("pipeline-run", "global-crypto", context.run.effective_at)
            ),
            _metric_snapshot("validation", {"validation_health": 0.82}),
            _metric_snapshot(
                "valuation", {"valuation": 0.74, "mispricing_quality": 0.68, "asymmetry": 0.71, "risk": 0.22}
            ),
            _metric_snapshot("backtesting", {"backtesting_reliability": 0.77}),
        )
        fused = CrossEngineFusionEngine().fuse(intelligence, target)
        fused_record = fused_intelligence_to_record(fused, pipeline_run_id=context.run.run_id)
        repositories.fused_intelligence().save(fused_record)
        opportunity = OpportunityTimingEngine().assess((fused_record,), target, as_of=NOW)
        opportunity_record = opportunity_assessment_to_record(
            opportunity,
            pipeline_run_id=context.run.run_id,
            created_at=NOW,
        )
        repositories.opportunity_timing_assessments().save(opportunity_record)
        probability = ProbabilityEngine().assess(
            ProbabilityInputSet(
                target_id="global-crypto",
                effective_at=NOW,
                fused_intelligence=(fused_record,),
                opportunity_timing=(opportunity_record,),
                intelligence=intelligence,
                evidence=evidence,
                snapshots=snapshots,
            )
        )
        pattern = PatternMatchingEngine(library=_pattern_library()).assess(
            PatternInputSet(
                target_id="global-crypto",
                effective_at=NOW,
                intelligence=intelligence,
                fused_intelligence=(fused_record,),
                opportunity_timing=(opportunity_record,),
                evidence=evidence,
                snapshots=snapshots,
            )
        )
        necessity = TechnologyNecessityEngine().assess(
            TechnologyNecessityInputSet(
                technology_id="global-crypto",
                effective_at=NOW,
                intelligence=intelligence,
                fused_intelligence=(fused_record,),
                opportunity_timing=(opportunity_record,),
                evidence=evidence,
                snapshots=snapshots,
            )
        )
        committee_inputs = _committee_inputs(
            intelligence=intelligence,
            fused_intelligence=(fused_record,),
            opportunity=opportunity,
            probability=probability,
            pattern=pattern,
            necessity=necessity,
            evidence=evidence,
            snapshots=snapshots,
        )
        committee = InvestmentCommitteeEngine().evaluate(committee_inputs)
        committee_record = assessment_to_record(committee)
        repositories.investment_committee_assessments().save(committee_record)
        for vote in committee.votes:
            repositories.committee_votes().save(vote_to_record(vote, project_id=committee.project_id, effective_at=NOW))
        champion, ranking = InvestmentCommitteeEngine().select_champion((committee_inputs,))
        repositories.cycle_champion_snapshots().save(champion_to_record(champion))
        session.commit()

        assert record_from_json(record_to_json(committee_record)) == committee_record
        report_sections = InvestmentCommitteeReportRenderer().render_project_sections(committee)
        champion_sections = InvestmentCommitteeReportRenderer().render_champion_sections(champion, ranking)
        ranked = rank_investment_committee((committee,), sort="committee")
        dashboard = DashboardDataProvider(repositories, DashboardConfig(max_rows=20)).build(generated_at=NOW)
        record_count = len(repositories.intelligence().query(QuerySpec())) + len(
            repositories.evidence().query(QuerySpec())
        )

    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "engine_ids": tuple(engine.id for engine in engines),
        "intelligence_ids": tuple(record.id for record in intelligence),
        "fusion_id": fused_record.id,
        "opportunity_id": opportunity.assessment_id,
        "probability_id": probability.assessment_id,
        "pattern_id": pattern.assessment_id,
        "necessity_id": necessity.assessment_id,
        "committee_id": committee.id,
        "ranking": tuple(item.id for item in ranked),
        "dashboard_panels": tuple(panel.panel_id for panel in dashboard.panels),
        "report_sections": tuple(title for title, body in (*report_sections, *champion_sections) if body.strip()),
        "runtime_seconds": round(time.perf_counter() - started, 4),
        "peak_memory_bytes": peak,
        "database_record_count": record_count,
    }


def _runtime_values() -> dict[str, object]:
    return {
        "macro_data": macro_points(),
        "whale_events": whale_events(),
        "developer_records": (developer_snapshot(),),
        "protocol_records": protocol_records(),
        "news_records": news_records(),
        "narrative_records": narrative_records(),
        "social_records": social_records(),
        "onchain_records": onchain_records(),
    }


def _engines():
    return (
        MacroIntelligenceEngine(),
        WhaleIntelligenceEngine(),
        DeveloperIntelligenceEngine(),
        ProtocolIntelligenceEngine(),
        NewsIntelligenceEngine(),
        NarrativeIntelligenceEngine(),
        SocialIntelligenceEngine(),
        OnchainIntelligenceEngine(),
    )


def _pattern_library() -> HistoricalPatternLibrary:
    return HistoricalPatternLibrary(
        projects=(
            HistoricalProjectPattern(
                project_id="historical-compound",
                name="Historical Compound",
                outcome="successful",
                dimensions={"macro_alignment": 0.7, "developer_activity": 0.7, "whale_behaviour": 0.7},
                context_dimensions={"current_macro_conditions": 0.7, "current_capital_rotation": 0.7},
            ),
        )
    )


def _committee_inputs(**kwargs):
    from hunter.committee.models import CommitteeInputSet

    return CommitteeInputSet(project_id="global-crypto", effective_at=NOW, **kwargs)


def _metric_snapshot(snapshot_id: str, payload: dict[str, float]) -> SnapshotRecord:
    return SnapshotRecord(
        id=f"snapshot:identity-v1:{snapshot_id}",
        created_at=NOW,
        effective_at=NOW,
        snapshot_type="runtime-validation",
        target_id="global-crypto",
        record_ids=("evidence:identity-v1:runtime",),
        payload=payload,
    )


def _snapshot_spec(snapshot_type: str, target_id: str, effective_at: datetime):
    from hunter.persistence.models import SnapshotSpec

    return SnapshotSpec(target_id=target_id, snapshot_type=snapshot_type, effective_at=effective_at)
