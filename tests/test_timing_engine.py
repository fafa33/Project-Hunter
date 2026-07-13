from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from hunter.cli import main
from hunter.explainability import DecisionExplainabilityEngine
from hunter.market_validation import MarketValidationRunner, load_market_validation_config
from hunter.market_validation.acquisition_sources import _timing_source
from hunter.market_validation.models import EngineValidationSource
from hunter.market_validation.runner import REQUIRED_ENGINES, SourceBackedV1ProjectExecutor
from hunter.timing import OpportunityTimingEvidenceEngine, TimingRepository
from hunter.timing.engine import REQUIRED_TIMING_ENGINES
from hunter.timing.models import TimingDependencySnapshot
from hunter.weights import WeightEngine

NOW = datetime(2026, 7, 13, tzinfo=UTC)


def test_timing_engine_is_deterministic_from_persisted_engine_evidence(tmp_path: Path) -> None:
    result = _result(_sources())
    engine = OpportunityTimingEvidenceEngine(repository=TimingRepository(tmp_path / "timing"))

    first = engine.assess_project(result, as_of=NOW)
    second = engine.assess_project(result, as_of=NOW)

    assert first == second
    assert first.classification in {"STRONG_ACCUMULATION", "ACCUMULATION", "WAIT", "REDUCE", "STRONG_REDUCE"}
    assert first.entry_score > 0.0
    assert first.evidence_ids
    assert first.repository_ids


def test_missing_evidence_returns_insufficient_evidence_without_fabricated_scores() -> None:
    result = _result(tuple(source for source in _sources() if source.engine != "developer"))

    assessment = OpportunityTimingEvidenceEngine().assess_project(result, as_of=NOW)

    assert assessment.classification == "INSUFFICIENT_EVIDENCE"
    assert assessment.timing_confidence == 0.0
    assert assessment.entry_score == 0.0
    assert "developer" in assessment.missing_evidence


def test_stale_evidence_reduces_confidence() -> None:
    fresh = OpportunityTimingEvidenceEngine().assess_project(_result(_sources(freshness=0.9)), as_of=NOW)
    stale = OpportunityTimingEvidenceEngine().assess_project(_result(_sources(freshness=0.4)), as_of=NOW)

    assert stale.timing_confidence < fresh.timing_confidence
    assert stale.stale_evidence


def test_macro_whale_and_scenario_backed_inputs_influence_timing() -> None:
    base = OpportunityTimingEvidenceEngine().assess_project(_result(_sources()), as_of=NOW)
    macro_weak = OpportunityTimingEvidenceEngine().assess_project(
        _result(_replace_score(_sources(), "macro_intelligence", 0.1)),
        as_of=NOW,
    )
    whale_weak = OpportunityTimingEvidenceEngine().assess_project(
        _result(_replace_score(_sources(), "whale_intelligence", 0.1)),
        as_of=NOW,
    )
    scenario_weak = OpportunityTimingEvidenceEngine().assess_project(
        _result(_replace_score(_sources(), "capital_rotation", 0.1)),
        as_of=NOW,
    )

    assert macro_weak.entry_score < base.entry_score
    assert whale_weak.accumulation_score < base.accumulation_score
    assert scenario_weak.entry_score < base.entry_score


def test_historical_replay_influences_confidence_only(monkeypatch) -> None:
    result = _result(_sources())
    monkeypatch.setattr("hunter.timing.engine._historical_confidence", lambda project_id, as_of: 1.0)
    high = OpportunityTimingEvidenceEngine().assess_project(result, as_of=NOW)
    monkeypatch.setattr("hunter.timing.engine._historical_confidence", lambda project_id, as_of: 0.5)
    low = OpportunityTimingEvidenceEngine().assess_project(result, as_of=NOW)

    assert low.classification == high.classification
    assert low.entry_score == high.entry_score
    assert low.timing_confidence < high.timing_confidence


def test_future_dated_sources_are_ignored_to_prevent_future_leakage() -> None:
    sources = tuple(
        _source(engine, timestamp=NOW + timedelta(days=1)) if engine == "macro_intelligence" else _source(engine)
        for engine in REQUIRED_TIMING_ENGINES
    )

    assessment = OpportunityTimingEvidenceEngine().assess_project(_result(sources), as_of=NOW)

    assert assessment.classification == "INSUFFICIENT_EVIDENCE"
    assert "macro_intelligence" in assessment.missing_evidence


def test_prior_timing_outputs_are_not_reused_as_timing_inputs() -> None:
    sources = _sources() + (_source("opportunity_timing", score=1.0),)

    assessment = OpportunityTimingEvidenceEngine().assess_project(_result(sources), as_of=NOW)

    assert "opportunity_timing" not in assessment.source_engines
    assert all("opportunity_timing" not in evidence_id for evidence_id in assessment.evidence_ids)


def test_timing_source_integrates_with_weight_framework_and_committee_inputs() -> None:
    assessment = OpportunityTimingEvidenceEngine().assess_project(_result(_sources()), as_of=NOW)
    source = _timing_source(assessment)

    weighted = WeightEngine().apply((source,))[0]

    assert source.engine == "opportunity_timing"
    assert weighted.base_weight > 0.0
    assert weighted.weighted_contribution > 0.0
    assert weighted.evidence_ids == assessment.evidence_ids
    assert weighted.repository_ids == assessment.repository_ids


def test_explainability_includes_opportunity_timing_lineage() -> None:
    timing = _source("opportunity_timing", score=0.72)
    result = _result(
        tuple(_source(engine) for engine in REQUIRED_ENGINES if engine != "opportunity_timing") + (timing,)
    )
    run = MarketValidationRunner(load_market_validation_config(), executor=_SingleResultExecutor(result)).run()

    audit = DecisionExplainabilityEngine().explain_project(run, "bitcoin")
    trace = next(item for item in audit.evidence_trace if item.engine == "Opportunity Timing")

    assert trace.evidence_ids == timing.evidence_ids
    assert trace.repository_ids == timing.repository_ids
    assert trace.confidence > 0.0


def test_timing_package_does_not_call_providers_directly() -> None:
    timing_source = "\n".join(path.read_text(encoding="utf-8") for path in Path("src/hunter/timing").rglob("*.py"))

    assert "requests" not in timing_source
    assert "httpx" not in timing_source
    assert "Provider(" not in timing_source


def test_timing_cli_commands_execute() -> None:
    assert main(["timing", "status"]) == 0
    assert main(["timing", "coverage"]) == 0
    assert main(["timing", "validate"]) == 0
    assert main(["timing", "history"]) == 0


def test_timing_repository_marks_newer_dependencies_stale(tmp_path: Path) -> None:
    repository = TimingRepository(tmp_path / "timing")
    assessment = OpportunityTimingEvidenceEngine(repository=repository).assess_project(_result(_sources()), as_of=NOW)
    saved = _dependencies(protocol_timestamp=NOW)
    current = _dependencies(protocol_timestamp=NOW + timedelta(hours=1))

    repository.save((assessment,), dependencies=saved)
    status = repository.rebuild_status(current)

    assert status.status == "STALE_TIMING_REBUILD_REQUIRED"
    assert status.stale_dependencies == ("protocol",)


def test_timing_repository_current_when_dependency_fingerprints_match(tmp_path: Path) -> None:
    repository = TimingRepository(tmp_path / "timing")
    assessment = OpportunityTimingEvidenceEngine(repository=repository).assess_project(_result(_sources()), as_of=NOW)
    dependencies = _dependencies(protocol_timestamp=NOW)

    repository.save((assessment,), dependencies=dependencies)

    assert repository.rebuild_status(dependencies).status == "CURRENT"


def test_missing_timing_dependency_metadata_requires_rebuild(tmp_path: Path) -> None:
    repository = TimingRepository(tmp_path / "timing")
    assessment = OpportunityTimingEvidenceEngine(repository=repository).assess_project(_result(_sources()), as_of=NOW)

    repository.save((assessment,))
    status = repository.rebuild_status(_dependencies(protocol_timestamp=NOW))

    assert status.status == "STALE_TIMING_REBUILD_REQUIRED"
    assert status.stale_dependencies == ("missing_dependency_metadata",)


def _result(sources: tuple[EngineValidationSource, ...]):
    config = load_market_validation_config()
    return next(
        item
        for item in MarketValidationRunner(
            config,
            executor=SourceBackedV1ProjectExecutor(NOW, {"bitcoin": sources}),
        )
        .run()
        .project_results
        if item.project_id == "bitcoin"
    )


def _sources(*, freshness: float = 0.9) -> tuple[EngineValidationSource, ...]:
    return tuple(_source(engine, freshness=freshness) for engine in REQUIRED_TIMING_ENGINES)


def _replace_score(
    sources: tuple[EngineValidationSource, ...],
    engine: str,
    score: float,
) -> tuple[EngineValidationSource, ...]:
    return tuple(_source(item.engine, score=score) if item.engine == engine else item for item in sources)


def _source(
    engine: str,
    *,
    score: float = 0.7,
    confidence: float = 0.8,
    freshness: float = 0.9,
    timestamp: datetime = NOW,
) -> EngineValidationSource:
    return EngineValidationSource(
        engine=engine,
        score=score,
        confidence=confidence,
        timestamp=timestamp,
        freshness=freshness,
        source_record_ids=(f"record:{engine}",),
        evidence_ids=(f"evidence:{engine}",),
        repository_ids=(f"repository:{engine}",),
        raw_input_metrics={"score": score},
        normalized_inputs={"score": score},
        evidence_coverage=1.0,
    )


class _SingleResultExecutor:
    def __init__(self, result) -> None:
        self.result = result

    def execute_project(self, target, *, run_id: str):
        if target.project_id == self.result.project_id:
            return self.result
        return SourceBackedV1ProjectExecutor(NOW, {}).execute_project(target, run_id=run_id)


def _dependencies(*, protocol_timestamp: datetime) -> TimingDependencySnapshot:
    return TimingDependencySnapshot(
        generation_timestamp=NOW,
        dependency_timestamps={"protocol": protocol_timestamp},
        dependency_fingerprints={"protocol": f"protocol:{protocol_timestamp.isoformat()}"},
        protocol_evidence_timestamp=protocol_timestamp,
    )
