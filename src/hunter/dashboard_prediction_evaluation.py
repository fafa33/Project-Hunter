from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from hunter.persistence.prediction_evaluation import (
    ACCURACY_TYPE,
    CALIBRATION_TYPE,
    EVALUATION_TYPE,
    PredictionEvaluationStore,
    load_prediction_evaluation_config,
    prediction_evaluation_store_status,
)
from hunter.persistence.records import AnalyticalRecord

PROJECTION_VERSION = "canonical-prediction-evaluation-dashboard.v1"
AUTHORITY_CLASSIFICATION = "canonical-evaluation"
LIFECYCLE_STATES = (
    "pending",
    "awaiting-horizon",
    "awaiting-data",
    "evaluable",
    "evaluated-correct",
    "evaluated-incorrect",
    "invalidated",
    "legacy-unevaluable",
    "superseded",
)
_COMPATIBILITY_FIELDS = (
    "aggregate_id",
    "cohort",
    "filter_definition",
    "target_ids",
    "window_start",
    "window_end",
    "policy_id",
    "policy_version",
    "model_version",
    "methodology_version",
    "configuration_version",
    "source_evaluation_ids",
    "source_record_fingerprint",
    "numerator",
    "denominator",
    "exclusions",
)


def build_prediction_evaluation_projection(*, config_path: Path, root: Path, as_of: datetime) -> dict[str, Any]:
    base = _base()
    try:
        config = load_prediction_evaluation_config(config_path)
    except (OSError, ValueError) as exc:
        return {**base, "status": "unavailable", "reason": f"configuration unavailable: {exc}"}
    if not config.enabled:
        return {**base, "status": "unavailable", "reason": "canonical evaluation persistence is disabled"}

    store_path = config.database_path if config.database_path.is_absolute() else root / config.database_path
    readiness = prediction_evaluation_store_status(store_path)
    if readiness == "absent":
        return {**base, "store_readiness": "absent", "status": "unavailable", "reason": "store is absent"}
    if readiness == "unreachable":
        return {**base, "store_readiness": "unreachable", "status": "error", "reason": "store is unreachable"}
    if readiness == "schema-only":
        return {**base, "store_readiness": "schema-only", "status": "empty", "reason": "store has no canonical records"}

    try:
        store = PredictionEvaluationStore(store_path)
        with store.read_repository() as repository:
            evaluations = _current_known(repository, EVALUATION_TYPE, as_of)
            accuracies = _current_known(repository, ACCURACY_TYPE, as_of)
            calibrations = _current_known(repository, CALIBRATION_TYPE, as_of)
    except Exception as exc:  # pragma: no cover - backend failures vary
        return {**base, "store_readiness": "unreachable", "status": "error", "reason": str(exc)}

    if not evaluations and not accuracies and not calibrations:
        return {
            **base,
            "store_readiness": "populated",
            "status": "empty",
            "reason": "no eligible canonical evaluation records",
        }

    counts = Counter(str(record.payload.get("state")) for record in evaluations)
    lifecycle = {
        "counts": {state: counts.get(state, 0) for state in LIFECYCLE_STATES},
        "record_ids": [record.id for record in evaluations],
        "total": len(evaluations),
    }
    calibration_by_target = {str(item.payload["target_identity"]): item for item in calibrations}
    aggregates = []
    for accuracy in accuracies:
        target = str(accuracy.payload["target_identity"])
        calibration = calibration_by_target.get(target)
        if calibration is not None and _compatible(accuracy, calibration):
            aggregates.append(_aggregate_projection(accuracy, calibration))
    aggregates.sort(key=lambda item: (str(item["aggregate_id"]), str(item["accuracy_record_id"])))
    overall = "available" if any(item["status"] == "available" for item in aggregates) else "insufficient-sample"
    reason = None if aggregates else "no compatible current accuracy/calibration snapshot pair"
    return {
        **base,
        "store_readiness": "populated",
        "status": overall,
        "reason": reason,
        "lifecycle": lifecycle,
        "aggregates": aggregates,
    }


def _base() -> dict[str, Any]:
    return {
        "projection_version": PROJECTION_VERSION,
        "authority_classification": AUTHORITY_CLASSIFICATION,
        "read_only": True,
        "owner": "PredictionEvaluationService",
        "store_readiness": "unconfigured",
        "status": "unavailable",
        "reason": None,
        "lifecycle": None,
        "aggregates": None,
    }


def _current_known(repository, semantic_type: str, as_of: datetime) -> tuple[AnalyticalRecord, ...]:
    targets = sorted({str(item.payload["target_identity"]) for item in repository.by_semantic_type(semantic_type)})
    records = (
        repository.strict_known_target(semantic_type, target, effective_as_of=as_of, known_by=as_of)
        for target in targets
    )
    return tuple(record for record in records if record is not None and record.strict_known_eligible)


def _compatible(accuracy: AnalyticalRecord, calibration: AnalyticalRecord) -> bool:
    left = accuracy.payload.get("aggregate")
    right = calibration.payload.get("aggregate")
    return (
        isinstance(left, dict)
        and isinstance(right, dict)
        and all(left.get(key) == right.get(key) for key in _COMPATIBILITY_FIELDS)
    )


def _aggregate_projection(accuracy: AnalyticalRecord, calibration: AnalyticalRecord) -> dict[str, Any]:
    accuracy_data = accuracy.payload["aggregate"]
    calibration_data = calibration.payload["aggregate"]
    status = (
        "available" if accuracy_data["status"] == calibration_data["status"] == "available" else "insufficient-sample"
    )
    return {
        "aggregate_id": accuracy_data["aggregate_id"],
        "status": status,
        "cohort": accuracy_data["cohort"],
        "filter_definition": accuracy_data["filter_definition"],
        "target_ids": accuracy_data["target_ids"],
        "window_start": accuracy_data["window_start"],
        "window_end": accuracy_data["window_end"],
        "policy_id": accuracy_data["policy_id"],
        "policy_version": accuracy_data["policy_version"],
        "model_version": accuracy_data["model_version"],
        "methodology_version": accuracy_data["methodology_version"],
        "configuration_version": accuracy_data["configuration_version"],
        "source_evaluation_ids": accuracy_data["source_evaluation_ids"],
        "source_record_fingerprint": accuracy_data["source_record_fingerprint"],
        "source_count": len(accuracy_data["source_evaluation_ids"]),
        "numerator": accuracy_data["numerator"],
        "denominator": accuracy_data["denominator"],
        "exclusions": accuracy_data["exclusions"],
        "accuracy": accuracy_data["accuracy"],
        "confidence_interval_95_wilson": accuracy_data["confidence_interval_95_wilson"],
        "brier_score": calibration_data["brier_score"],
        "reliability_bins": calibration_data["reliability_bins"],
        "accuracy_record_id": accuracy.id,
        "calibration_record_id": calibration.id,
        "accuracy_provenance": _record_provenance(accuracy),
        "calibration_provenance": _record_provenance(calibration),
    }


def _record_provenance(record: AnalyticalRecord) -> dict[str, Any]:
    return {
        "schema_version": record.schema_version,
        "effective_at": record.effective_at.isoformat(),
        "recorded_at": record.recorded_at.isoformat(),
        "known_at": record.known_at.isoformat() if record.known_at else None,
        "source_record_ids": list(record.source_record_ids),
        "source_versions": list(record.source_versions),
    }
