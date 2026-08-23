from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from hunter.evidence_assembly.composition import build_production_evidence_assembly_service
from hunter.evidence_assembly.models import AccountingMeaning, AssemblyConstituent
from hunter.evidence_assembly.service import CanonicalEvidenceAssemblyService
from hunter.value_capture.repository import SupplyAndValueCaptureRepository

_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"
_CANONICAL_PERSISTENCE_DATABASE = Path("data/data_ops.sqlite")
_OPERATIONS = ("assemble", "status")


@dataclass(frozen=True)
class _ConstituentSpec:
    record_id: str
    shape_id: str
    currency: str
    raw_unit: str
    accounting_meaning: AccountingMeaning
    supply_basis_id: str
    pathway_id: str
    representation_continuity_proof_id: str
    pathway_continuity_proof_id: str
    supply_basis_continuity_proof_id: str


def main(argv: list[str]) -> int:
    """Undispatched thin orchestration over CanonicalEvidenceAssemblyService.

    The module is intentionally not registered in ``hunter.__main__``. It exposes
    only deterministic manifest mapping to the already-authorized production
    composition root and its existing ``assemble`` / ``strict_known`` APIs.
    """
    if len(argv) != 2 or argv[0] != "run":
        print(
            "usage: python -c 'from hunter.evidence_assembly.command import main; "
            'raise SystemExit(main(["run", "MANIFEST.json"]))\''
        )
        return 1

    manifest_path = Path(argv[1]).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("evidence-assembly manifest must be a JSON object")

    application_root = _application_root()
    operation = manifest.get("operation")
    if operation == "assemble":
        output = _assemble(manifest, application_root)
    elif operation == "status":
        output = _status(manifest, application_root)
    else:
        raise ValueError(f"evidence-assembly manifest requires operation: {' or '.join(_OPERATIONS)}")

    print(json.dumps(output, sort_keys=True))
    return 0


def _assemble(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    # Parse and validate every caller-supplied field before constructing any
    # repository. The manifest supplies references and declared metadata only;
    # canonical native evidence is always re-hydrated from production persistence.
    raw_constituents = _required_value(manifest, "constituents")
    if not isinstance(raw_constituents, list) or len(raw_constituents) < 2:
        raise ValueError("evidence-assembly assemble manifest requires at least two constituents")
    constituent_specs = tuple(_constituent_spec(value, index=index) for index, value in enumerate(raw_constituents))

    accounting_window_start = _datetime(_required_value(manifest, "accounting_window_start"), "accounting_window_start")
    accounting_window_end = _datetime(_required_value(manifest, "accounting_window_end"), "accounting_window_end")
    recorded_at = _datetime(_required_value(manifest, "recorded_at"), "recorded_at")
    replay_cutoff = _datetime(_required_value(manifest, "replay_cutoff"), "replay_cutoff")
    methodology_contract_id = _required_text(
        _required_value(manifest, "methodology_contract_id"), "methodology_contract_id"
    )
    methodology_contract_version = _required_text(
        _required_value(manifest, "methodology_contract_version"), "methodology_contract_version"
    )
    evidence_shape_registry_version = _required_text(
        _required_value(manifest, "evidence_shape_registry_version"), "evidence_shape_registry_version"
    )
    semantic_version = _defaulted_text(manifest, "semantic_version", default="1.0.0")
    supersedes_record_id = _optional_text(manifest, "supersedes_record_id")
    correction_reason = _defaulted_text(manifest, "correction_reason", default="", allow_blank=True)

    service, persistence_path = _service(application_root)
    native_repository = SupplyAndValueCaptureRepository(persistence_path)
    constituents = tuple(_hydrate_constituent(native_repository, spec) for spec in constituent_specs)

    record = service.assemble(
        constituents=constituents,
        accounting_window_start=accounting_window_start,
        accounting_window_end=accounting_window_end,
        recorded_at=recorded_at,
        replay_cutoff=replay_cutoff,
        methodology_contract_id=methodology_contract_id,
        methodology_contract_version=methodology_contract_version,
        evidence_shape_registry_version=evidence_shape_registry_version,
        semantic_version=semantic_version,
        supersedes_record_id=supersedes_record_id,
        correction_reason=correction_reason,
    )
    return {
        "operation": "assemble",
        "persistence_database": str(persistence_path),
        "record_id": record.record_id,
        "logical_id": record.logical_id,
        "content_hash": record.content_hash,
        "assembly_content_hash": record.assembly_content_hash,
        "quality_state": record.quality_state,
        "conflict_state": record.conflict_state,
    }


def _status(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    # Validate the complete read manifest before the unavailable short-circuit so
    # malformed input can never masquerade as truthful missingness.
    logical_id = _required_text(_required_value(manifest, "logical_id"), "logical_id")
    effective_as_of = _datetime(_required_value(manifest, "effective_as_of"), "effective_as_of")
    known_by = _datetime(_required_value(manifest, "known_by"), "known_by")
    persistence_path = _canonical_path(application_root, _CANONICAL_PERSISTENCE_DATABASE)

    if not persistence_path.exists():
        return {
            "operation": "status",
            "persistence_database": str(persistence_path),
            "available": False,
        }

    service, persistence_path = _service(application_root)
    record = service.strict_known(logical_id=logical_id, effective_as_of=effective_as_of, known_by=known_by)
    if record is None:
        return {
            "operation": "status",
            "persistence_database": str(persistence_path),
            "available": False,
        }
    return {
        "operation": "status",
        "persistence_database": str(persistence_path),
        "available": True,
        "record": _json_safe(asdict(record)),
    }


def _service(application_root: Path) -> tuple[CanonicalEvidenceAssemblyService, Path]:
    persistence_path = _canonical_path(application_root, _CANONICAL_PERSISTENCE_DATABASE)
    service = build_production_evidence_assembly_service(
        db_path=persistence_path,
        application_root=application_root,
    )
    return service, persistence_path


def _constituent_spec(value: Any, *, index: int) -> _ConstituentSpec:
    field_prefix = f"constituents[{index}]"
    if not isinstance(value, dict):
        raise ValueError(f"evidence-assembly manifest field {field_prefix!r} must be an object")
    return _ConstituentSpec(
        record_id=_required_text(_required_value(value, "record_id", prefix=field_prefix), f"{field_prefix}.record_id"),
        shape_id=_required_text(_required_value(value, "shape_id", prefix=field_prefix), f"{field_prefix}.shape_id"),
        currency=_required_text(_required_value(value, "currency", prefix=field_prefix), f"{field_prefix}.currency"),
        raw_unit=_required_text(_required_value(value, "raw_unit", prefix=field_prefix), f"{field_prefix}.raw_unit"),
        accounting_meaning=_accounting_meaning(
            _required_value(value, "accounting_meaning", prefix=field_prefix), f"{field_prefix}.accounting_meaning"
        ),
        supply_basis_id=_required_text(
            _required_value(value, "supply_basis_id", prefix=field_prefix), f"{field_prefix}.supply_basis_id"
        ),
        pathway_id=_required_text(
            _required_value(value, "pathway_id", prefix=field_prefix), f"{field_prefix}.pathway_id"
        ),
        representation_continuity_proof_id=_optional_string_field(
            value, "representation_continuity_proof_id", prefix=field_prefix
        ),
        pathway_continuity_proof_id=_optional_string_field(value, "pathway_continuity_proof_id", prefix=field_prefix),
        supply_basis_continuity_proof_id=_optional_string_field(
            value, "supply_basis_continuity_proof_id", prefix=field_prefix
        ),
    )


def _hydrate_constituent(repository: SupplyAndValueCaptureRepository, spec: _ConstituentSpec) -> AssemblyConstituent:
    record = repository.evidence(spec.record_id)
    if record is None:
        raise ValueError(f"evidence-assembly constituent record_id is not canonical native evidence: {spec.record_id}")
    return AssemblyConstituent(
        record=record,
        shape_id=spec.shape_id,
        currency=spec.currency,
        raw_unit=spec.raw_unit,
        accounting_meaning=spec.accounting_meaning,
        supply_basis_id=spec.supply_basis_id,
        pathway_id=spec.pathway_id,
        representation_continuity_proof_id=spec.representation_continuity_proof_id,
        pathway_continuity_proof_id=spec.pathway_continuity_proof_id,
        supply_basis_continuity_proof_id=spec.supply_basis_continuity_proof_id,
    )


def _required_value(payload: dict[str, Any], field: str, *, prefix: str = "") -> Any:
    if field not in payload:
        qualified = f"{prefix}.{field}" if prefix else field
        raise ValueError(f"evidence-assembly manifest requires field {qualified!r}")
    return payload[field]


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"evidence-assembly manifest field {field!r} must be a non-blank string")
    return value


def _defaulted_text(payload: dict[str, Any], field: str, *, default: str, allow_blank: bool = False) -> str:
    if field not in payload:
        return default
    value = payload[field]
    if not isinstance(value, str) or (not allow_blank and not value.strip()):
        qualifier = "a string" if allow_blank else "a non-blank string"
        raise ValueError(f"evidence-assembly manifest field {field!r} must be {qualifier}")
    return value


def _optional_text(payload: dict[str, Any], field: str) -> str | None:
    if field not in payload or payload[field] is None:
        return None
    value = payload[field]
    if not isinstance(value, str):
        raise ValueError(f"evidence-assembly manifest field {field!r} must be a string or null")
    return value if value.strip() else None


def _optional_string_field(payload: dict[str, Any], field: str, *, prefix: str) -> str:
    if field not in payload:
        return ""
    value = payload[field]
    if not isinstance(value, str):
        raise ValueError(f"evidence-assembly manifest field {prefix}.{field!r} must be a string when provided")
    return value


def _accounting_meaning(value: Any, field: str) -> AccountingMeaning:
    if value not in {"period_specific", "cumulative", "event"}:
        raise ValueError(
            f"evidence-assembly manifest field {field!r} must be period_specific, cumulative, or event"
        )
    return cast(AccountingMeaning, value)


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
        raise ValueError("canonical evidence-assembly runtime path escaped application root")
    return candidate


def _datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value)
    else:
        raise ValueError(f"evidence-assembly manifest field {field!r} must be a timezone-aware ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"evidence-assembly manifest field {field!r} must be timezone-aware")
    return parsed.astimezone(UTC)


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
