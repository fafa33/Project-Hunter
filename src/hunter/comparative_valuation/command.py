from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.comparative_valuation.models import PeerCandidate
from hunter.comparative_valuation.repository import ComparativeValuationRepository
from hunter.comparative_valuation.service import CanonicalComparativeValuationService
from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.repository import SupplyAndValueCaptureRepository

_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"
_CANONICAL_PERSISTENCE_DATABASE = Path("data/data_ops.sqlite")
_OPERATIONS = (
    "peer_policy",
    "peer_universe",
    "eligibility_decision",
    "metric_observation",
    "assess",
    "status",
)
_STATUS_TARGETS = (
    "peer_policy",
    "peer_universe",
    "eligibility_decision",
    "metric_observation",
    "assessment",
)


def main(argv: list[str]) -> int:
    """Thin orchestration entry point for the existing canonical Comparative Valuation
    authority (`CanonicalComparativeValuationService`, ADR 0026), plus a read-only status
    query over its five record families.

    This module performs no validation, formula, replay, or persistence logic of its
    own. Every check -- strict-known selection, peer eligibility, missingness, conflict
    rejection, coverage gates, correction-lineage integrity, repository bypass rejection
    -- is enforced exclusively by the existing, unmodified service, exactly as
    `hunter.valuation_authority.command` already does for `hunter.valuation`/
    `hunter.valuation_methodology`. No caller-supplied value can select "latest"/
    "current" state: every status lookup this module triggers is bounded by the exact
    `effective_as_of`/`known_by` coordinates the manifest declares, resolved
    deterministically by the underlying service's strict-known logic.

    Not dispatched from `hunter.__main__`; not reachable through the `hunter` CLI.
    ADR 0026 Implementation Prerequisite 9 requires a "disabled-entry-point plan" and
    Prerequisite 10 requires independent implementation review and post-merge audit
    "before any production activation" -- neither has occurred yet. This module is
    exercised only by direct construction in
    `tests/test_comparative_valuation_authority_v1.py`, mirroring the identical
    precedent already established for `hunter.evidence_assembly`.
    """
    if len(argv) != 2 or argv[0] != "run":
        print("usage: hunter comparative-valuation-authority run MANIFEST.json")
        return 1
    manifest_path = Path(argv[1]).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("comparative-valuation-authority manifest must be a JSON object")
    application_root = _application_root()
    operation = manifest.get("operation")
    if operation == "peer_policy":
        output = _persist_peer_policy(manifest, application_root)
    elif operation == "peer_universe":
        output = _persist_peer_universe(manifest, application_root)
    elif operation == "eligibility_decision":
        output = _persist_eligibility_decision(manifest, application_root)
    elif operation == "metric_observation":
        output = _persist_metric_observation(manifest, application_root)
    elif operation == "assess":
        output = _assess(manifest, application_root)
    elif operation == "status":
        output = _status(manifest, application_root)
    else:
        raise ValueError(f"comparative-valuation-authority manifest requires operation: {' or '.join(_OPERATIONS)}")
    print(json.dumps(output, sort_keys=True))
    return 0


def _service(application_root: Path) -> tuple[CanonicalComparativeValuationService, Path]:
    persistence_path = _canonical_path(application_root, _CANONICAL_PERSISTENCE_DATABASE)
    service = CanonicalComparativeValuationService(
        repository=ComparativeValuationRepository(persistence_path),
        value_capture_repository=SupplyAndValueCaptureRepository(persistence_path),
        market_fact_repository=ObservedMarketFactRepository(persistence_path),
        application_root=application_root,
    )
    return service, persistence_path


def _persist_peer_policy(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    service, persistence_path = _service(application_root)
    payload = manifest.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("comparative-valuation-authority peer_policy manifest requires a payload object")
    record = service.persist_peer_policy(
        policy_id=str(payload["policy_id"]),
        supported_entity_class=str(payload["supported_entity_class"]),
        entity_class_criteria_id=str(payload["entity_class_criteria_id"]),
        entity_class_criteria_version=str(payload["entity_class_criteria_version"]),
        taxonomy=str(payload["taxonomy"]),
        taxonomy_version=str(payload["taxonomy_version"]),
        required_lifecycle_state=str(payload["required_lifecycle_state"]),
        required_sector_classification=str(payload["required_sector_classification"]),
        required_value_capture_mechanism_types=_string_tuple(payload["required_value_capture_mechanism_types"]),
        required_revenue_accounting_meaning=str(payload["required_revenue_accounting_meaning"]),
        required_native_evidence_types=_string_tuple(payload["required_native_evidence_types"]),
        required_quote_currency=str(payload["required_quote_currency"]),
        freshness_limit_days=int(payload["freshness_limit_days"]),
        observation_window_days=int(payload["observation_window_days"]),
        maximum_candidate_universe_size=int(payload["maximum_candidate_universe_size"]),
        effective_at=_datetime(payload["effective_at"]),
        recorded_at=_datetime(payload["recorded_at"]),
        known_at=_datetime(payload["known_at"]),
        supersedes_record_id=_optional_text(payload.get("supersedes_record_id")),
        correction_reason=str(payload.get("correction_reason", "")),
    )
    return {
        "operation": "peer_policy",
        "persistence_database": str(persistence_path),
        "record_id": record.record_id,
        "logical_id": record.logical_id,
        "quality_state": record.quality_state,
        "conflict_state": record.conflict_state,
    }


def _persist_peer_universe(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    service, persistence_path = _service(application_root)
    candidates_payload = manifest.get("candidates")
    if not isinstance(candidates_payload, list):
        raise ValueError("comparative-valuation-authority peer_universe manifest requires a candidates list")
    record = service.persist_peer_universe(
        identity=_identity(manifest.get("identity")),
        policy_record_id=str(manifest["policy_record_id"]),
        cutoff=_datetime(manifest["cutoff"]),
        candidates=tuple(_candidate(item) for item in candidates_payload),
        recorded_at=_datetime(manifest["recorded_at"]),
        known_at=_datetime(manifest["known_at"]),
        supersedes_record_id=_optional_text(manifest.get("supersedes_record_id")),
        correction_reason=str(manifest.get("correction_reason", "")),
    )
    return {
        "operation": "peer_universe",
        "persistence_database": str(persistence_path),
        "record_id": record.record_id,
        "logical_id": record.logical_id,
        "candidate_count": len(record.candidates),
        "quality_state": record.quality_state,
        "conflict_state": record.conflict_state,
    }


def _persist_eligibility_decision(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    service, persistence_path = _service(application_root)
    record = service.persist_eligibility_decision(
        target_identity=_identity(manifest.get("target_identity")),
        candidate_identity=_identity(manifest.get("candidate_identity")),
        policy_record_id=str(manifest["policy_record_id"]),
        universe_snapshot_id=str(manifest["universe_snapshot_id"]),
        effective_at=_datetime(manifest["effective_at"]),
        recorded_at=_datetime(manifest["recorded_at"]),
        known_at=_datetime(manifest["known_at"]),
        supersedes_record_id=_optional_text(manifest.get("supersedes_record_id")),
        correction_reason=str(manifest.get("correction_reason", "")),
    )
    return {
        "operation": "eligibility_decision",
        "persistence_database": str(persistence_path),
        "record_id": record.record_id,
        "logical_id": record.logical_id,
        "decision": record.decision,
        "quality_state": record.quality_state,
        "conflict_state": record.conflict_state,
    }


def _persist_metric_observation(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    service, persistence_path = _service(application_root)
    record = service.persist_metric_observation(
        identity=_identity(manifest.get("identity")),
        policy_record_id=str(manifest["policy_record_id"]),
        universe_snapshot_id=str(manifest["universe_snapshot_id"]),
        evidence_type=str(manifest["evidence_type"]),
        rule_type=str(manifest["rule_type"]),
        effective_at=_datetime(manifest["effective_at"]),
        recorded_at=_datetime(manifest["recorded_at"]),
        known_at=_datetime(manifest["known_at"]),
        supersedes_record_id=_optional_text(manifest.get("supersedes_record_id")),
        correction_reason=str(manifest.get("correction_reason", "")),
    )
    return {
        "operation": "metric_observation",
        "persistence_database": str(persistence_path),
        "record_id": record.record_id,
        "logical_id": record.logical_id,
        "comparative_multiple": record.comparative_multiple,
        "availability_state": record.availability_state,
        "normalization_status": record.normalization_status,
        "quality_state": record.quality_state,
    }


def _assess(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    service, persistence_path = _service(application_root)
    record = service.assess(
        identity=_identity(manifest.get("identity")),
        policy_record_id=str(manifest["policy_record_id"]),
        universe_snapshot_id=str(manifest["universe_snapshot_id"]),
        evidence_type=str(manifest["evidence_type"]),
        rule_type=str(manifest["rule_type"]),
        effective_at=_datetime(manifest["effective_at"]),
        recorded_at=_datetime(manifest["recorded_at"]),
        known_at=_datetime(manifest["known_at"]),
        supersedes_record_id=_optional_text(manifest.get("supersedes_record_id")),
        correction_reason=str(manifest.get("correction_reason", "")),
    )
    return {
        "operation": "assess",
        "persistence_database": str(persistence_path),
        "record_id": record.record_id,
        "logical_id": record.logical_id,
        "availability_state": record.availability_state,
        "normalization_status": record.normalization_status,
        "raw_log_residual": record.raw_log_residual,
        "peer_median_multiple": record.peer_median_multiple,
        "target_multiple": record.target_multiple,
        "confidence": record.confidence,
        "quality_state": record.quality_state,
    }


def _status(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    service, persistence_path = _service(application_root)
    target = manifest.get("target")
    if target not in _STATUS_TARGETS:
        raise ValueError(
            f"comparative-valuation-authority status manifest requires target: {' or '.join(_STATUS_TARGETS)}"
        )
    effective_as_of = _datetime(manifest["effective_as_of"])
    known_by = _datetime(manifest["known_by"])
    logical_id = str(manifest["logical_id"])
    if target == "peer_policy":
        record: Any = service.strict_known_policy(
            effective_as_of=effective_as_of, known_by=known_by, logical_id=logical_id
        )
    elif target == "peer_universe":
        record = service.strict_known_universe(
            effective_as_of=effective_as_of, known_by=known_by, logical_id=logical_id
        )
    elif target == "eligibility_decision":
        record = service.strict_known_decision(
            effective_as_of=effective_as_of, known_by=known_by, logical_id=logical_id
        )
    elif target == "metric_observation":
        record = service.strict_known_observation(
            effective_as_of=effective_as_of, known_by=known_by, logical_id=logical_id
        )
    else:
        record = service.strict_known_assessment(
            effective_as_of=effective_as_of, known_by=known_by, logical_id=logical_id
        )
    if record is None:
        return {
            "operation": "status",
            "target": target,
            "persistence_database": str(persistence_path),
            "available": False,
        }
    return {
        "operation": "status",
        "target": target,
        "persistence_database": str(persistence_path),
        "available": True,
        "record": _json_safe(asdict(record)),
    }


def _candidate(payload: Any) -> PeerCandidate:
    if not isinstance(payload, dict):
        raise ValueError("comparative-valuation-authority candidate entries must be objects")
    return PeerCandidate(
        identity=_identity(payload.get("identity")),
        source_record_id=str(payload["source_record_id"]),
        source_record_version=str(payload["source_record_version"]),
        lifecycle_state=str(payload["lifecycle_state"]),
        sector_classification=str(payload["sector_classification"]),
        value_capture_mechanism_type=str(payload["value_capture_mechanism_type"]),
        revenue_accounting_meaning=str(payload["revenue_accounting_meaning"]),
        native_evidence_type=str(payload["native_evidence_type"]),
        quote_currency=str(payload["quote_currency"]),
        supply_basis=str(payload["supply_basis"]),
    )


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("comparative-valuation-authority manifest requires a non-empty list of strings")
    return tuple(str(item) for item in value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _identity(payload: Any) -> EconomicClaimIdentity:
    if not isinstance(payload, dict):
        raise ValueError("comparative-valuation-authority manifest requires an identity object")
    return EconomicClaimIdentity(
        entity_id=str(payload["entity_id"]),
        economic_claim_id=str(payload["economic_claim_id"]),
        asset_id=str(payload["asset_id"]),
        representation_id=str(payload["representation_id"]),
        token_id=str(payload["token_id"]),
        chain=str(payload.get("chain", "")),
        contract_address=str(payload.get("contract_address", "")),
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _application_root() -> Path:
    configured = os.environ.get(_APPLICATION_ROOT_ENV, "").strip()
    if not configured:
        raise ValueError(f"{_APPLICATION_ROOT_ENV} must identify the approved Hunter application root")
    root = Path(configured).expanduser()
    if not root.is_absolute():
        raise ValueError(f"{_APPLICATION_ROOT_ENV} must be an absolute path")
    return root.resolve()


def _canonical_path(root: Path, relative: Path) -> Path:
    candidate = (root / relative).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("canonical comparative-valuation-authority runtime path escaped application root")
    return candidate


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("comparative-valuation-authority manifest timestamps must be timezone-aware ISO-8601 values")
    return parsed
