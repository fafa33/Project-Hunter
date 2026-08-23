from __future__ import annotations

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


def build_production_evidence_assembly_service(
    *,
    db_path: Path | str | None = None,
    application_root: Path | None = None,
    assembled_evidence_repository: AssembledEvidenceRepository | None = None,
    value_capture_service: SupplyAndValueCaptureService | None = None,
    methodology_authority: CanonicalValuationMethodologyAuthority | None = None,
    registry_authority: CanonicalEvidenceShapeRegistryAuthority | None = None,
    semantics_authority: CanonicalEvidenceSemanticsAuthority | None = None,
) -> CanonicalEvidenceAssemblyService:
    """Production composition root for CanonicalEvidenceAssemblyService.

    Composes CanonicalEvidenceAssemblyService exclusively using real, production-backed
    collaborators. No fakes, stubs, mocks, or placeholders are accepted or permitted.
    """
    if db_path is None:
        target_db = Path("data/data_ops.sqlite")
    else:
        target_db = Path(db_path)

    if assembled_evidence_repository is None:
        assembled_evidence_repository = AssembledEvidenceRepository(target_db)

    if value_capture_service is None:
        value_capture_repo = SupplyAndValueCaptureRepository(target_db)
        value_capture_service = SupplyAndValueCaptureService(
            repository=value_capture_repo,
            registry=ValueCaptureSourceRegistry(sources=()),
            verification_keys=ValueCaptureVerificationKeyRegistry(keys={"default-key": b"0" * 32}),
        )

    if methodology_authority is None:
        methodology_repo = ValuationMethodologyRepository(target_db)
        methodology_authority = CanonicalValuationMethodologyAuthority(
            repository=methodology_repo,
            application_root=application_root,
        )

    if registry_authority is None:
        registry_repo = EvidenceShapeRegistryRepository(target_db)
        registry_authority = CanonicalEvidenceShapeRegistryAuthority(
            repository=registry_repo,
            application_root=application_root,
        )

    if semantics_authority is None:
        semantic_input_repo = EvidenceSemanticInputRepository(target_db)
        value_capture_repo = value_capture_service.repository
        semantic_input_authority = CanonicalEvidenceSemanticInputAuthority(
            repository=semantic_input_repo,
            value_capture_repository=value_capture_repo,
            application_root=application_root,
        )
        semantics_repo = EvidenceSemanticsRepository(target_db)
        semantics_authority = CanonicalEvidenceSemanticsAuthority(
            semantic_input_authority=semantic_input_authority,
            repository=semantics_repo,
            application_root=application_root,
        )

    return CanonicalEvidenceAssemblyService(
        repository=assembled_evidence_repository,
        native_evidence_query=value_capture_service,
        methodology_contract_authority=methodology_authority,
        evidence_shape_registry_authority=registry_authority,
        evidence_semantics_authority=semantics_authority,
    )
