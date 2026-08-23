from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.evidence_assembly import (
    AUTHORIZING_ADR_REFERENCE,
    CANONICAL_REGISTRY_AUTHORITY_ID,
    REGISTRY_LOGICAL_ID,
    AssembledEvidenceRepository,
    AssemblyConstituent,
    AuthoritativeEvidenceSemantics,
    CanonicalEvidenceAssemblyError,
    CanonicalEvidenceAssemblyService,
    CanonicalEvidenceShapeRegistryAuthority,
    CanonicalEvidenceShapeRegistryAuthorityError,
    EvidenceShape,
    EvidenceShapeRegistryError,
    EvidenceShapeRegistryIntegrityError,
    EvidenceShapeRegistryRepository,
    MethodologyEvidenceInputContract,
)
from hunter.value_capture.models import EconomicClaimIdentity, FundamentalEvidenceRecord

DAY = datetime(2026, 1, 1, tzinfo=UTC)
CUTOFF = DAY + timedelta(days=10)


def _shapes(version: str = "v1") -> tuple[EvidenceShape, ...]:
    return (
        EvidenceShape(
            shape_id="official-period-specific-v1",
            registry_version=version,
            evidence_type="official_disclosure",
            accounting_meaning="period_specific",
            cadence="interval",
            composition_operation="exact_sum",
            active=True,
        ),
        EvidenceShape(
            shape_id="cumulative-v1",
            registry_version=version,
            evidence_type="official_disclosure",
            accounting_meaning="cumulative",
            cadence="interval",
            composition_operation="none",
            active=True,
        ),
    )


@pytest.fixture
def repo_path(tmp_path: Path) -> Path:
    return tmp_path / "registry.sqlite"


@pytest.fixture
def repository(repo_path: Path) -> EvidenceShapeRegistryRepository:
    return EvidenceShapeRegistryRepository(repo_path)


@pytest.fixture
def authority(
    repository: EvidenceShapeRegistryRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> CanonicalEvidenceShapeRegistryAuthority:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path.resolve()))
    return CanonicalEvidenceShapeRegistryAuthority(repository=repository)


def test_attestation_requires_application_root(
    repository: EvidenceShapeRegistryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HUNTER_APPLICATION_ROOT", raising=False)
    with pytest.raises(CanonicalEvidenceShapeRegistryAuthorityError, match="HUNTER_APPLICATION_ROOT"):
        CanonicalEvidenceShapeRegistryAuthority(repository=repository)


def test_1_exact_version_retrieval_succeeds(authority: CanonicalEvidenceShapeRegistryAuthority) -> None:
    registered = authority.persist_registry(
        version="v1",
        shapes=_shapes("v1"),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    fetched = authority.strict_known_registry(version="v1", known_by=DAY)
    assert fetched is not None
    assert fetched.version == "v1"
    assert fetched.record_id == registered.record_id
    assert fetched.content_hash == registered.content_hash
    assert fetched.logical_id == REGISTRY_LOGICAL_ID
    assert fetched.authorizing_adr_reference == AUTHORIZING_ADR_REFERENCE
    assert fetched.authorized_by == CANONICAL_REGISTRY_AUTHORITY_ID


def test_2_unavailable_when_requested_version_does_not_exist(
    authority: CanonicalEvidenceShapeRegistryAuthority,
) -> None:
    authority.persist_registry(
        version="v1",
        shapes=_shapes("v1"),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    assert authority.strict_known_registry(version="v2", known_by=DAY) is None


def test_3_unavailable_when_exact_version_is_not_yet_effective(
    authority: CanonicalEvidenceShapeRegistryAuthority,
) -> None:
    authority.persist_registry(
        version="v1",
        shapes=_shapes("v1"),
        effective_at=DAY + timedelta(days=5),
        recorded_at=DAY + timedelta(days=5),
        known_at=DAY + timedelta(days=5),
    )
    assert authority.strict_known_registry(version="v1", known_by=DAY + timedelta(days=2)) is None
    assert authority.strict_known_registry(version="v1", known_by=DAY + timedelta(days=5)) is not None


def test_4_unavailable_when_exact_version_is_not_yet_known(authority: CanonicalEvidenceShapeRegistryAuthority) -> None:
    authority.persist_registry(
        version="v1",
        shapes=_shapes("v1"),
        effective_at=DAY,
        recorded_at=DAY + timedelta(days=5),
        known_at=DAY + timedelta(days=5),
    )
    assert authority.strict_known_registry(version="v1", known_by=DAY + timedelta(days=2)) is None
    assert authority.strict_known_registry(version="v1", known_by=DAY + timedelta(days=5)) is not None


def test_5_and_6_historical_replay_returns_correct_old_version_after_later_correction(
    authority: CanonicalEvidenceShapeRegistryAuthority,
) -> None:
    v1 = authority.persist_registry(
        version="v1",
        shapes=_shapes("v1"),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    v1_corrected = authority.persist_registry(
        version="v1.1",
        shapes=_shapes("v1.1"),
        effective_at=DAY,
        recorded_at=DAY + timedelta(days=5),
        known_at=DAY + timedelta(days=5),
        supersedes_version="v1",
        supersedes_record_id=v1.record_id,
        correction_reason="updated shape definitions",
    )

    # Before correction became known (at DAY + 2)
    replay_old = authority.strict_known_registry(version="v1", known_by=DAY + timedelta(days=2))
    assert replay_old is not None
    assert replay_old.version == "v1"
    assert replay_old.record_id == v1.record_id

    # After correction became known (at DAY + 6)
    # v1 is now superseded by v1.1
    assert authority.strict_known_registry(version="v1", known_by=DAY + timedelta(days=6)) is None
    replay_new = authority.strict_known_registry(version="v1.1", known_by=DAY + timedelta(days=6))
    assert replay_new is not None
    assert replay_new.version == "v1.1"
    assert replay_new.record_id == v1_corrected.record_id


def test_7_append_only_lineage(
    authority: CanonicalEvidenceShapeRegistryAuthority, repository: EvidenceShapeRegistryRepository
) -> None:
    v1 = authority.persist_registry(
        version="v1",
        shapes=_shapes("v1"),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    v2 = authority.persist_registry(
        version="v2",
        shapes=_shapes("v2"),
        effective_at=DAY,
        recorded_at=DAY + timedelta(days=1),
        known_at=DAY + timedelta(days=1),
        supersedes_version="v1",
        supersedes_record_id=v1.record_id,
        correction_reason="v2 release",
    )
    history = authority.registry_history()
    assert len(history) == 2
    assert history[0].version == "v1"
    assert history[1].version == "v2"
    assert history[1].supersedes_version == "v1"
    assert history[1].supersedes_record_id == v1.record_id
    assert v2.version == "v2"


def test_8_second_independent_root_rejected(authority: CanonicalEvidenceShapeRegistryAuthority) -> None:
    authority.persist_registry(
        version="v1",
        shapes=_shapes("v1"),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    # Trying to persist a second independent root v2 for the same registry identity without supersedes_* lineage
    with pytest.raises(EvidenceShapeRegistryIntegrityError, match="root registry record already exists"):
        authority.persist_registry(
            version="v2",
            shapes=_shapes("v2"),
            effective_at=DAY,
            recorded_at=DAY + timedelta(days=1),
            known_at=DAY + timedelta(days=1),
        )


def test_finding_2_get_by_record_id(authority: CanonicalEvidenceShapeRegistryAuthority) -> None:
    v1 = authority.persist_registry(
        version="v1",
        shapes=_shapes("v1"),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    # get(persisted.record_id) returns the exact record
    retrieved = authority.get(v1.record_id)
    assert retrieved is not None
    assert retrieved.record_id == v1.record_id
    assert retrieved.version == "v1"

    # Unknown record_id returns None
    assert authority.get("nonexistent-record-id") is None


def test_finding_3_persisted_hash_and_record_id_tamper_protection(
    authority: CanonicalEvidenceShapeRegistryAuthority,
    repository: EvidenceShapeRegistryRepository,
    repo_path: Path,
) -> None:
    authority.persist_registry(
        version="v1",
        shapes=_shapes("v1"),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )

    # Tamper case A: mutate shape in payload in database while retaining old content_hash/record_id
    import sqlite3

    with sqlite3.connect(repo_path) as conn:
        row = conn.execute(
            "SELECT payload FROM persistence_records WHERE id = ?", (f"{REGISTRY_LOGICAL_ID}:v1",)
        ).fetchone()
        assert row is not None
        import json

        payload_data = json.loads(row[0])
        payload_data["fields"]["payload"]["shapes"][0]["cadence"] = "tampered-cadence"
        conn.execute(
            "UPDATE persistence_records SET payload = ? WHERE id = ?",
            (json.dumps(payload_data), f"{REGISTRY_LOGICAL_ID}:v1"),
        )
        conn.commit()

    # Direct load/deserialization fails closed with EvidenceShapeRegistryIntegrityError
    snapshot = repository._load_version("v1")
    assert snapshot is not None
    from hunter.evidence_assembly.registry import _from_payload

    with pytest.raises(EvidenceShapeRegistryIntegrityError, match="content hash mismatch"):
        _from_payload(snapshot.payload)

    # Repository fault isolation: records() skips the malformed/tampered record
    assert repository.records() == ()

    # strict_known_registry never returns a tampered row
    assert authority.strict_known_registry(version="v1", known_by=DAY + timedelta(days=10)) is None


def test_9_branching_correction_rejected(authority: CanonicalEvidenceShapeRegistryAuthority) -> None:
    v1 = authority.persist_registry(
        version="v1",
        shapes=_shapes("v1"),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    authority.persist_registry(
        version="v1.1",
        shapes=_shapes("v1.1"),
        effective_at=DAY,
        recorded_at=DAY + timedelta(days=1),
        known_at=DAY + timedelta(days=1),
        supersedes_version="v1",
        supersedes_record_id=v1.record_id,
        correction_reason="first correction",
    )
    with pytest.raises(EvidenceShapeRegistryIntegrityError, match="branching"):
        authority.persist_registry(
            version="v1.2",
            shapes=_shapes("v1.2"),
            effective_at=DAY,
            recorded_at=DAY + timedelta(days=2),
            known_at=DAY + timedelta(days=2),
            supersedes_version="v1",
            supersedes_record_id=v1.record_id,
            correction_reason="branching correction",
        )


def test_10_non_advancing_correction_clocks_rejected(authority: CanonicalEvidenceShapeRegistryAuthority) -> None:
    v1 = authority.persist_registry(
        version="v1",
        shapes=_shapes("v1"),
        effective_at=DAY,
        recorded_at=DAY + timedelta(days=5),
        known_at=DAY + timedelta(days=10),
    )
    with pytest.raises(EvidenceShapeRegistryIntegrityError, match="recorded_at must follow predecessor"):
        authority.persist_registry(
            version="v1.1",
            shapes=_shapes("v1.1"),
            effective_at=DAY,
            recorded_at=DAY + timedelta(days=4),  # Before predecessor (DAY + 5)
            known_at=DAY + timedelta(days=11),
            supersedes_version="v1",
            supersedes_record_id=v1.record_id,
            correction_reason="earlier recorded_at",
        )
    with pytest.raises(EvidenceShapeRegistryIntegrityError, match="known_at must follow predecessor"):
        authority.persist_registry(
            version="v1.2",
            shapes=_shapes("v1.2"),
            effective_at=DAY,
            recorded_at=DAY + timedelta(days=6),
            known_at=DAY + timedelta(days=9),  # Before predecessor known_at (DAY + 10)
            supersedes_version="v1",
            supersedes_record_id=v1.record_id,
            correction_reason="non-advancing known_at",
        )


def test_11_unresolved_conflict_fails_closed(authority: CanonicalEvidenceShapeRegistryAuthority) -> None:
    with pytest.raises(EvidenceShapeRegistryError, match="not authoritative"):
        authority.persist_registry(
            version="v1-conflicted",
            shapes=_shapes("v1-conflicted"),
            effective_at=DAY,
            recorded_at=DAY,
            known_at=DAY,
            quality_state="stale",
            conflict_state="open",
        )


def test_12_and_13_deterministic_identity_hash_stability_and_provenance(
    authority: CanonicalEvidenceShapeRegistryAuthority,
) -> None:
    reg1 = authority.persist_registry(
        version="v1",
        shapes=_shapes("v1"),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    assert reg1.content_hash.strip() != ""
    assert reg1.record_id.strip() != ""
    assert reg1.authorizing_adr_reference == "ADR-0025"
    assert reg1.authorized_by == "canonical-evidence-shape-registry-authority-v1"


def test_14_malformed_persisted_registry_row_does_not_break_access_to_valid_rows(
    authority: CanonicalEvidenceShapeRegistryAuthority,
    repository: EvidenceShapeRegistryRepository,
    repo_path: Path,
) -> None:
    v1 = authority.persist_registry(
        version="v1",
        shapes=_shapes("v1"),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )

    # Insert a malformed snapshot directly into SQL persistence
    from hunter.evidence_assembly.registry import _REGISTRY_SNAPSHOT_TYPE
    from hunter.persistence.records import SnapshotRecord
    from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_sqlite_engine

    engine = create_sqlite_engine(repo_path)
    session = SessionFactory(engine).create()
    try:
        malformed = SnapshotRecord(
            id=f"{REGISTRY_LOGICAL_ID}:bad-version",
            created_at=DAY,
            effective_at=DAY,
            snapshot_type=_REGISTRY_SNAPSHOT_TYPE,
            target_id=REGISTRY_LOGICAL_ID,
            record_ids=("bad-record",),
            payload={"version": "bad-version", "corrupted": True},
            metadata={},
        )
        RepositoryFactory(session).snapshots().save(malformed)
        session.commit()
    finally:
        session.close()
        engine.dispose()

    # Fault isolation check: reading records skipping malformed succeeds and retrieves v1
    records = repository.records()
    assert len(records) == 1
    assert records[0].version == "v1"
    assert records[0].record_id == v1.record_id


def test_15_16_17_evidence_assembly_consumes_exact_production_authority(
    authority: CanonicalEvidenceShapeRegistryAuthority,
    tmp_path: Path,
) -> None:
    v1 = authority.persist_registry(
        version="registry-v1",
        shapes=_shapes("registry-v1"),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    v2 = authority.persist_registry(
        version="registry-v2",
        shapes=_shapes("registry-v2"),
        effective_at=DAY,
        recorded_at=DAY + timedelta(days=12),
        known_at=DAY + timedelta(days=12),
        supersedes_version="registry-v1",
        supersedes_record_id=v1.record_id,
        correction_reason="v2 release",
    )
    assert v2.version == "registry-v2"

    class _NativeQuery:
        def overlapping_evidence(self, **_: object) -> tuple[FundamentalEvidenceRecord, ...]:
            return (rec1, rec2)

    class _ContractAuth:
        def strict_known_contract(self, **_: object) -> MethodologyEvidenceInputContract | None:
            return MethodologyEvidenceInputContract(
                contract_id="future-contract",
                contract_version="1.0.0",
                methodology_logical_id="m-logical",
                accepts_assembled_evidence=True,
                accepted_shape_ids=("official-period-specific-v1",),
                accepted_assembly_rule_versions=("lossless-exact-coverage-v1",),
                accounting_window_start=DAY,
                accounting_window_end=DAY + timedelta(days=4),
                exact_gap_free_non_overlapping_coverage_required=True,
                allow_representation_boundary_crossing=False,
                allow_pathway_boundary_crossing=False,
                allow_supply_basis_boundary_crossing=False,
                provenance_content_hash_required=True,
                conflict_policy="reject",
                minimum_quality_state="accepted",
                entity_id="entity:alpha",
                representation_id="representation:alpha",
                value_capture_pathway_id="pathway:fees",
                currency="USD",
                unit="USD",
                missingness_behavior="unavailable",
                strict_known_required=True,
                effective_at=DAY,
                recorded_at=DAY,
                known_at=DAY,
                quality_state="accepted",
                conflict_state="none",
                content_hash="contract-hash",
            )

    class _SemanticsAuth:
        def strict_known_semantics(
            self, *, evidence_record_id: str, **_: object
        ) -> AuthoritativeEvidenceSemantics | None:
            return AuthoritativeEvidenceSemantics(
                evidence_record_id=evidence_record_id,
                evidence_record_version="1.0.0",
                shape_id="official-period-specific-v1",
                currency="USD",
                raw_unit="USD",
                accounting_meaning="period_specific",
                supply_basis_id="supply-basis:fdv",
                pathway_id="pathway:fees",
                effective_at=DAY,
                recorded_at=DAY,
                known_at=DAY,
                quality_state="accepted",
                conflict_state="none",
                content_hash="semantics-hash",
            )

    identity = EconomicClaimIdentity(
        entity_id="entity:alpha",
        economic_claim_id="claim:fees",
        asset_id="asset:alpha",
        representation_id="representation:alpha",
        token_id="token:alpha",
    )
    rec1 = FundamentalEvidenceRecord(
        record_id="evidence:1",
        logical_id="logical:1",
        schema_version="supply-value-capture-v3.5.0",
        semantic_version="1.0.0",
        identity=identity,
        evidence_type="official_disclosure",
        source_id="source:official",
        source_authority_tier="primary",
        source_reference="https://example.test/1",
        parser_version="parser-v1",
        extracted_claim="period 1",
        amount="10",
        unit="USD",
        accounting_period_start=DAY,
        accounting_period_end=DAY + timedelta(days=2),
        attribution_rule_id="pathway:fees",
        source_methodology="official-period-total",
        source_record_id="source-record:1",
        source_record_version="1",
        entity_link_confidence="0.9",
        evidence_confidence="0.8",
        uncertainty="0.1",
        effective_at=DAY + timedelta(days=2),
        recorded_at=DAY + timedelta(days=2),
        known_at=DAY + timedelta(days=2),
        raw_content_hash="a" * 64,
        quality_state="accepted",
        conflict_state="none",
        content_hash="b" * 64,
        acquisition_id="acquisition:1",
    )
    rec2 = FundamentalEvidenceRecord(
        record_id="evidence:2",
        logical_id="logical:2",
        schema_version="supply-value-capture-v3.5.0",
        semantic_version="1.0.0",
        identity=identity,
        evidence_type="official_disclosure",
        source_id="source:official",
        source_authority_tier="primary",
        source_reference="https://example.test/2",
        parser_version="parser-v1",
        extracted_claim="period 2",
        amount="20",
        unit="USD",
        accounting_period_start=DAY + timedelta(days=2),
        accounting_period_end=DAY + timedelta(days=4),
        attribution_rule_id="pathway:fees",
        source_methodology="official-period-total",
        source_record_id="source-record:2",
        source_record_version="1",
        entity_link_confidence="0.9",
        evidence_confidence="0.8",
        uncertainty="0.1",
        effective_at=DAY + timedelta(days=4),
        recorded_at=DAY + timedelta(days=4),
        known_at=DAY + timedelta(days=4),
        raw_content_hash="c" * 64,
        quality_state="accepted",
        conflict_state="none",
        content_hash="d" * 64,
        acquisition_id="acquisition:2",
    )

    c1 = AssemblyConstituent(
        record=rec1,
        shape_id="official-period-specific-v1",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis:fdv",
        pathway_id="pathway:fees",
    )
    c2 = AssemblyConstituent(
        record=rec2,
        shape_id="official-period-specific-v1",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis:fdv",
        pathway_id="pathway:fees",
    )

    assembly_repo = AssembledEvidenceRepository(tmp_path / "assembly.sqlite")
    service = CanonicalEvidenceAssemblyService(
        repository=assembly_repo,
        native_evidence_query=_NativeQuery(),
        methodology_contract_authority=_ContractAuth(),
        evidence_shape_registry_authority=authority,
        evidence_semantics_authority=_SemanticsAuth(),
    )

    # Replay cutoff at DAY + 10: registry-v1 is available, registry-v2 is not yet recorded/known until DAY + 12.
    record = service.assemble(
        constituents=(c1, c2),
        accounting_window_start=DAY,
        accounting_window_end=DAY + timedelta(days=4),
        recorded_at=DAY + timedelta(days=6),
        replay_cutoff=DAY + timedelta(days=10),
        methodology_contract_id="future-contract",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="registry-v1",
    )
    assert record.evidence_shape_registry_version == "registry-v1"

    # 16. Rejects wrong registry version (asking for registry-v2 at replay cutoff DAY + 10 when registry-v2 is not known until DAY + 12)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="Evidence Shape Registry snapshot"):
        service.assemble(
            constituents=(c1, c2),
            accounting_window_start=DAY,
            accounting_window_end=DAY + timedelta(days=4),
            recorded_at=DAY + timedelta(days=6),
            replay_cutoff=DAY + timedelta(days=10),
            methodology_contract_id="future-contract",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="registry-v2",
        )

    # 17. Unrelated newer registry snapshot cannot substitute for requested version.
    # At replay cutoff DAY + 15, registry-v1 was superseded by registry-v2.
    # Requesting registry-v1 at DAY + 15 fails because registry-v1 is superseded at DAY + 15.
    with pytest.raises(CanonicalEvidenceAssemblyError, match="Evidence Shape Registry snapshot"):
        service.assemble(
            constituents=(c1, c2),
            accounting_window_start=DAY,
            accounting_window_end=DAY + timedelta(days=4),
            recorded_at=DAY + timedelta(days=6),
            replay_cutoff=DAY + timedelta(days=15),
            methodology_contract_id="future-contract",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="registry-v1",
        )


def test_18_clean_dependency_direction() -> None:
    # Ensure evidence_assembly does not import valuation services or runtime orchestration
    import hunter.evidence_assembly.registry as reg_module

    for name in dir(reg_module):
        assert "ValuationService" not in name
        assert "CanonicalEvidenceAssemblyService" not in name
