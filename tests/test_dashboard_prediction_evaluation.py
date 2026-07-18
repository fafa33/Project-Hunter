from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from hunter.dashboard_prediction_evaluation import PROJECTION_VERSION, build_prediction_evaluation_projection
from hunter.persistence.models import AuthorizedAnalyticalWrite
from hunter.persistence.prediction_evaluation import (
    ACCURACY_TYPE,
    CALIBRATION_TYPE,
    EVALUATION_TYPE,
    PredictionEvaluationStore,
    bootstrap_prediction_evaluation_store,
)
from hunter.persistence.records import AnalyticalRecord

NOW = datetime(2026, 1, 5, tzinfo=UTC)


def test_unconfigured_store_is_unavailable(tmp_path: Path) -> None:
    config = _config(tmp_path, enabled=False)
    projection = _project(tmp_path, config)
    assert projection["status"] == "unavailable"
    assert projection["store_readiness"] == "unconfigured"
    assert projection["lifecycle"] is None and projection["aggregates"] is None


def test_configured_absent_store_is_unavailable_without_creating_it(tmp_path: Path) -> None:
    config = _config(tmp_path)
    projection = _project(tmp_path, config)
    assert projection["store_readiness"] == "absent"
    assert not (tmp_path / "evaluation.sqlite").exists()


def test_schema_only_store_is_empty_without_fabricated_counts(tmp_path: Path) -> None:
    bootstrap_prediction_evaluation_store(tmp_path / "evaluation.sqlite")
    projection = _project(tmp_path, _config(tmp_path))
    assert projection["status"] == "empty"
    assert projection["lifecycle"] is None and projection["aggregates"] is None


def test_populated_store_projects_current_strict_known_lifecycle_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    old = _record(EVALUATION_TYPE, "prediction-1", "old", state="pending")
    current = _record(EVALUATION_TYPE, "prediction-1", "current", state="evaluated-correct", supersedes=old)
    unknown = _record(EVALUATION_TYPE, "prediction-2", "unknown", state="evaluated-incorrect", known=False)
    future = _record(EVALUATION_TYPE, "prediction-3", "future", state="pending", effective=NOW + timedelta(days=1))
    _persist(store, old, current, unknown, future)
    projection = _project(tmp_path, _config(tmp_path))
    assert projection["lifecycle"]["total"] == 1
    assert projection["lifecycle"]["record_ids"] == [current.id]
    assert projection["lifecycle"]["counts"]["evaluated-correct"] == 1
    assert projection["lifecycle"]["counts"]["superseded"] == 0


def test_compatible_accuracy_and_calibration_are_projected_with_provenance(tmp_path: Path) -> None:
    store = _store(tmp_path)
    evaluation = _record(EVALUATION_TYPE, "prediction-1", "evaluation", state="evaluated-correct")
    accuracy = _aggregate_record(ACCURACY_TYPE, "cohort-1", "accuracy", (evaluation.id,))
    calibration = _aggregate_record(CALIBRATION_TYPE, "cohort-1", "calibration", (evaluation.id,))
    _persist(store, evaluation, accuracy, calibration)
    projection = _project(tmp_path, _config(tmp_path))
    aggregate = projection["aggregates"][0]
    assert projection["status"] == "available"
    assert aggregate["accuracy_record_id"] == accuracy.id
    assert aggregate["calibration_record_id"] == calibration.id
    assert aggregate["source_evaluation_ids"] == [evaluation.id]
    assert aggregate["accuracy_provenance"]["known_at"] == accuracy.known_at.isoformat()
    assert aggregate["calibration_provenance"]["schema_version"] == calibration.schema_version
    assert aggregate["accuracy"] == 1.0 and aggregate["brier_score"] == 0.04


def test_insufficient_sample_preserves_null_metrics(tmp_path: Path) -> None:
    store = _store(tmp_path)
    accuracy = _aggregate_record(ACCURACY_TYPE, "sparse", "accuracy", (), sufficient=False)
    calibration = _aggregate_record(CALIBRATION_TYPE, "sparse", "calibration", (), sufficient=False)
    _persist(store, accuracy, calibration)
    projection = _project(tmp_path, _config(tmp_path))
    assert projection["status"] == "insufficient-sample"
    assert projection["aggregates"][0]["accuracy"] is None
    assert projection["aggregates"][0]["brier_score"] is None


def test_incompatible_aggregate_snapshots_are_not_mixed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    accuracy = _aggregate_record(ACCURACY_TYPE, "cohort-1", "accuracy", (), fingerprint="one")
    calibration = _aggregate_record(CALIBRATION_TYPE, "cohort-1", "calibration", (), fingerprint="two")
    _persist(store, accuracy, calibration)
    projection = _project(tmp_path, _config(tmp_path))
    assert projection["status"] == "insufficient-sample"
    assert projection["aggregates"] == []


def test_multiple_compatible_cohorts_remain_separate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    records = tuple(
        _aggregate_record(kind, cohort, suffix, ())
        for cohort in ("cohort-a", "cohort-b")
        for kind, suffix in ((ACCURACY_TYPE, "accuracy"), (CALIBRATION_TYPE, "calibration"))
    )
    _persist(store, *records)
    projection = _project(tmp_path, _config(tmp_path))
    assert [item["aggregate_id"] for item in projection["aggregates"]] == ["cohort-a", "cohort-b"]


def test_unreachable_store_reports_error(tmp_path: Path) -> None:
    (tmp_path / "evaluation.sqlite").write_text("not sqlite", encoding="utf-8")
    projection = _project(tmp_path, _config(tmp_path))
    assert projection["status"] == "error"
    assert projection["store_readiness"] == "unreachable"


def test_projection_is_read_only_and_versioned(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _persist(store, _record(EVALUATION_TYPE, "prediction-1", "evaluation", state="pending"))
    path = tmp_path / "evaluation.sqlite"
    before = path.read_bytes()
    projection = _project(tmp_path, _config(tmp_path))
    assert projection["projection_version"] == PROJECTION_VERSION
    assert projection["authority_classification"] == "canonical-evaluation"
    assert projection["read_only"] is True
    assert path.read_bytes() == before


def _project(root: Path, config: Path):
    return build_prediction_evaluation_projection(config_path=config, root=root, as_of=NOW)


def _config(root: Path, *, enabled: bool = True) -> Path:
    path = root / "prediction.yaml"
    path.write_text(
        f"prediction_evaluation_persistence:\n  enabled: {str(enabled).lower()}\n  database_path: evaluation.sqlite\n",
        encoding="utf-8",
    )
    return path


def _store(root: Path) -> PredictionEvaluationStore:
    path = bootstrap_prediction_evaluation_store(root / "evaluation.sqlite")
    return PredictionEvaluationStore(path)


def _persist(store: PredictionEvaluationStore, *records: AnalyticalRecord) -> None:
    with store.repository() as repository:
        for record in records:
            repository.persist(AuthorizedAnalyticalWrite(record, "correct" if record.supersedes_id else "create"))


def _record(
    semantic_type: str,
    target: str,
    suffix: str,
    *,
    state: str | None = None,
    known: bool = True,
    effective: datetime = NOW - timedelta(days=1),
    supersedes: AnalyticalRecord | None = None,
    aggregate: dict[str, object] | None = None,
) -> AnalyticalRecord:
    return AnalyticalRecord(
        id=f"prediction-evaluation:{target}:{suffix}",
        schema_version="canonical-prediction-evaluation-v1",
        created_at=NOW - timedelta(hours=1),
        effective_at=effective,
        logical_identity=f"{semantic_type}:{target}",
        semantic_type=semantic_type,
        known_at=NOW - timedelta(hours=1) if known else None,
        known_time_limitation=None if known else "unknown-known-time",
        model_version="model-v1",
        methodology_fingerprint="method-v1",
        source_record_ids=(),
        source_versions=(),
        evidence_references=(),
        confidence=None,
        missing_evidence=(),
        supersedes_id=supersedes.id if supersedes else None,
        correction_reason="correction" if supersedes else None,
        payload={
            "authority_classification": "canonical-evaluation",
            "target_identity": target,
            **({"state": state} if state else {}),
            **({"aggregate": aggregate} if aggregate else {}),
        },
    )


def _aggregate_record(
    semantic_type: str,
    target: str,
    suffix: str,
    sources: tuple[str, ...],
    *,
    sufficient: bool = True,
    fingerprint: str = "fingerprint",
) -> AnalyticalRecord:
    data = {
        "aggregate_id": target,
        "cohort": target,
        "filter_definition": "compatible canonical evaluations",
        "target_ids": ["prediction-1"],
        "window_start": "2026-01-01T00:00:00+00:00",
        "window_end": "2026-01-04T00:00:00+00:00",
        "policy_id": "policy-1",
        "policy_version": "1",
        "model_version": "model-v1",
        "methodology_version": "method-v1",
        "configuration_version": "config-v1",
        "source_evaluation_ids": list(sources),
        "source_record_fingerprint": fingerprint,
        "numerator": len(sources),
        "denominator": len(sources),
        "exclusions": {},
        "status": "available" if sufficient else "insufficient-sample",
    }
    if semantic_type == ACCURACY_TYPE:
        data.update(
            accuracy=1.0 if sufficient else None, confidence_interval_95_wilson=[0.2, 1.0] if sufficient else None
        )
    else:
        data.update(brier_score=0.04 if sufficient else None, reliability_bins=[])
    return _record(semantic_type, target, suffix, aggregate=data)
