from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.committee.models import CommitteeVote, InvestmentCommitteeAssessment
from hunter.necessity.models import TechnologyNecessityAssessment
from hunter.patterns.models import PatternMatchingAssessment
from hunter.persistence import AnalyticalRecord, AnalyticalReplaySpec, AuthorizedAnalyticalWrite
from hunter.persistence.experimental import (
    ALLOWED_SEMANTIC_TYPES,
    DERIVED_REASONING_SEMANTIC_TYPES,
    ExperimentalAuthorizationContext,
    ExperimentalDerivedReasoningAuthorizationService,
    ExperimentalDerivedReasoningStore,
    ExperimentalPersistenceConfig,
    bootstrap_experimental_store,
    load_experimental_persistence_config,
)
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.persistence.sql.exceptions import AnalyticalWriteAuthorizationError, PersistenceIdentityConflictError
from hunter.probability.models import ProbabilityAssessment

NOW = datetime(2026, 2, 1, 12, tzinfo=UTC)
SERVICE = ExperimentalDerivedReasoningAuthorizationService()


def test_all_four_types_round_trip_in_isolated_store(tmp_path: Path) -> None:
    assessments = _assessments()
    store_path = tmp_path / "experimental" / "derived.sqlite"
    bootstrap_experimental_store(store_path)
    store = ExperimentalDerivedReasoningStore(store_path)

    records: list[AnalyticalRecord] = []
    with store.repository() as repository:
        for assessment in assessments:
            plan = SERVICE.authorize(assessment, _context(len(assessment.source_record_ids)))
            records.append(repository.persist(plan))

    with store.repository() as repository:
        for record in records:
            assert repository.load(record.id) == record
            assert record.semantic_type in ALLOWED_SEMANTIC_TYPES
            assert record.payload["authority_classification"] == "experimental"
            assert record.payload["configuration_version"] == "config-v1"
            assert record.model_version == "model-v1"
            assert record.methodology_fingerprint == "method-v1"
            assert record.recorded_at == NOW
        for semantic_type in DERIVED_REASONING_SEMANTIC_TYPES:
            assert len(repository.by_semantic_type(semantic_type)) == 1  # type: ignore[arg-type]


def test_identity_idempotency_conflict_and_explicit_correction_lineage(tmp_path: Path) -> None:
    path = bootstrap_experimental_store(tmp_path / "experimental.sqlite")
    store = ExperimentalDerivedReasoningStore(path)
    assessment = _probability()
    original = SERVICE.authorize(assessment, _context(1))

    with store.repository() as repository:
        assert repository.persist(original) == original.record
        assert repository.persist(original) == original.record
        conflicting = AuthorizedAnalyticalWrite(
            replace(
                original.record,
                payload={**original.record.payload, "native_assessment_id": "changed"},
            ),
            "create",
        )
        with pytest.raises(PersistenceIdentityConflictError):
            repository.persist(conflicting)

    corrected_assessment = replace(assessment, assessment_id="probability-2", probability_score=0.8)
    correction = SERVICE.authorize(
        corrected_assessment,
        replace(_context(1), recorded_at=NOW + timedelta(days=2)),
        predecessor=original.record,
        correction_reason="corrected source calculation",
    )
    with store.repository() as repository:
        repository.persist(correction)
        assert repository.lineage(original.record.logical_identity) == (original.record, correction.record)
        assert repository.load(original.record.id) == original.record


def test_strict_known_and_unknown_known_time_replay(tmp_path: Path) -> None:
    path = bootstrap_experimental_store(tmp_path / "experimental.sqlite")
    store = ExperimentalDerivedReasoningStore(path)
    original = SERVICE.authorize(_pattern(), _context(1))
    late = SERVICE.authorize(
        replace(_pattern(), assessment_id="pattern-2", overall_similarity=0.7),
        replace(_context(1), recorded_at=NOW + timedelta(days=3), known_at=NOW + timedelta(days=1)),
        predecessor=original.record,
        correction_reason="late correction",
    )
    unknown = SERVICE.authorize(
        _necessity(),
        replace(_context(1), known_at=None, known_time_limitation="source has no known-time field"),
    )

    with store.repository() as repository:
        repository.persist(original)
        repository.persist(late)
        repository.persist(unknown)
        assert (
            repository.strict_known(
                AnalyticalReplaySpec(original.record.logical_identity, NOW, NOW + timedelta(days=2))
            )
            == original.record
        )
        assert (
            repository.strict_known(
                AnalyticalReplaySpec(original.record.logical_identity, NOW, NOW + timedelta(days=4))
            )
            == late.record
        )
        assert (
            repository.strict_known(AnalyticalReplaySpec(unknown.record.logical_identity, NOW, NOW + timedelta(days=4)))
            is None
        )


def test_missing_evidence_confidence_and_provenance_survive_round_trip(tmp_path: Path) -> None:
    path = bootstrap_experimental_store(tmp_path / "experimental.sqlite")
    store = ExperimentalDerivedReasoningStore(path)
    plan = SERVICE.authorize(_probability(), _context(1))
    with store.repository() as repository:
        repository.persist(plan)
    with store.repository() as repository:
        restored = repository.load(plan.record.id)

    assert restored is not None
    assert restored.confidence == 0.55
    assert restored.missing_evidence == ("secondary-source",)
    assert restored.source_record_ids == ("source-1",)
    assert restored.source_versions == ("source-schema-v1",)
    assert restored.evidence_references == ("evidence-ref-1",)


def test_committee_namespace_cannot_claim_market_validation_fields(tmp_path: Path) -> None:
    plan = SERVICE.authorize(_committee(), _context(1))
    assert plan.record.semantic_type == "experimental.standalone-committee-assessment"
    assert "committee_decision" not in plan.record.payload["native_assessment"]
    assert "hunter_score" not in plan.record.payload["native_assessment"]

    path = bootstrap_experimental_store(tmp_path / "experimental.sqlite")
    store = ExperimentalDerivedReasoningStore(path)
    forged = replace(
        plan.record,
        payload={
            **plan.record.payload,
            "native_assessment": {"committee_decision": "QUALIFIED_CANDIDATE"},
        },
    )
    with store.repository() as repository:
        with pytest.raises(AnalyticalWriteAuthorizationError):
            repository.persist(AuthorizedAnalyticalWrite(forged, "create"))


def test_production_factory_and_experimental_store_are_physically_separate(tmp_path: Path) -> None:
    production_path = tmp_path / "production.sqlite"
    experimental_path = bootstrap_experimental_store(tmp_path / "experimental" / "derived.sqlite")
    production_engine = create_sqlite_engine(production_path)
    create_schema(production_engine)
    production_sessions = SessionFactory(production_engine)
    plan = SERVICE.authorize(_necessity(), _context(1))

    store = ExperimentalDerivedReasoningStore(experimental_path)
    with store.repository() as repository:
        repository.persist(plan)
    with production_sessions.create() as session:
        assert RepositoryFactory(session).analytical_records().load(plan.record.id) is None
    production_engine.dispose()


def test_store_requires_explicit_bootstrap_and_disabled_config_cannot_open(tmp_path: Path) -> None:
    path = tmp_path / "not-created.sqlite"
    with pytest.raises(FileNotFoundError):
        ExperimentalDerivedReasoningStore(path)
    assert not path.exists()

    config_path = tmp_path / "experimental.yaml"
    config_path.write_text(
        "experimental_persistence:\n  enabled: false\n  database_path: data/experimental/derived.sqlite\n"
    )
    config = load_experimental_persistence_config(config_path)
    assert config == ExperimentalPersistenceConfig(False, Path("data/experimental/derived.sqlite"))
    with pytest.raises(RuntimeError, match="disabled"):
        ExperimentalDerivedReasoningStore.from_config(config)


def test_no_production_or_operational_consumer_is_wired_to_experimental_persistence() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = (
        "src/hunter/dashboard_api.py",
        "src/hunter/operational_status.py",
        "src/hunter/market_validation",
        "src/hunter/timing",
        "src/hunter/automation",
        "src/hunter/operational_corpus",
        "desktop/OperationalConsole",
    )
    for relative in forbidden:
        path = root / relative
        files = (path,) if path.is_file() else tuple(path.rglob("*.py")) + tuple(path.rglob("*.swift"))
        assert all("persistence.experimental" not in item.read_text() for item in files)


def _context(source_count: int) -> ExperimentalAuthorizationContext:
    return ExperimentalAuthorizationContext(
        recorded_at=NOW,
        known_at=NOW - timedelta(hours=1),
        known_time_limitation=None,
        model_version="model-v1",
        configuration_version="config-v1",
        methodology_fingerprint="method-v1",
        source_versions=tuple("source-schema-v1" for _ in range(source_count)),
        evidence_references=("evidence-ref-1",),
    )


def _assessments() -> (
    tuple[
        ProbabilityAssessment, PatternMatchingAssessment, TechnologyNecessityAssessment, InvestmentCommitteeAssessment
    ]
):
    return (_probability(), _pattern(), _necessity(), _committee())


def _probability() -> ProbabilityAssessment:
    return ProbabilityAssessment(
        assessment_id="probability-1",
        target_id="project-a",
        effective_at=NOW - timedelta(days=1),
        source_record_ids=("source-1",),
        probability_score=0.6,
        success_probability=0.6,
        failure_probability=0.4,
        probability_label="Moderately Positive",
        evidence_robustness=0.5,
        historical_reliability=0.4,
        decision_confidence=0.55,
        consensus_score=0.5,
        conflict_score=0.1,
        components=(),
        largest_positive_contributors=(),
        largest_negative_contributors=(),
        supporting_engines=("engine-a",),
        conflicting_engines=(),
        supporting_evidence=("evidence-ref-1",),
        weak_evidence=(),
        missing_evidence=("secondary-source",),
        explanation=("experimental assessment",),
    )


def _pattern() -> PatternMatchingAssessment:
    return PatternMatchingAssessment(
        assessment_id="pattern-1",
        target_id="project-a",
        effective_at=NOW - timedelta(days=1),
        source_record_ids=("source-1",),
        top_matches=(),
        positive_matches=(),
        negative_matches=(),
        historical_similarity=0.5,
        context_similarity=0.4,
        overall_similarity=0.45,
        historical_confidence=0.5,
        missing_evidence=("historical-outcome",),
    )


def _necessity() -> TechnologyNecessityAssessment:
    return TechnologyNecessityAssessment(
        assessment_id="necessity-1",
        technology_id="technology-a",
        effective_at=NOW - timedelta(days=1),
        source_record_ids=("source-1",),
        technology_necessity_score=0.6,
        capital_rotation_score=0.5,
        infrastructure_criticality=0.7,
        dependency_strength=0.6,
        replacement_difficulty=0.5,
        necessity_gap=0.4,
        overall_necessity=0.6,
        label="Growing Necessity",
        components=(),
        technology_position=("infrastructure",),
        supporting_evidence=("evidence-ref-1",),
        missing_evidence=("dependency-source",),
        confidence=0.6,
    )


def _committee() -> InvestmentCommitteeAssessment:
    vote = CommitteeVote(
        id="vote-1",
        assessment_id="committee-1",
        engine_name="probability",
        vote="ABSTAIN_MISSING",
        normalized_contribution=0.0,
        source_score=0.5,
        source_confidence=0.5,
        source_timestamp=NOW - timedelta(days=1),
        freshness_state="current",
        explanation="missing corroboration",
        missing_fields=("corroboration",),
    )
    return InvestmentCommitteeAssessment(
        id="committee-1",
        project_id="project-a",
        created_at=NOW - timedelta(days=1),
        eligibility_state="INSUFFICIENT_EVIDENCE",
        decision="INSUFFICIENT_EVIDENCE",
        approval_score=0.0,
        opposition_score=0.0,
        consensus_score=0.0,
        conflict_score=0.0,
        evidence_robustness=0.2,
        committee_confidence=0.2,
        thesis_fragility=0.8,
        rank=0,
        votes=(vote,),
        positive_drivers=(),
        negative_drivers=(),
        conflicts=(),
        abstentions=("probability",),
        risks=("missing evidence",),
        invalidation_conditions=(),
        runner_up_comparison="not applicable",
        explanation=("experimental standalone committee",),
        source_record_ids=("source-1",),
    )
