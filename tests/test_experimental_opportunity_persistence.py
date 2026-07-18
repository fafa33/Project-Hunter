from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.market_validation import MarketValidationRunner
from hunter.market_validation.configuration import load_market_validation_config
from hunter.market_validation.persistence import (
    MarketValidationPersistenceAuthorizationService,
    MarketValidationPersistenceContext,
)
from hunter.opportunity import (
    CURRENT_OPPORTUNITY_FACTORS,
    ExperimentalOpportunityRepository,
    OpportunityAssessmentService,
    OpportunityEngine,
    OpportunityPersistenceContext,
    OpportunityPersistenceService,
    opportunity_factor_trace,
)
from hunter.opportunity.metrics import OpportunityMetricSnapshot
from hunter.persistence import AuthorizedAnalyticalWrite
from hunter.persistence.experimental import ExperimentalDerivedReasoningStore, bootstrap_experimental_store
from hunter.persistence.sql.exceptions import AnalyticalWriteAuthorizationError, PersistenceIdentityConflictError

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def test_phase31_snapshot_and_linked_assessment_round_trip_exactly(tmp_path: Path) -> None:
    assembly, service, store, context = _fixture(tmp_path)
    plan = service.authorize(assembly, context)

    result = service.execute(assembly, store, context)

    assert result.assessment == OpportunityEngine().evaluate(assembly.snapshot)
    assert result.snapshot_record_id == plan.snapshot_write.record.id
    assert result.assessment_record_id == plan.assessment_write.record.id
    with store.repository() as base:
        repository = ExperimentalOpportunityRepository(base)
        assert repository.load(result.snapshot_record_id) == plan.snapshot_write.record
        assert repository.load(result.assessment_record_id) == plan.assessment_write.record
    snapshot_payload = plan.snapshot_write.record.payload
    assert len(snapshot_payload["opportunity_snapshot"]["factors"]) == 17
    assert set(snapshot_payload["opportunity_snapshot"]["factors"]) == set(CURRENT_OPPORTUNITY_FACTORS)
    assert plan.assessment_write.record.payload["metric_snapshot_record_id"] == result.snapshot_record_id
    assert plan.assessment_write.record.payload["metric_snapshot_canonical_hash"] == result.snapshot_canonical_hash


def test_assessment_score_and_factor_contributions_match_pure_engine(tmp_path: Path) -> None:
    assembly, service, _, context = _fixture(tmp_path)
    plan = service.authorize(assembly, context)
    direct = OpportunityEngine().evaluate(assembly.snapshot)

    assert plan.assessment == direct
    assert plan.assessment_write.record.payload["opportunity_assessment"] == _plain_assessment(direct)
    trace = plan.assessment_write.record.payload["per_factor_trace"]
    direct_trace = opportunity_factor_trace(assembly.snapshot)
    assert [
        {key: item[key] for key in ("name", "value", "weight", "contribution", "evidence_id", "explanation")}
        for item in trace
    ] == [_plain_assessment(item) for item in direct_trace]
    assert all("confidence" in item and "missing" in item and "source_record_ids" in item for item in trace)


def test_all_missing_snapshot_persists_without_supportive_values(tmp_path: Path) -> None:
    assembly, service, store, context = _fixture(tmp_path, partial=False)
    result = service.execute(assembly, store, context)
    assert assembly.snapshot.values.as_dict() == {"validation_health": 0.0}
    assert len(assembly.snapshot.missing_evidence) == 17
    assert assembly.snapshot.evidence_ids == ()
    assert result.assessment.opportunity_score == 0.0
    assert result.assessment.supporting_evidence == ()

    forged = replace(
        assembly,
        snapshot=OpportunityMetricSnapshot(
            project_id=assembly.snapshot.project_id,
            effective_at=assembly.effective_as_of,
            values={"validation_health": 0.0, "adoption": 1.0},
            evidence_ids=(),
            missing_evidence=assembly.snapshot.missing_evidence,
            metadata=assembly.snapshot.metadata,
        ),
    )
    with pytest.raises(ValueError, match="values do not match"):
        service.authorize(forged, context)


def test_identical_writes_are_idempotent_and_conflicts_are_rejected(tmp_path: Path) -> None:
    assembly, service, store, context = _fixture(tmp_path)
    first = service.execute(assembly, store, context)
    second = service.execute(assembly, store, context)
    assert first == second

    plan = service.authorize(assembly, context)
    forged_record = replace(
        plan.snapshot_write.record,
        payload={**plan.snapshot_write.record.payload, "input_canonical_hash": "conflict"},
    )
    with store.repository() as repository:
        with pytest.raises(PersistenceIdentityConflictError):
            repository.persist(AuthorizedAnalyticalWrite(forged_record, "create"))


def test_explicit_correction_preserves_predecessors_and_lineage(tmp_path: Path) -> None:
    assembly, service, store, context = _fixture(tmp_path)
    original = service.authorize(assembly, context)
    service.execute(assembly, store, context)
    correction_context = replace(context, recorded_at=NOW + timedelta(days=1))
    correction = service.authorize(
        assembly,
        correction_context,
        predecessor_snapshot=original.snapshot_write.record,
        predecessor_assessment=original.assessment_write.record,
        correction_reason="authorized replay metadata correction",
    )
    service.execute(
        assembly,
        store,
        correction_context,
        predecessor_snapshot=original.snapshot_write.record,
        predecessor_assessment=original.assessment_write.record,
        correction_reason="authorized replay metadata correction",
    )

    with store.repository() as base:
        repository = ExperimentalOpportunityRepository(base)
        assert repository.lineage(original.snapshot_write.record.logical_identity) == (
            original.snapshot_write.record,
            correction.snapshot_write.record,
        )
        assert repository.lineage(original.assessment_write.record.logical_identity) == (
            original.assessment_write.record,
            correction.assessment_write.record,
        )


def test_assessment_requires_exact_compatible_persisted_snapshot(tmp_path: Path) -> None:
    assembly, service, store, context = _fixture(tmp_path)
    plan = service.authorize(assembly, context)
    incompatible_assessment = replace(
        plan.assessment_write.record,
        source_record_ids=("experimental-derived:identity-v1:missing",),
    )
    incompatible_plan = replace(
        plan,
        assessment_write=AuthorizedAnalyticalWrite(incompatible_assessment, "create"),
    )
    with store.repository() as base:
        with pytest.raises(AnalyticalWriteAuthorizationError, match="incompatible"):
            ExperimentalOpportunityRepository(base).persist(incompatible_plan)


def test_strict_known_excludes_future_legacy_superseded_and_incompatible_records(tmp_path: Path) -> None:
    assembly, service, store, context = _fixture(tmp_path)
    original = service.authorize(assembly, context)
    service.execute(assembly, store, context)
    corrected_context = replace(context, recorded_at=NOW + timedelta(days=2))
    corrected = service.authorize(
        assembly,
        corrected_context,
        predecessor_snapshot=original.snapshot_write.record,
        predecessor_assessment=original.assessment_write.record,
        correction_reason="correction",
    )
    service.execute(
        assembly,
        store,
        corrected_context,
        predecessor_snapshot=original.snapshot_write.record,
        predecessor_assessment=original.assessment_write.record,
        correction_reason="correction",
    )

    with store.repository() as base:
        repository = ExperimentalOpportunityRepository(base)
        kwargs = {
            "configuration_fingerprint": original.configuration_fingerprint,
            "methodology_fingerprint": context.methodology_fingerprint,
            "factor_authority_fingerprint": original.factor_authority_fingerprint,
        }
        assert (
            repository.strict_known(
                original.snapshot_write.record.semantic_type,
                assembly.snapshot.project_id,
                effective_as_of=assembly.effective_as_of,
                known_by=NOW + timedelta(days=1),
                **kwargs,
            )
            == original.snapshot_write.record
        )
        assert (
            repository.strict_known(
                original.snapshot_write.record.semantic_type,
                assembly.snapshot.project_id,
                effective_as_of=assembly.effective_as_of,
                known_by=NOW + timedelta(days=3),
                **kwargs,
            )
            == corrected.snapshot_write.record
        )
        assert (
            repository.strict_known(
                original.snapshot_write.record.semantic_type,
                assembly.snapshot.project_id,
                effective_as_of=assembly.effective_as_of - timedelta(days=1),
                known_by=NOW + timedelta(days=3),
                **kwargs,
            )
            is None
        )
        assert (
            repository.strict_known(
                original.snapshot_write.record.semantic_type,
                assembly.snapshot.project_id,
                effective_as_of=assembly.effective_as_of,
                known_by=NOW + timedelta(days=3),
                **{**kwargs, "configuration_fingerprint": "incompatible"},
            )
            is None
        )

        legacy = replace(
            original.snapshot_write.record,
            id="experimental-derived:identity-v1:legacy-project-b",
            logical_identity="experimental.opportunity-metric-snapshot:project-b",
            known_at=None,
            known_time_limitation="legacy known time unavailable",
            payload={**original.snapshot_write.record.payload, "project_id": "project-b"},
        )
        base.persist(AuthorizedAnalyticalWrite(legacy, "create"))
        assert (
            repository.strict_known(
                legacy.semantic_type,
                "project-b",
                effective_as_of=assembly.effective_as_of,
                known_by=NOW + timedelta(days=3),
                **kwargs,
            )
            is None
        )


def test_unowned_factor_diagnostics_remain_missing_in_both_records(tmp_path: Path) -> None:
    assembly, service, _, context = _fixture(tmp_path)
    plan = service.authorize(assembly, context)
    factors = plan.snapshot_write.record.payload["opportunity_snapshot"]["factors"]
    assert len(factors) == 17
    assert sum(item["state"] == "available" for item in factors.values()) == 5
    assert sum(item["state"] == "missing" for item in factors.values()) == 12
    assert all(item["value"] is None for item in factors.values() if item["state"] == "missing")
    assert "valuation_discount" not in assembly.snapshot.values


def test_no_production_or_operational_consumer_wiring() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = (
        "src/hunter/cli.py",
        "src/hunter/pipeline.py",
        "src/hunter/dashboard_api.py",
        "src/hunter/operational_corpus.py",
        "src/hunter/market_validation",
        "src/hunter/timing",
        "src/hunter/automation",
        "src/hunter/opportunity/ranking.py",
        "desktop/OperationalConsole",
    )
    for relative in forbidden:
        path = root / relative
        files = (path,) if path.is_file() else tuple(path.rglob("*.py")) + tuple(path.rglob("*.swift"))
        assert all("OpportunityPersistenceService" not in item.read_text() for item in files)


def _fixture(tmp_path: Path, *, partial: bool = True):
    source = None
    if partial:
        config = load_market_validation_config()
        run = MarketValidationRunner(config).run()
        market_context = MarketValidationPersistenceContext(
            recorded_at=datetime(2026, 7, 12, tzinfo=UTC),
            known_at=datetime(2026, 7, 11, tzinfo=UTC),
            known_time_limitation=None,
            model_version="market-validation-v1",
            methodology_fingerprint="market-validation-method-v1",
            source_versions={},
        )
        market_plan = MarketValidationPersistenceAuthorizationService().authorize(run, config, market_context)
        record = market_plan.project_records[0]

        class Source:
            def market_validation_project_records(self, project_id: str):
                return (record,) if project_id == record.project_id else ()

        source = Source()
    project_id = record.project_id if partial else "project-a"
    assembly = OpportunityAssessmentService(source).assemble(
        project_id,
        effective_as_of=NOW - timedelta(days=1),
        known_by=NOW,
    )
    service = OpportunityPersistenceService()
    path = bootstrap_experimental_store(tmp_path / "experimental" / "derived.sqlite")
    store = ExperimentalDerivedReasoningStore(path)
    return assembly, service, store, OpportunityPersistenceContext(recorded_at=NOW)


def _plain_assessment(assessment) -> dict[str, object]:
    from hunter.opportunity.persistence_service import _plain

    return _plain(assessment)
