from __future__ import annotations

import os
from pathlib import Path

from hunter.evidence_assembly.registry import (
    CanonicalEvidenceShapeRegistryAuthority,
    EvidenceShapeRegistryRepository,
)
from hunter.evidence_assembly.repository import AssembledEvidenceRepository
from hunter.evidence_assembly.semantics import (
    CanonicalEvidenceSemanticsAuthority,
    EvidenceSemanticsRepository,
)
from hunter.evidence_assembly.service import CanonicalEvidenceAssemblyService
from hunter.evidence_semantic_inputs import (
    CanonicalEvidenceSemanticInputAuthority,
    EvidenceSemanticInputRepository,
)
from hunter.valuation_methodology.repository import ValuationMethodologyRepository
from hunter.valuation_methodology.service import CanonicalValuationMethodologyAuthority
from hunter.value_capture.providers import ValueCaptureVerificationKeyRegistry
from hunter.value_capture.registry import ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository
from hunter.value_capture.service import SupplyAndValueCaptureService

_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"
_SIGNING_KEY_ID_ENV = "HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID"
_SIGNING_KEY_ENV = "HUNTER_VALUE_CAPTURE_SIGNING_KEY"
_LEGACY_KEY_ID_ENV = "HUNTER_VALUE_CAPTURE_KEY_ID"
_LEGACY_KEY_SECRET_ENV = "HUNTER_VALUE_CAPTURE_KEY_SECRET"


class ProductionEvidenceAssemblyCompositionError(ValueError):
    """Raised when production composition configuration is missing or invalid."""


def _authorized_application_root(application_root: Path | None) -> Path:
    if application_root is None:
        configured = os.environ.get(_APPLICATION_ROOT_ENV, "").strip()
        if not configured:
            raise ProductionEvidenceAssemblyCompositionError(
                f"{_APPLICATION_ROOT_ENV} must identify the approved Hunter application root"
            )
        application_root = Path(configured).expanduser()
    if not application_root.is_absolute():
        raise ProductionEvidenceAssemblyCompositionError(f"{_APPLICATION_ROOT_ENV} must be an absolute path")
    return application_root.resolve()


def _resolve_verification_keys() -> ValueCaptureVerificationKeyRegistry:
    """Load the Value Capture producer key using its established production contract.

    The canonical acquisition path signs receipts with
    HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID/HUNTER_VALUE_CAPTURE_SIGNING_KEY, where the key is a
    hex-encoded byte string. The older KEY_ID/KEY_SECRET names are retained only as a
    compatibility fallback for callers created during the Issue #197 branch and are interpreted
    as literal secret bytes.
    """
    key_id = os.environ.get(_SIGNING_KEY_ID_ENV, "").strip()
    key_hex = os.environ.get(_SIGNING_KEY_ENV, "").strip()
    if key_id or key_hex:
        if not key_id or not key_hex:
            raise ProductionEvidenceAssemblyCompositionError(
                f"production Value Capture verification keys require {_SIGNING_KEY_ID_ENV} and {_SIGNING_KEY_ENV}"
            )
        try:
            key_bytes = bytes.fromhex(key_hex)
        except ValueError as exc:
            raise ProductionEvidenceAssemblyCompositionError(
                f"{_SIGNING_KEY_ENV} must be a hex-encoded byte string"
            ) from exc
        if len(key_bytes) < 32:
            raise ProductionEvidenceAssemblyCompositionError(f"{_SIGNING_KEY_ENV} must decode to at least 32 bytes")
        return ValueCaptureVerificationKeyRegistry(keys={key_id: key_bytes})

    legacy_key_id = os.environ.get(_LEGACY_KEY_ID_ENV, "").strip()
    legacy_secret = os.environ.get(_LEGACY_KEY_SECRET_ENV, "").strip()
    if not legacy_key_id or not legacy_secret:
        raise ProductionEvidenceAssemblyCompositionError(
            f"production Value Capture verification keys require {_SIGNING_KEY_ID_ENV} and {_SIGNING_KEY_ENV}"
        )
    legacy_key_bytes = legacy_secret.encode("utf-8")
    if len(legacy_key_bytes) < 32:
        raise ProductionEvidenceAssemblyCompositionError(f"{_LEGACY_KEY_SECRET_ENV} must be at least 32 bytes")
    return ValueCaptureVerificationKeyRegistry(keys={legacy_key_id: legacy_key_bytes})


def build_production_evidence_assembly_service(
    *,
    db_path: Path | str | None = None,
    application_root: Path | None = None,
) -> CanonicalEvidenceAssemblyService:
    """Production composition root for CanonicalEvidenceAssemblyService.

    Composes CanonicalEvidenceAssemblyService exclusively using real, production-backed
    collaborators constructed internally. No fakes, stubs, mocks, placeholders, or collaborator
    overrides are accepted or permitted.
    """
    app_root = _authorized_application_root(application_root)

    if db_path is None:
        target_db = app_root / "data" / "data_ops.sqlite"
    else:
        target_db = Path(db_path)
        if not target_db.is_absolute():
            target_db = (app_root / target_db).resolve()

    verification_keys = _resolve_verification_keys()

    assembled_evidence_repository = AssembledEvidenceRepository(target_db)

    value_capture_repo = SupplyAndValueCaptureRepository(target_db)
    value_capture_service = SupplyAndValueCaptureService(
        repository=value_capture_repo,
        registry=ValueCaptureSourceRegistry(sources=()),
        verification_keys=verification_keys,
    )

    methodology_repo = ValuationMethodologyRepository(target_db)
    methodology_authority = CanonicalValuationMethodologyAuthority(
        repository=methodology_repo,
        application_root=app_root,
    )

    registry_repo = EvidenceShapeRegistryRepository(target_db)
    registry_authority = CanonicalEvidenceShapeRegistryAuthority(
        repository=registry_repo,
        application_root=app_root,
    )

    semantic_input_repo = EvidenceSemanticInputRepository(target_db)
    semantic_input_authority = CanonicalEvidenceSemanticInputAuthority(
        repository=semantic_input_repo,
        value_capture_repository=value_capture_repo,
        application_root=app_root,
    )

    semantics_repo = EvidenceSemanticsRepository(target_db)
    semantics_authority = CanonicalEvidenceSemanticsAuthority(
        semantic_input_authority=semantic_input_authority,
        repository=semantics_repo,
        application_root=app_root,
    )

    return CanonicalEvidenceAssemblyService(
        repository=assembled_evidence_repository,
        native_evidence_query=value_capture_service,
        methodology_contract_authority=methodology_authority,
        evidence_shape_registry_authority=registry_authority,
        evidence_semantics_authority=semantics_authority,
    )
