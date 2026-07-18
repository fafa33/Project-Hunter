from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.persistence import AuthorizedAnalyticalWrite
from hunter.persistence.sql.exceptions import (
    AnalyticalWriteAuthorizationError,
    PersistenceIdentityConflictError,
)
from hunter.prediction_evaluation import (
    AggregateRequest,
    EvaluationContext,
    EvaluationPolicy,
    OutcomeObservation,
    PredictionEvaluationService,
    PredictionEvaluationStore,
    PredictionPublication,
    bootstrap_prediction_evaluation_store,
    load_prediction_evaluation_config,
    prediction_evaluation_store_status,
)

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def test_policy_is_immutable_idempotent_and_store_is_opt_in(tmp_path: Path) -> None:
    service, store = _service_store(tmp_path)
    policy = _policy()
    context = _context(BASE)
    first = service.persist_policy(policy, store, context)
    assert service.persist_policy(policy, store, context) == first
    with pytest.raises(PersistenceIdentityConflictError):
        service.persist_policy(replace(policy, tolerance=0.2), store, context)

    config = load_prediction_evaluation_config()
    assert config.enabled is False
    assert "prediction_evaluation" in str(config.database_path)
    assert prediction_evaluation_store_status(store.path) == "populated"
    with pytest.raises(RuntimeError, match="disabled"):
        PredictionEvaluationStore.from_config(config)


def test_publication_requires_bound_policy_and_complete_unambiguous_contract(tmp_path: Path) -> None:
    service, store = _service_store(tmp_path)
    publication = _publication()
    with pytest.raises(AnalyticalWriteAuthorizationError, match="policy"):
        service.publish(publication, store, _context(BASE + timedelta(days=1)))

    service.persist_policy(_policy(), store, _context(BASE))
    with pytest.raises(ValueError, match="incompatible"):
        service.publish(
            replace(publication, claim_type="ambiguous"),
            store,
            _context(BASE + timedelta(days=1)),
        )
    with pytest.raises(ValueError, match="provenance"):
        replace(publication, evidence_references=())


def test_valid_publication_round_trips_with_pending_lifecycle_and_provenance(tmp_path: Path) -> None:
    service, store, publication_record, pending = _published(tmp_path)
    assert pending.payload["state"] == "pending"
    assert publication_record.payload["publication"]["policy_id"] == "return-policy"
    assert publication_record.source_record_ids[-1].startswith("prediction-evaluation:")
    assert publication_record.evidence_references == ("baseline-evidence", "evidence-1")
    with store.repository() as repository:
        assert repository.load(publication_record.id) == publication_record
        assert repository.current("canonical.prediction-evaluation", "prediction-1") == pending
    # Publication retry is an immutable idempotent write.
    retried, _ = service.publish(_publication(), store, _context(BASE + timedelta(days=1)))
    assert retried == publication_record


def test_lifecycle_due_and_awaiting_data_are_deterministic_and_not_correctness(tmp_path: Path) -> None:
    service, store, _, _ = _published(tmp_path)
    due_context = _context(BASE + timedelta(days=3))
    due = service.transition("prediction-1", "due", store, due_context, reason="horizon reached")
    assert due.payload["correctness"] is None
    assert service.transition("prediction-1", "due", store, due_context, reason="retry") == due
    awaiting = service.evaluate("prediction-1", None, store, due_context)
    assert awaiting.payload["state"] == "awaiting-data"
    assert awaiting.payload["correctness"] is None
    with pytest.raises(ValueError, match="invalid"):
        service.transition("prediction-1", "pending", store, due_context, reason="cannot reverse")


@pytest.mark.parametrize(
    ("value", "expected_state", "correctness"),
    ((112.0, "evaluated-correct", True), (105.0, "evaluated-incorrect", False)),
)
def test_compliant_outcome_evaluates_exactly_under_bound_policy(
    tmp_path: Path, value: float, expected_state: str, correctness: bool
) -> None:
    service, store, _, _ = _published(tmp_path)
    context = _context(BASE + timedelta(days=3))
    service.transition("prediction-1", "due", store, context, reason="horizon reached")
    evaluation = service.evaluate("prediction-1", _outcome(value=value), store, context)
    assert evaluation.payload["state"] == expected_state
    assert evaluation.payload["correctness"] is correctness
    assert evaluation.payload["outcome"]["observation_id"] == "outcome-1"
    assert service.evaluate("prediction-1", _outcome(value=value), store, context) == evaluation


@pytest.mark.parametrize(
    ("case", "state"),
    (
        ("missing", "awaiting-data"),
        ("unknown-known", "unevaluable"),
        ("wrong-source", "invalidated"),
        ("stale", "unevaluable"),
        ("post-cutoff", "awaiting-data"),
    ),
)
def test_noncompliant_or_missing_outcomes_never_create_correctness(tmp_path: Path, case: str, state: str) -> None:
    service, store, _, _ = _published(tmp_path)
    context = _context(BASE + timedelta(days=3))
    service.transition("prediction-1", "due", store, context, reason="horizon reached")
    outcomes = {
        "missing": None,
        "unknown-known": replace(_outcome(), known_at=None),
        "wrong-source": replace(_outcome(), source_type="wrong-source"),
        "stale": replace(_outcome(), effective_at=BASE),
        "post-cutoff": replace(
            _outcome(),
            recorded_at=BASE + timedelta(days=4),
            known_at=BASE + timedelta(days=4),
        ),
    }
    evaluation = service.evaluate("prediction-1", outcomes[case], store, context)
    assert evaluation.payload["state"] == state
    assert evaluation.payload["correctness"] is None


def test_legacy_operational_prediction_is_never_evaluated_or_aggregated() -> None:
    legacy = {
        "prediction_id": "legacy-1",
        "status": "closed",
        "benchmark_values": [{"benchmark_id": "btc", "value": 1.0}],
    }
    result = PredictionEvaluationService.classify_legacy_operational_prediction(legacy)
    assert result == {
        "prediction_id": "legacy-1",
        "classification": "legacy-unevaluable",
        "reason": "operational record lacks the complete pre-outcome canonical publication contract",
    }


def test_correction_lineage_and_strict_known_exclude_successor_before_cutoff(tmp_path: Path) -> None:
    service, store, publication, _ = _published(tmp_path)
    corrected_native = replace(
        _publication(),
        recorded_at=BASE + timedelta(days=2),
        known_at=BASE + timedelta(days=2),
        threshold=11.0,
    )
    corrected, _ = service.publish(
        corrected_native,
        store,
        _context(BASE + timedelta(days=2)),
        predecessor_publication=publication,
        correction_reason="authorized threshold correction",
    )
    with store.repository() as repository:
        lineage = repository.lineage("canonical.prediction-publication:prediction-1")
        assert lineage == (publication, corrected)
        before = repository.strict_known_target(
            "canonical.prediction-publication",
            "prediction-1",
            effective_as_of=BASE + timedelta(days=3),
            known_by=BASE + timedelta(days=1),
        )
        after = repository.strict_known_target(
            "canonical.prediction-publication",
            "prediction-1",
            effective_as_of=BASE + timedelta(days=3),
            known_by=BASE + timedelta(days=2),
        )
        assert before == publication
        assert after == corrected


def test_aggregate_accuracy_calibration_and_insufficient_sample_semantics(tmp_path: Path) -> None:
    service, store, _, _ = _published(tmp_path, minimum_sample_size=1)
    context = _context(BASE + timedelta(days=3))
    service.transition("prediction-1", "due", store, context, reason="horizon reached")
    evaluation = service.evaluate("prediction-1", _outcome(value=112.0), store, context)
    request = _aggregate((evaluation.id,))
    accuracy, calibration = service.aggregate(request, store, context)
    assert accuracy.payload["aggregate"]["numerator"] == 1
    assert accuracy.payload["aggregate"]["denominator"] == 1
    assert accuracy.payload["aggregate"]["accuracy"] == 1.0
    assert calibration.payload["aggregate"]["brier_score"] == pytest.approx(0.04)
    assert service.aggregate(request, store, context) == (accuracy, calibration)

    empty_request = replace(request, aggregate_id="empty", target_ids=("no-such-target",), evaluation_ids=())
    empty_accuracy, empty_calibration = service.aggregate(empty_request, store, context)
    assert empty_accuracy.payload["aggregate"]["status"] == "insufficient-sample"
    assert empty_accuracy.payload["aggregate"]["accuracy"] is None
    assert empty_accuracy.payload["aggregate"]["denominator"] == 0
    assert empty_calibration.payload["aggregate"]["brier_score"] is None


def test_store_rejects_foreign_records_and_atomic_failure_rolls_back(tmp_path: Path) -> None:
    service, store, publication, _ = _published(tmp_path)
    forged = replace(
        publication,
        semantic_type="experimental.opportunity-assessment",
        payload={**publication.payload, "authority_classification": "experimental"},
    )
    with store.repository() as repository:
        with pytest.raises(AnalyticalWriteAuthorizationError):
            repository.persist(AuthorizedAnalyticalWrite(forged, "create"))

    first_id = "prediction-evaluation:identity-v1:atomic-first"
    first = replace(
        publication,
        id=first_id,
        logical_identity="canonical.prediction-publication:atomic-first",
        payload={**publication.payload, "target_identity": "atomic-first"},
    )
    conflict = replace(publication, payload={**publication.payload, "target_identity": "conflict"})
    # A failed two-record transaction does not persist its successful first write.
    with pytest.raises(PersistenceIdentityConflictError):
        with store.repository() as repository:
            repository.persist_many(
                (
                    AuthorizedAnalyticalWrite(first, "create"),
                    AuthorizedAnalyticalWrite(conflict, "create"),
                )
            )
    with store.repository() as repository:
        assert repository.load(first_id) is None
        assert len(repository.by_semantic_type("canonical.prediction-evaluation-policy")) == 1


def test_no_forbidden_consumer_or_runtime_wiring() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = (
        "src/hunter/dashboard_api.py",
        "src/hunter/operational_corpus.py",
        "src/hunter/automation",
        "src/hunter/market_validation",
        "src/hunter/opportunity",
        "src/hunter/timing",
        "src/hunter/backtest",
        "src/hunter/cli.py",
        "desktop/OperationalConsole",
    )
    for relative in forbidden:
        path = root / relative
        files = (path,) if path.is_file() else tuple(path.rglob("*.py")) + tuple(path.rglob("*.swift"))
        assert all("PredictionEvaluationService" not in item.read_text() for item in files)


def _service_store(tmp_path: Path) -> tuple[PredictionEvaluationService, PredictionEvaluationStore]:
    path = tmp_path / "prediction-evaluation" / "canonical.sqlite"
    assert prediction_evaluation_store_status(path) == "absent"
    bootstrap_prediction_evaluation_store(path)
    assert prediction_evaluation_store_status(path) == "schema-only"
    return PredictionEvaluationService(), PredictionEvaluationStore(path)


def _published(tmp_path: Path, *, minimum_sample_size: int = 2):
    service, store = _service_store(tmp_path)
    service.persist_policy(_policy(minimum_sample_size=minimum_sample_size), store, _context(BASE))
    publication, pending = service.publish(_publication(), store, _context(BASE + timedelta(days=1)))
    return service, store, publication, pending


def _policy(*, minimum_sample_size: int = 2) -> EvaluationPolicy:
    return EvaluationPolicy(
        policy_id="return-policy",
        policy_version="1",
        claim_type="absolute-return",
        entity_type="asset",
        comparison_mode="change-from-baseline",
        allowed_operator="gte",
        measurement_unit="usd",
        baseline_rule="exact authorized observation",
        required_precision=2,
        horizon_rule="due_at fixed at publication",
        observation_window_seconds=86_400,
        outcome_data_deadline_seconds=172_800,
        outcome_source_type="canonical-price-observation",
        outcome_source_version="v1",
        benchmark_rule=None,
        tolerance=0.0,
        missing_data_rule="unevaluable after deadline",
        ambiguous_data_rule="invalidated",
        strict_known_rule="effective, recorded, known no later than cutoffs",
        minimum_sample_size=minimum_sample_size,
        minimum_calibration_bin_size=1,
        correction_policy="immutable supersession with reason",
        methodology_version="evaluation-method-v1",
    )


def _publication() -> PredictionPublication:
    return PredictionPublication(
        prediction_id="prediction-1",
        target_id="bitcoin",
        entity_type="asset",
        claim_type="absolute-return",
        claim="Bitcoin price increases by at least USD 10",
        operator="gte",
        threshold=10.0,
        condition="outcome minus baseline is at least threshold",
        measurement_unit="usd",
        baseline_value=100.0,
        baseline_observation_id="baseline-1",
        baseline_source_version="v1",
        baseline_evidence_references=("baseline-evidence",),
        benchmark_id=None,
        effective_at=BASE + timedelta(days=1),
        published_at=BASE + timedelta(days=1),
        due_at=BASE + timedelta(days=3),
        recorded_at=BASE + timedelta(days=1),
        known_at=BASE + timedelta(days=1),
        policy_id="return-policy",
        policy_version="1",
        model_version="model-v1",
        methodology_version="method-v1",
        configuration_version="config-v1",
        source_record_ids=("source-1",),
        source_versions=("source-v1",),
        evidence_references=("evidence-1",),
        forecast_probability=0.8,
    )


def _outcome(*, value: float = 112.0) -> OutcomeObservation:
    return OutcomeObservation(
        observation_id="outcome-1",
        target_id="bitcoin",
        entity_type="asset",
        source_type="canonical-price-observation",
        source_version="v1",
        value=value,
        measurement_unit="usd",
        effective_at=BASE + timedelta(days=3),
        recorded_at=BASE + timedelta(days=3),
        known_at=BASE + timedelta(days=3),
        evidence_references=("outcome-evidence",),
    )


def _context(value: datetime) -> EvaluationContext:
    return EvaluationContext(recorded_at=value, known_by=value)


def _aggregate(evaluation_ids: tuple[str, ...]) -> AggregateRequest:
    return AggregateRequest(
        aggregate_id="aggregate-1",
        cohort="bitcoin absolute-return",
        filter_definition="target identity and compatible policy/model/configuration",
        target_ids=("prediction-1",),
        window_start=BASE,
        window_end=BASE + timedelta(days=4),
        policy_id="return-policy",
        policy_version="1",
        model_version="model-v1",
        methodology_version="method-v1",
        configuration_version="config-v1",
        evaluation_ids=evaluation_ids,
    )
