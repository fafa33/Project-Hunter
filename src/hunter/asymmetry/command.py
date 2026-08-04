from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.asymmetry.repository import AsymmetryRepository
from hunter.asymmetry.service import CanonicalAsymmetryService
from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.value_capture.models import EconomicClaimIdentity

_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"
_CANONICAL_PERSISTENCE_DATABASE = Path("data/data_ops.sqlite")
_OPERATIONS = (
    "methodology",
    "scenario_set",
    "scenario_probability",
    "scenario_payoff",
    "assess",
    "status",
)
_STATUS_TARGETS = (
    "methodology",
    "scenario_set",
    "scenario_probability",
    "scenario_payoff",
    "assessment",
)


def main(argv: list[str]) -> int:
    """Thin orchestration entry point for the existing canonical Asymmetry authority
    (`CanonicalAsymmetryService`, ADR 0021), plus a read-only status query over its
    five record families.

    This module performs no validation, formula, replay, or persistence logic of its
    own. Every check -- strict-known selection, scenario/probability/payoff
    compatibility, missingness, conflict rejection, correction-lineage integrity,
    repository bypass rejection -- is enforced exclusively by the existing,
    unmodified service, exactly as `hunter.valuation_authority.command`,
    `hunter.comparative_valuation.command` (Issue #181), and
    `hunter.mispricing.command` (Issue #183) already do for their own authorities.
    No caller-supplied value can select "latest"/"current" state: every status
    lookup this module triggers is bounded by the exact `effective_as_of`/`known_by`
    coordinates the manifest declares, resolved deterministically by the underlying
    service's strict-known logic.

    Not dispatched from `hunter.__main__`; not reachable through the `hunter` CLI.
    Dispatching this module would create a new cross-component/production boundary
    under `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`'s Scope section and
    would require independent Architecture Review before activation -- a separate,
    future, independently-reviewed change, not part of this issue. This module is
    exercised only by direct construction in `tests/test_asymmetry_authority_v1.py`.
    """
    if len(argv) != 2 or argv[0] != "run":
        print(
            "usage: python -c 'from hunter.asymmetry.command import main; "
            'raise SystemExit(main(["run", "MANIFEST.json"]))\''
        )
        return 1
    manifest_path = Path(argv[1]).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("asymmetry-authority manifest must be a JSON object")
    application_root = _application_root()
    operation = manifest.get("operation")
    if operation == "methodology":
        output = _persist_methodology(manifest, application_root)
    elif operation == "scenario_set":
        output = _persist_scenario_set(manifest, application_root)
    elif operation == "scenario_probability":
        output = _persist_scenario_probability(manifest, application_root)
    elif operation == "scenario_payoff":
        output = _persist_scenario_payoff(manifest, application_root)
    elif operation == "assess":
        output = _assess(manifest, application_root)
    elif operation == "status":
        output = _status(manifest, application_root)
    else:
        raise ValueError(f"asymmetry-authority manifest requires operation: {' or '.join(_OPERATIONS)}")
    print(json.dumps(output, sort_keys=True))
    return 0


def _service(application_root: Path) -> tuple[CanonicalAsymmetryService, Path]:
    persistence_path = _canonical_path(application_root, _CANONICAL_PERSISTENCE_DATABASE)
    service = CanonicalAsymmetryService(
        repository=AsymmetryRepository(persistence_path),
        market_fact_repository=ObservedMarketFactRepository(persistence_path),
        application_root=application_root,
    )
    return service, persistence_path


def _persist_methodology(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    service, persistence_path = _service(application_root)
    payload = manifest.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("asymmetry-authority methodology manifest requires a payload object")
    record = service.persist_asymmetry_methodology(
        methodology_id=str(payload["methodology_id"]),
        required_quote_currency=str(payload["required_quote_currency"]),
        entity_class_criteria_id=str(payload["entity_class_criteria_id"]),
        entity_class_criteria_version=str(payload["entity_class_criteria_version"]),
        effective_at=_datetime(payload["effective_at"]),
        recorded_at=_datetime(payload["recorded_at"]),
        known_at=_datetime(payload["known_at"]),
        formula_version=str(payload.get("formula_version", "asymmetry-raw-ratio-v1")),
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


def _persist_scenario_set(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    service, persistence_path = _service(application_root)
    record = service.persist_scenario_set(
        identity=_identity(manifest.get("identity")),
        methodology_record_id=str(manifest["methodology_record_id"]),
        scenario_set_id=str(manifest["scenario_set_id"]),
        scenario_ids=_string_tuple(manifest["scenario_ids"]),
        scenario_types=_string_tuple(manifest["scenario_types"]),
        baseline_market_fact_record_id=str(manifest["baseline_market_fact_record_id"]),
        quote_currency=str(manifest["quote_currency"]),
        material_omitted_scenario_policy=str(manifest["material_omitted_scenario_policy"]),
        uncertainty=str(manifest["uncertainty"]),
        evidence_record_ids=_string_tuple(manifest["evidence_record_ids"]),
        source_versions=_string_tuple(manifest["source_versions"]),
        dependency_pairs=_pair_tuple(manifest.get("dependency_pairs", [])),
        effective_at=_datetime(manifest["effective_at"]),
        recorded_at=_datetime(manifest["recorded_at"]),
        known_at=_datetime(manifest["known_at"]),
        supersedes_record_id=_optional_text(manifest.get("supersedes_record_id")),
        correction_reason=str(manifest.get("correction_reason", "")),
    )
    return {
        "operation": "scenario_set",
        "persistence_database": str(persistence_path),
        "record_id": record.record_id,
        "logical_id": record.logical_id,
        "scenario_count": len(record.scenario_ids),
        "quality_state": record.quality_state,
        "conflict_state": record.conflict_state,
    }


def _persist_scenario_probability(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    service, persistence_path = _service(application_root)
    record = service.persist_scenario_probability(
        scenario_set_record_id=str(manifest["scenario_set_record_id"]),
        scenario_id=str(manifest["scenario_id"]),
        probability=str(manifest["probability"]),
        probability_uncertainty=str(manifest["probability_uncertainty"]),
        probability_authority=str(manifest["probability_authority"]),
        probability_method=str(manifest["probability_method"]),
        effective_at=_datetime(manifest["effective_at"]),
        recorded_at=_datetime(manifest["recorded_at"]),
        known_at=_datetime(manifest["known_at"]),
        supersedes_record_id=_optional_text(manifest.get("supersedes_record_id")),
        correction_reason=str(manifest.get("correction_reason", "")),
    )
    return {
        "operation": "scenario_probability",
        "persistence_database": str(persistence_path),
        "record_id": record.record_id,
        "logical_id": record.logical_id,
        "scenario_id": record.scenario_id,
        "quality_state": record.quality_state,
        "conflict_state": record.conflict_state,
    }


def _persist_scenario_payoff(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    service, persistence_path = _service(application_root)
    record = service.persist_scenario_payoff(
        scenario_set_record_id=str(manifest["scenario_set_record_id"]),
        scenario_id=str(manifest["scenario_id"]),
        terminal_payoff=str(manifest["terminal_payoff"]),
        payoff_uncertainty=str(manifest["payoff_uncertainty"]),
        evidence_record_ids=_string_tuple(manifest["evidence_record_ids"]),
        source_record_id=str(manifest["source_record_id"]),
        source_record_version=str(manifest["source_record_version"]),
        effective_at=_datetime(manifest["effective_at"]),
        recorded_at=_datetime(manifest["recorded_at"]),
        known_at=_datetime(manifest["known_at"]),
        supersedes_record_id=_optional_text(manifest.get("supersedes_record_id")),
        correction_reason=str(manifest.get("correction_reason", "")),
    )
    return {
        "operation": "scenario_payoff",
        "persistence_database": str(persistence_path),
        "record_id": record.record_id,
        "logical_id": record.logical_id,
        "scenario_id": record.scenario_id,
        "payoff_class": record.payoff_class,
        "quality_state": record.quality_state,
        "conflict_state": record.conflict_state,
    }


def _assess(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    service, persistence_path = _service(application_root)
    record = service.assess(
        identity=_identity(manifest.get("identity")),
        methodology_record_id=str(manifest["methodology_record_id"]),
        scenario_set_logical_id=str(manifest["scenario_set_logical_id"]),
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
        "raw_asymmetry_ratio": record.raw_asymmetry_ratio,
        "confidence": record.confidence,
        "quality_state": record.quality_state,
    }


def _status(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    service, persistence_path = _service(application_root)
    target = manifest.get("target")
    if target not in _STATUS_TARGETS:
        raise ValueError(f"asymmetry-authority status manifest requires target: {' or '.join(_STATUS_TARGETS)}")
    effective_as_of = _datetime(manifest["effective_as_of"])
    known_by = _datetime(manifest["known_by"])
    logical_id = str(manifest["logical_id"])
    if target == "methodology":
        record: Any = service.strict_known_methodology(
            effective_as_of=effective_as_of, known_by=known_by, logical_id=logical_id
        )
    elif target == "scenario_set":
        record = service.strict_known_scenario_set(
            effective_as_of=effective_as_of, known_by=known_by, logical_id=logical_id
        )
    elif target == "scenario_probability":
        record = service.strict_known_scenario_probability(
            effective_as_of=effective_as_of, known_by=known_by, logical_id=logical_id
        )
    elif target == "scenario_payoff":
        record = service.strict_known_scenario_payoff(
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


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("asymmetry-authority manifest requires a non-empty list of strings")
    return tuple(str(item) for item in value)


def _pair_tuple(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise ValueError("asymmetry-authority manifest requires dependency_pairs to be a list")
    pairs: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("asymmetry-authority manifest dependency_pairs entries must be two-element lists")
        pairs.append((str(item[0]), str(item[1])))
    return tuple(pairs)


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
        raise ValueError("asymmetry-authority manifest requires an identity object")
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
        raise ValueError("canonical asymmetry-authority runtime path escaped application root")
    return candidate


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("asymmetry-authority manifest timestamps must be timezone-aware ISO-8601 values")
    return parsed
