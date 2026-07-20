from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hunter.cli import main
from hunter.market_validation import MarketValidationRenderer, MarketValidationRunner, compare_runs
from hunter.market_validation.configuration import load_market_validation_config
from hunter.market_validation.evidence import EvidenceCoverageAnalyzer, EvidenceReportRenderer
from hunter.market_validation.models import EngineValidationSource
from hunter.market_validation.repositories import result_to_record, run_to_record
from hunter.market_validation.runner import REQUIRED_ENGINES, EvidenceBackedProjectExecutor
from hunter.persistence.models import QuerySpec
from hunter.persistence.serialization import record_from_json, record_to_json
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine


def test_market_validation_config_supports_at_least_50_projects() -> None:
    config = load_market_validation_config()

    assert len(config.project_universe) >= 50
    assert {"csv", "json", "markdown"}.issubset(set(config.report_formats))


def test_market_validation_run_is_deterministic_and_ranked() -> None:
    config = load_market_validation_config()

    first = MarketValidationRunner(config).run()
    second = MarketValidationRunner(config).run()

    assert first == second
    assert len(first.project_results) >= 50
    assert [item.rank for item in first.project_results] == list(range(1, len(first.project_results) + 1))
    assert first.project_results[0].hunter_score >= first.project_results[-1].hunter_score
    assert first.champion_project_id is None
    assert first.no_qualified_candidate is True
    assert {item.committee_decision for item in first.project_results} == {"INSUFFICIENT_EVIDENCE"}
    assert all(item.sector_rank >= 1 for item in first.project_results)


def test_market_validation_reports_include_required_sections(tmp_path: Path) -> None:
    run = MarketValidationRunner(load_market_validation_config()).run()
    renderer = MarketValidationRenderer()

    markdown = renderer.render_markdown(run)
    csv_report = renderer.render_csv(run)
    json_report = renderer.render_json(run)
    files = renderer.write_reports(run, tmp_path)

    assert "Full Ranking" in markdown
    assert "Sector Ranking" in markdown
    assert "Score Breakdown" in markdown
    assert "committee_decision" in csv_report
    assert json.loads(json_report)["results"][0]["final_rank"] == 1
    assert {path.suffix for path in files} == {".csv", ".md", ".json"}


def test_market_validation_comparison_is_stable_for_identical_inputs() -> None:
    run = MarketValidationRunner(load_market_validation_config()).run()
    comparison = compare_runs(run, run)

    assert comparison.champion_change == "unchanged"
    assert all(delta.rank_change == 0 for delta in comparison.project_deltas)
    assert all(delta.score_change == 0 for delta in comparison.project_deltas)
    assert all(delta.confidence_change == 0 for delta in comparison.project_deltas)
    assert all(delta.committee_change == "unchanged" for delta in comparison.project_deltas)
    assert all(delta.evidence_change == 0 for delta in comparison.project_deltas)


def test_market_validation_persistence_round_trip_and_sql_repositories() -> None:
    run = MarketValidationRunner(load_market_validation_config()).run()
    run_record = run_to_record(run)
    result_record = result_to_record(run.project_results[0], effective_at=run.effective_at)

    assert record_from_json(record_to_json(run_record)) == run_record
    assert record_from_json(record_to_json(result_record)) == result_record

    engine = create_sqlite_engine()
    create_schema(engine)
    with SessionFactory(engine).create() as session:
        repositories = RepositoryFactory(session)
        repositories.market_validation_runs().save(run_record)
        repositories.market_validation_project_results().save(result_record)
        session.commit()

        assert repositories.market_validation_runs().load(run_record.id) == run_record
        assert repositories.market_validation_project_results().query(
            QuerySpec(record_kind="market-validation-project-result")
        )


def test_market_validation_cli_commands_execute() -> None:
    assert main(["market-validation", "run"]) == 0
    assert main(["market-validation", "report"]) == 0
    assert main(["market-validation", "compare", "a", "b"]) == 0
    assert main(["market-validation", "history"]) == 0


def test_evidence_cli_commands_execute() -> None:
    assert main(["evidence", "status"]) == 0
    assert main(["evidence", "coverage"]) == 0
    assert main(["evidence", "validate"]) == 0
    assert main(["evidence", "sources"]) == 0
    assert main(["evidence", "missing"]) == 0


def test_market_validation_cli_report_uses_real_persisted_acquisition_evidence(capsys) -> None:
    assert main(["market-validation", "report"]) == 0
    report = capsys.readouterr().out

    assert main(["evidence", "status"]) == 0
    evidence_status = capsys.readouterr().out

    assert "Coverage: 0.00%" not in report
    assert "Coverage:" in evidence_status
    expected_line = next(line for line in evidence_status.splitlines() if line.startswith("Coverage:"))
    assert expected_line in report


def test_explain_cli_uses_real_persisted_acquisition_evidence_not_empty_sources(capsys) -> None:
    assert main(["explain", "aave"]) == 0
    audit = capsys.readouterr().out

    assert "| Valuation | 0.0000 |" in audit
    assert "contract_unavailable:valuation" in audit


def test_market_validation_boundaries() -> None:
    source_files = tuple(Path("src/hunter").rglob("*.py"))
    sqlalchemy_violations = [
        str(path)
        for path in source_files
        if "sqlalchemy" in path.read_text(encoding="utf-8") and "src/hunter/persistence/" not in str(path)
    ]
    market_validation_source = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("src/hunter/market_validation").rglob("*.py")
    )

    assert sqlalchemy_violations == []
    assert "requests" not in market_validation_source
    assert "httpx" not in market_validation_source
    assert "buy" not in market_validation_source.lower()
    assert "investment validity" not in market_validation_source.lower()


def test_missing_upstream_records_cause_abstention_and_no_champion() -> None:
    run = MarketValidationRunner(load_market_validation_config()).run()

    assert run.no_qualified_candidate is True
    assert run.champion_project_id is None
    assert all(result.hunter_score == 0.0 for result in run.project_results)
    assert all(result.committee_decision == "INSUFFICIENT_EVIDENCE" for result in run.project_results)
    assert all(result.missing_evidence for result in run.project_results)


def test_real_evidence_coverage_reports_missing_unavailable_and_trace_fields() -> None:
    config = load_market_validation_config()
    run = MarketValidationRunner(config).run()
    report = EvidenceCoverageAnalyzer().analyze(run)
    rendered = EvidenceReportRenderer().render_coverage(report)

    assert report.stats.available_percent == 0.0
    assert report.stats.missing_percent == 100.0
    assert all(source.status == "UNAVAILABLE" for source in report.sources)
    assert "Evidence Completeness" in rendered
    assert "Independent production engines" in rendered
    assert "Derived analytical views" in rendered


def test_zero_confidence_engine_cannot_report_complete_evidence() -> None:
    with pytest.raises(ValueError, match="zero-confidence"):
        _source("valuation", 0.0, confidence=0.0, missing_fields=())


def test_evidence_backed_validation_uses_distinct_confidence_freshness_and_evidence() -> None:
    config = load_market_validation_config()
    alpha_sources = _sources(score=0.8, confidence=0.9, freshness=0.85, prefix="alpha")
    beta_sources = _sources(score=0.5, confidence=0.6, freshness=0.7, prefix="beta")
    executor = EvidenceBackedProjectExecutor(
        config.effective_at,
        {
            "bitcoin": alpha_sources,
            "ethereum": beta_sources,
        },
    )

    run = MarketValidationRunner(config, executor=executor).run()
    bitcoin = next(result for result in run.project_results if result.project_id == "bitcoin")
    ethereum = next(result for result in run.project_results if result.project_id == "ethereum")

    assert bitcoin.hunter_score != ethereum.hunter_score
    assert bitcoin.confidence != ethereum.confidence
    assert bitcoin.data_freshness != ethereum.data_freshness
    assert bitcoin.committee_decision == "QUALIFIED_CANDIDATE"
    assert bitcoin.engine_sources[0].source_record_ids[0].startswith("record:alpha")
    assert bitcoin.engine_sources[0].evidence_ids[0].startswith("evidence:alpha")


def test_placeholder_scores_cannot_qualify_project_when_required_engine_is_missing() -> None:
    config = load_market_validation_config()
    incomplete = tuple(
        source
        for source in _sources(score=0.95, confidence=0.95, freshness=0.95, prefix="partial")
        if source.engine != "committee"
    )
    executor = EvidenceBackedProjectExecutor(config.effective_at, {"safe": incomplete})

    safe = next(
        result
        for result in MarketValidationRunner(config, executor=executor).run().project_results
        if result.project_id == "safe"
    )

    assert safe.committee_decision == "INSUFFICIENT_EVIDENCE"
    assert "committee" in safe.missing_evidence


def _sources(*, score: float, confidence: float, freshness: float, prefix: str) -> tuple[EngineValidationSource, ...]:
    weight = round(1.0 / len(REQUIRED_ENGINES), 6)
    return tuple(
        _source(engine, score, confidence=confidence, freshness=freshness, prefix=prefix, weight=weight)
        for engine in REQUIRED_ENGINES
    )


def _source(
    engine: str,
    score: float,
    *,
    confidence: float,
    freshness: float = 0.8,
    prefix: str = "fixture",
    weight: float = 0.1,
    missing_fields: tuple[str, ...] = (),
) -> EngineValidationSource:
    source = "opportunity-timing" if engine == "opportunity_timing" else "persisted-upstream"
    collector = "timing-repository" if engine == "opportunity_timing" else "repository"
    return EngineValidationSource(
        engine=engine,
        score=score,
        confidence=confidence,
        timestamp=datetime(2026, 7, 11, tzinfo=UTC),
        freshness=freshness,
        source_record_ids=(f"record:{prefix}:{engine}",),
        evidence_ids=(f"evidence:{prefix}:{engine}",),
        repository_ids=(f"repository:{prefix}:{engine}",),
        source=source,
        collector=collector,
        raw_input_metrics={"raw": score, "source": prefix},
        normalized_inputs={"normalized": score},
        applied_weight=weight,
        weighted_contribution=round(score * weight, 4),
        missing_fields=missing_fields,
        warnings=tuple(f"missing:{item}" for item in missing_fields),
    )
