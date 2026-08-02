from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.valuation.repository import CanonicalValuationRepository
from hunter.valuation.service import CanonicalValuationService
from hunter.valuation_methodology.repository import ValuationMethodologyRepository
from hunter.valuation_methodology.service import CanonicalValuationMethodologyAuthority
from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.repository import SupplyAndValueCaptureRepository

_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"
_CANONICAL_PERSISTENCE_DATABASE = Path("data/data_ops.sqlite")
_OPERATIONS = ("methodology", "estimate", "status")
_STATUS_TARGETS = ("methodology", "fair_value_estimate", "valuation_assessment")


def main(argv: list[str]) -> int:
    """Thin orchestration entry point for the two existing canonical valuation
    authorities (`CanonicalValuationMethodologyAuthority`, `CanonicalValuationService`),
    plus a read-only status query over their two repositories.

    This module performs no validation, formula, replay, or persistence logic of its
    own. Every check -- strict-known selection, missingness, conflict rejection,
    horizon/accounting-period compatibility, correction-lineage integrity, repository
    bypass rejection -- is enforced exclusively by the two existing, unmodified
    services this orchestrates, exactly as `hunter.valuation_evidence.command` already
    does for `hunter.value_capture`/`hunter.market_facts`. No caller-supplied value can
    select "latest"/"current" state: every lookup this module triggers is bounded by the
    exact `effective_at`/`recorded_at`/`known_at` coordinates the manifest declares,
    resolved deterministically by the underlying services' strict-known logic. The
    `status` operation adds no new authority: it calls exactly
    the same `strict_known_methodology`/`strict_known_fair_value_estimate`/
    `strict_known_valuation_assessment` service read methods already exercised by
    the existing test suite, making them independently invocable by an operator or
    auditor without ad hoc scripting, mirroring the read-only projection role ADR 0016
    assigns to Dashboard API.
    """
    if len(argv) != 2 or argv[0] != "run":
        print("usage: hunter valuation-authority run MANIFEST.json")
        return 1
    manifest_path = Path(argv[1]).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("valuation-authority manifest must be a JSON object")
    application_root = _application_root()
    operation = manifest.get("operation")
    if operation == "methodology":
        output = _persist_methodology(manifest, application_root)
    elif operation == "estimate":
        output = _persist_estimate(manifest, application_root)
    elif operation == "status":
        output = _status(manifest, application_root)
    else:
        raise ValueError(f"valuation-authority manifest requires operation: {' or '.join(_OPERATIONS)}")
    print(json.dumps(output, sort_keys=True))
    return 0


def _persist_methodology(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    persistence_path = _canonical_path(application_root, _CANONICAL_PERSISTENCE_DATABASE)
    repository = ValuationMethodologyRepository(persistence_path)
    authority = CanonicalValuationMethodologyAuthority(repository=repository, application_root=application_root)
    payload = manifest.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("valuation-authority methodology manifest requires a payload object")
    record = authority.persist_methodology(
        entity_class_criteria_id=str(payload["entity_class_criteria_id"]),
        entity_class_criteria_version=str(payload["entity_class_criteria_version"]),
        currency=str(payload["currency"]),
        discount_rate_policy_id=str(payload["discount_rate_policy_id"]),
        discount_rate_policy_version=str(payload["discount_rate_policy_version"]),
        sensitivity_policy_id=str(payload["sensitivity_policy_id"]),
        sensitivity_policy_version=str(payload["sensitivity_policy_version"]),
        supply_basis_selection_rule=str(payload["supply_basis_selection_rule"]),
        effective_at=_datetime(payload["effective_at"]),
        recorded_at=_datetime(payload["recorded_at"]),
        known_at=_datetime(payload["known_at"]),
        supersedes_record_id=_optional_text(payload.get("supersedes_record_id")),
        correction_reason=str(payload.get("correction_reason", "")),
    )
    return {
        "operation": "methodology",
        "persistence_database": str(persistence_path),
        "record_id": record.record_id,
        "logical_id": record.logical_id,
        "quality_state": record.quality_state,
        "conflict_state": record.conflict_state,
    }


def _persist_estimate(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    persistence_path = _canonical_path(application_root, _CANONICAL_PERSISTENCE_DATABASE)
    value_capture_repository = SupplyAndValueCaptureRepository(persistence_path)
    methodology_repository = ValuationMethodologyRepository(persistence_path)
    methodology_authority = CanonicalValuationMethodologyAuthority(
        repository=methodology_repository, application_root=application_root
    )
    service = CanonicalValuationService(
        repository=CanonicalValuationRepository(persistence_path),
        methodology_authority=methodology_authority,
        value_capture_repository=value_capture_repository,
        market_fact_repository=ObservedMarketFactRepository(persistence_path),
        application_root=application_root,
    )
    identity = _identity(manifest.get("identity"))
    estimate, assessment = service.estimate_fair_value(
        identity=identity,
        evidence_type=str(manifest["evidence_type"]),
        rule_type=str(manifest["rule_type"]),
        effective_at=_datetime(manifest["effective_at"]),
        recorded_at=_datetime(manifest["recorded_at"]),
        known_at=_datetime(manifest["known_at"]),
        supersedes_estimate_record_id=_optional_text(manifest.get("supersedes_estimate_record_id")),
        supersedes_assessment_record_id=_optional_text(manifest.get("supersedes_assessment_record_id")),
        correction_reason=str(manifest.get("correction_reason", "")),
    )
    return {
        "operation": "estimate",
        "persistence_database": str(persistence_path),
        "fair_value_estimate_record_id": estimate.record_id,
        "fair_value_estimate_logical_id": estimate.logical_id,
        "fair_value_estimate_quality_state": estimate.quality_state,
        "valuation_assessment_record_id": assessment.record_id,
        "valuation_assessment_logical_id": assessment.logical_id,
        "valuation_assessment_quality_state": assessment.quality_state,
    }


def _status(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    persistence_path = _canonical_path(application_root, _CANONICAL_PERSISTENCE_DATABASE)
    target = manifest.get("target")
    if target not in _STATUS_TARGETS:
        raise ValueError(f"valuation-authority status manifest requires target: {' or '.join(_STATUS_TARGETS)}")
    effective_as_of = _datetime(manifest["effective_as_of"])
    known_by = _datetime(manifest["known_by"])
    if target == "methodology":
        methodology_repository = ValuationMethodologyRepository(persistence_path)
        record: Any = CanonicalValuationMethodologyAuthority(
            repository=methodology_repository, application_root=application_root
        ).strict_known_methodology(effective_as_of=effective_as_of, known_by=known_by)
    else:
        logical_id = str(manifest["logical_id"])
        valuation_repository = CanonicalValuationRepository(persistence_path)
        if target == "fair_value_estimate":
            record = CanonicalValuationService.select_strict_known_fair_value_estimate(
                valuation_repository, effective_as_of=effective_as_of, known_by=known_by, logical_id=logical_id
            )
        else:
            record = CanonicalValuationService.select_strict_known_valuation_assessment(
                valuation_repository, effective_as_of=effective_as_of, known_by=known_by, logical_id=logical_id
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
        raise ValueError("valuation-authority estimate manifest requires an identity object")
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
        raise ValueError("canonical valuation-authority runtime path escaped application root")
    return candidate


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("valuation-authority manifest timestamps must be timezone-aware ISO-8601 values")
    return parsed
