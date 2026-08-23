from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.evidence_assembly import (
    AssembledEvidenceRepository,
    AssemblyConstituent,
    CanonicalEvidenceAssemblyError,
    CanonicalEvidenceAssemblyService,
    CanonicalEvidenceSemanticsAuthority,
    CanonicalEvidenceSemanticsAuthorityError,
    CanonicalEvidenceShapeRegistryAuthority,
    EvidenceSemanticsRepository,
    EvidenceShape,
    MethodologyEvidenceInputContract,
)
from hunter.evidence_semantic_inputs import (
    POLICY_LOGICAL_ID,
    CanonicalEvidenceSemanticInputAuthority,
    CanonicalEvidenceSemanticInputAuthorityError,
    EvidenceSemanticInputError,
    EvidenceSemanticInputIntegrityError,
    EvidenceSemanticInputPolicySnapshot,
    EvidenceSemanticInputRepository,
    EvidenceSemanticInputRule,
)
from hunter.value_capture.models import EconomicClaimIdentity, FundamentalEvidenceRecord
from hunter.value_capture.repository import SupplyAndValueCaptureRepository, record_snapshot

DAY = datetime(2026, 1, 1, tzinfo=UTC)
CUTOFF = DAY + timedelta(days=10)


def _rules() -> tuple[EvidenceSemanticInputRule, ...]:
    return (
        EvidenceSemanticInputRule(
            rule_id="rule-official-fees",
            match_evidence_type="official_disclosure",
            match_attribution_rule_id="pathway:fees",
            shape_id="official-period-specific-v1",
            accounting_meaning="period_specific",
            supply_basis_id="supply-basis:fdv",
            pathway_id="pathway:fees",
            asserts_representation_continuity=True,
            currency="USD",
            raw_unit="USD",
            semantic_dimensions={"tier": "primary"},
        ),
    )


def _evidence_record(rec_id: str = "evidence:1", start_day: int = 0, duration: int = 2) -> FundamentalEvidenceRecord:
    identity = EconomicClaimIdentity(
        entity_id="entity:alpha",
        economic_claim_id="claim:fees",
        asset_id="asset:alpha",
        representation_id="representation:alpha",
        token_id="token:alpha",
    )
    return FundamentalEvidenceRecord(
        record_id=rec_id,
        logical_id=f"logical:{rec_id}",
        schema_version="supply-value-capture-v3.5.0",
        semantic_version="1.0.0",
        identity=identity,
        evidence_type="official_disclosure",
        source_id="source:official",
        source_authority_tier="primary",
        source_reference="https://example.test/1",
        parser_version="parser-v1",
        extracted_claim="period claim",
        amount="100",
        unit="USD",
        accounting_period_start=DAY + timedelta(days=start_day),
        accounting_period_end=DAY + timedelta(days=start_day + duration),
        attribution_rule_id="pathway:fees",
        source_methodology="official-period-total",
        source_record_id=f"source-record:{rec_id}",
        source_record_version="1",
        entity_link_confidence="0.9",
        evidence_confidence="0.8",
        uncertainty="0.1",
        effective_at=DAY + timedelta(days=start_day + duration),
        recorded_at=DAY + timedelta(days=start_day + duration),
        known_at=DAY + timedelta(days=start_day + duration),
        raw_content_hash="a" * 64,
        quality_state="accepted",
        conflict_state="none",
        content_hash="b" * 64,
        acquisition_id="acquisition:1",
    )


def _persist_native_evidence(repo: SupplyAndValueCaptureRepository, record: FundamentalEvidenceRecord) -> None:
    from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_sqlite_engine

    engine = create_sqlite_engine(repo.path)
    session = SessionFactory(engine).create()
    try:
        RepositoryFactory(session).snapshots().save(record_snapshot(record))
        session.commit()
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def value_capture_repo(tmp_path: Path) -> SupplyAndValueCaptureRepository:
    return SupplyAndValueCaptureRepository(tmp_path / "value_capture.sqlite")


@pytest.fixture
def semantic_input_repo(tmp_path: Path) -> EvidenceSemanticInputRepository:
    return EvidenceSemanticInputRepository(tmp_path / "semantic_inputs.sqlite")


@pytest.fixture
def semantic_input_authority(
    semantic_input_repo: EvidenceSemanticInputRepository,
    value_capture_repo: SupplyAndValueCaptureRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> CanonicalEvidenceSemanticInputAuthority:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path.resolve()))
    return CanonicalEvidenceSemanticInputAuthority(
        repository=semantic_input_repo,
        value_capture_repository=value_capture_repo,
    )


@pytest.fixture
def semantics_repo(tmp_path: Path) -> EvidenceSemanticsRepository:
    return EvidenceSemanticsRepository(tmp_path / "semantics.sqlite")


@pytest.fixture
def semantics_authority(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    semantics_repo: EvidenceSemanticsRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> CanonicalEvidenceSemanticsAuthority:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path.resolve()))
    return CanonicalEvidenceSemanticsAuthority(
        semantic_input_authority=semantic_input_authority,
        repository=semantics_repo,
    )


def test_semantic_input_authority_attestation_requires_root(
    semantic_input_repo: EvidenceSemanticInputRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HUNTER_APPLICATION_ROOT", raising=False)
    with pytest.raises(CanonicalEvidenceSemanticInputAuthorityError, match="HUNTER_APPLICATION_ROOT"):
        CanonicalEvidenceSemanticInputAuthority(repository=semantic_input_repo)


def test_semantics_authority_attestation_requires_root(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    semantics_repo: EvidenceSemanticsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HUNTER_APPLICATION_ROOT", raising=False)
    with pytest.raises(CanonicalEvidenceSemanticsAuthorityError, match="HUNTER_APPLICATION_ROOT"):
        CanonicalEvidenceSemanticsAuthority(
            semantic_input_authority=semantic_input_authority,
            repository=semantics_repo,
        )


def test_policy_persistence_and_strict_known_lookup(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
) -> None:
    policy = semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=_rules(),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    assert policy.version == "1.0.0"
    assert policy.logical_id == POLICY_LOGICAL_ID

    fetched = semantic_input_authority.strict_known_policy(effective_as_of=DAY, known_by=DAY)
    assert fetched is not None
    assert fetched.record_id == policy.record_id
    assert fetched.content_hash == policy.content_hash


def test_derive_and_persist_input(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    value_capture_repo: SupplyAndValueCaptureRepository,
) -> None:
    policy = semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=_rules(),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    ev_record = _evidence_record("evidence:1")
    _persist_native_evidence(value_capture_repo, ev_record)

    input_rec = semantic_input_authority.derive_and_persist_input(
        evidence_record=ev_record,
        policy_snapshot=policy,
        recorded_at=DAY + timedelta(days=2),
        known_at=DAY + timedelta(days=2),
    )
    assert input_rec.evidence_record_id == "evidence:1"
    assert input_rec.shape_id == "official-period-specific-v1"
    assert input_rec.representation_continuity_asserted is True
    assert input_rec.representation_continuity_proof_id == policy.record_id

    fetched = semantic_input_authority.strict_known_input(
        evidence_record_id="evidence:1",
        evidence_record_version="1.0.0",
        effective_as_of=DAY + timedelta(days=2),
        known_by=DAY + timedelta(days=2),
    )
    assert fetched is not None
    assert fetched.record_id == input_rec.record_id
    assert fetched.content_hash == input_rec.content_hash


def test_unpersisted_evidence_object_rejected(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
) -> None:
    policy = semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=_rules(),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    ev_record = _evidence_record("evidence:unpersisted")
    with pytest.raises(EvidenceSemanticInputError, match="unpersisted evidence record"):
        semantic_input_authority.derive_and_persist_input(
            evidence_record=ev_record,
            policy_snapshot=policy,
            recorded_at=DAY + timedelta(days=2),
            known_at=DAY + timedelta(days=2),
        )


def test_unpersisted_policy_snapshot_rejected(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    value_capture_repo: SupplyAndValueCaptureRepository,
) -> None:
    ev_record = _evidence_record("evidence:1")
    _persist_native_evidence(value_capture_repo, ev_record)

    # Construct an unpersisted policy snapshot with valid content hash / record_id
    temp_policy = EvidenceSemanticInputPolicySnapshot(
        record_id="pending",
        logical_id=POLICY_LOGICAL_ID,
        schema_version="evidence-semantic-input-policy-v1",
        semantic_version="1.0.0",
        version="9.9.9",
        rules=_rules(),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
        quality_state="accepted",
        conflict_state="none",
        authorizing_adr_reference="ADR-0028",
        authorized_by="canonical-evidence-semantic-input-authority-v1",
        content_hash="pending",
    )
    from hunter.evidence_semantic_inputs import _normalize_policy

    policy = _normalize_policy(temp_policy)

    with pytest.raises(EvidenceSemanticInputError, match="unpersisted policy snapshot"):
        semantic_input_authority.derive_and_persist_input(
            evidence_record=ev_record,
            policy_snapshot=policy,
            recorded_at=DAY + timedelta(days=2),
            known_at=DAY + timedelta(days=2),
        )


def test_future_policy_not_strict_known_rejected(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    value_capture_repo: SupplyAndValueCaptureRepository,
) -> None:
    ev_record = _evidence_record("evidence:1")
    _persist_native_evidence(value_capture_repo, ev_record)

    # Persist policy that becomes known in the future (DAY + 10)
    future_policy = semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=_rules(),
        effective_at=DAY + timedelta(days=10),
        recorded_at=DAY + timedelta(days=10),
        known_at=DAY + timedelta(days=10),
    )

    # Attempt derivation at cutoff DAY + 2 -> should reject future policy
    with pytest.raises(EvidenceSemanticInputError, match="not strict-known at derivation cutoff"):
        semantic_input_authority.derive_and_persist_input(
            evidence_record=ev_record,
            policy_snapshot=future_policy,
            recorded_at=DAY + timedelta(days=2),
            known_at=DAY + timedelta(days=2),
        )


def test_no_matching_rule_raises(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    value_capture_repo: SupplyAndValueCaptureRepository,
) -> None:
    policy = semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=_rules(),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    unmatched_record = FundamentalEvidenceRecord(
        record_id="evidence:unmatched",
        logical_id="logical:unmatched",
        schema_version="supply-value-capture-v3.5.0",
        semantic_version="1.0.0",
        identity=EconomicClaimIdentity("entity:a", "claim:a", "asset:a", "rep:a", "token:a"),
        evidence_type="governance_record",
        source_id="source:x",
        source_authority_tier="primary",
        source_reference="https://example.test",
        parser_version="p1",
        extracted_claim="claim",
        amount="10",
        unit="USD",
        accounting_period_start=DAY,
        accounting_period_end=DAY + timedelta(days=1),
        attribution_rule_id="pathway:unmatched",
        source_methodology="m1",
        source_record_id="sr1",
        source_record_version="1",
        entity_link_confidence="1.0",
        evidence_confidence="1.0",
        uncertainty="0.0",
        effective_at=DAY + timedelta(days=1),
        recorded_at=DAY + timedelta(days=1),
        known_at=DAY + timedelta(days=1),
        raw_content_hash="a" * 64,
        quality_state="accepted",
        conflict_state="none",
        content_hash="b" * 64,
        acquisition_id="acq1",
    )
    _persist_native_evidence(value_capture_repo, unmatched_record)

    with pytest.raises(EvidenceSemanticInputError, match="no policy rule matched"):
        semantic_input_authority.derive_and_persist_input(
            evidence_record=unmatched_record,
            policy_snapshot=policy,
            recorded_at=DAY + timedelta(days=2),
            known_at=DAY + timedelta(days=2),
        )


def test_conflicting_multiple_matching_rules_raises(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    value_capture_repo: SupplyAndValueCaptureRepository,
) -> None:
    conflicting_rules = (
        EvidenceSemanticInputRule(
            rule_id="r1",
            match_evidence_type="official_disclosure",
            shape_id="shape-1",
            accounting_meaning="period_specific",
            supply_basis_id="sb1",
            pathway_id="pw1",
            currency="USD",
            raw_unit="USD",
        ),
        EvidenceSemanticInputRule(
            rule_id="r2",
            match_evidence_type="official_disclosure",
            shape_id="shape-2",
            accounting_meaning="cumulative",
            supply_basis_id="sb2",
            pathway_id="pw2",
            currency="USD",
            raw_unit="USD",
        ),
    )
    policy = semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=conflicting_rules,
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    ev_record = _evidence_record("evidence:1")
    _persist_native_evidence(value_capture_repo, ev_record)

    with pytest.raises(EvidenceSemanticInputError, match="conflict: multiple non-equivalent policy rules"):
        semantic_input_authority.derive_and_persist_input(
            evidence_record=ev_record,
            policy_snapshot=policy,
            recorded_at=DAY + timedelta(days=2),
            known_at=DAY + timedelta(days=2),
        )


def test_p1_finding_2_no_semantics_record_fails_closed(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    semantics_authority: CanonicalEvidenceSemanticsAuthority,
    value_capture_repo: SupplyAndValueCaptureRepository,
) -> None:
    policy = semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=_rules(),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    ev_record = _evidence_record("evidence:1")
    _persist_native_evidence(value_capture_repo, ev_record)

    # Upstream input exists
    input_rec = semantic_input_authority.derive_and_persist_input(
        evidence_record=ev_record,
        policy_snapshot=policy,
        recorded_at=DAY + timedelta(days=2),
        known_at=DAY + timedelta(days=2),
    )
    assert input_rec is not None

    # But NO AuthoritativeEvidenceSemanticsRecord is registered!
    # P1 Finding 2: strict_known_semantics MUST return None (fail closed)
    semantics = semantics_authority.strict_known_semantics(
        evidence_record_id="evidence:1",
        evidence_record_version="1.0.0",
        known_by=DAY + timedelta(days=5),
    )
    assert semantics is None


def test_p1_finding_3_continuity_proof_mismatch_raises(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    semantics_authority: CanonicalEvidenceSemanticsAuthority,
    value_capture_repo: SupplyAndValueCaptureRepository,
    tmp_path: Path,
) -> None:
    # 1. Setup registry
    from hunter.evidence_assembly import EvidenceShapeRegistryRepository

    registry_auth = CanonicalEvidenceShapeRegistryAuthority(
        repository=EvidenceShapeRegistryRepository(tmp_path / "registry.sqlite"),
        application_root=tmp_path,
    )
    registry_auth.persist_registry(
        version="reg-v1",
        shapes=(
            EvidenceShape(
                shape_id="official-period-specific-v1",
                registry_version="reg-v1",
                evidence_type="official_disclosure",
                accounting_meaning="period_specific",
                cadence="interval",
                composition_operation="exact_sum",
                active=True,
            ),
        ),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )

    policy = semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=_rules(),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    ev1 = _evidence_record("ev:1", start_day=0, duration=2)
    ev2 = _evidence_record("ev:2", start_day=2, duration=2)
    _persist_native_evidence(value_capture_repo, ev1)
    _persist_native_evidence(value_capture_repo, ev2)

    input1 = semantic_input_authority.derive_and_persist_input(
        evidence_record=ev1,
        policy_snapshot=policy,
        recorded_at=DAY + timedelta(days=2),
        known_at=DAY + timedelta(days=2),
    )
    input2 = semantic_input_authority.derive_and_persist_input(
        evidence_record=ev2,
        policy_snapshot=policy,
        recorded_at=DAY + timedelta(days=4),
        known_at=DAY + timedelta(days=4),
    )

    semantics_authority.register_semantics(
        semantic_input_record=input1,
        recorded_at=DAY + timedelta(days=2),
        known_at=DAY + timedelta(days=2),
    )
    semantics_authority.register_semantics(
        semantic_input_record=input2,
        recorded_at=DAY + timedelta(days=4),
        known_at=DAY + timedelta(days=4),
    )

    class _NativeQuery:
        def overlapping_evidence(self, **_: object) -> tuple[FundamentalEvidenceRecord, ...]:
            return (ev1, ev2)

    class _ContractAuth:
        def strict_known_contract(self, **_: object) -> MethodologyEvidenceInputContract | None:
            return MethodologyEvidenceInputContract(
                contract_id="contract-1",
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

    # Caller supplies mismatching representation_continuity_proof_id
    c1_bad = AssemblyConstituent(
        record=ev1,
        shape_id="official-period-specific-v1",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis:fdv",
        pathway_id="pathway:fees",
        representation_continuity_proof_id="WRONG_PROOF_ID",
    )
    c2 = AssemblyConstituent(
        record=ev2,
        shape_id="official-period-specific-v1",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis:fdv",
        pathway_id="pathway:fees",
    )

    assembly_repo = AssembledEvidenceRepository(tmp_path / "assembly.sqlite")
    assembly_service = CanonicalEvidenceAssemblyService(
        repository=assembly_repo,
        native_evidence_query=_NativeQuery(),
        methodology_contract_authority=_ContractAuth(),
        evidence_shape_registry_authority=registry_auth,
        evidence_semantics_authority=semantics_authority,
    )

    with pytest.raises(CanonicalEvidenceAssemblyError, match="representation continuity proof mismatch"):
        assembly_service.assemble(
            constituents=(c1_bad, c2),
            accounting_window_start=DAY,
            accounting_window_end=DAY + timedelta(days=4),
            recorded_at=DAY + timedelta(days=6),
            replay_cutoff=DAY + timedelta(days=10),
            methodology_contract_id="contract-1",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="reg-v1",
        )


def test_policy_second_root_rejected(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
) -> None:
    semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=_rules(),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    with pytest.raises(EvidenceSemanticInputIntegrityError, match="root policy record already exists"):
        semantic_input_authority.persist_policy(
            version="2.0.0",
            rules=_rules(),
            effective_at=DAY,
            recorded_at=DAY + timedelta(days=1),
            known_at=DAY + timedelta(days=1),
        )


def test_policy_branching_correction_rejected(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
) -> None:
    p1 = semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=_rules(),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    semantic_input_authority.persist_policy(
        version="1.1.0",
        rules=_rules(),
        effective_at=DAY,
        recorded_at=DAY + timedelta(days=1),
        known_at=DAY + timedelta(days=1),
        supersedes_version="1.0.0",
        supersedes_record_id=p1.record_id,
        correction_reason="first correction",
    )
    with pytest.raises(EvidenceSemanticInputIntegrityError, match="branching correction lineage"):
        semantic_input_authority.persist_policy(
            version="1.2.0",
            rules=_rules(),
            effective_at=DAY,
            recorded_at=DAY + timedelta(days=2),
            known_at=DAY + timedelta(days=2),
            supersedes_version="1.0.0",
            supersedes_record_id=p1.record_id,
            correction_reason="branching correction",
        )


def test_semantics_authority_registration_and_strict_known_lookup(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    semantics_authority: CanonicalEvidenceSemanticsAuthority,
    value_capture_repo: SupplyAndValueCaptureRepository,
) -> None:
    policy = semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=_rules(),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    ev_record = _evidence_record("evidence:1")
    _persist_native_evidence(value_capture_repo, ev_record)

    input_rec = semantic_input_authority.derive_and_persist_input(
        evidence_record=ev_record,
        policy_snapshot=policy,
        recorded_at=DAY + timedelta(days=2),
        known_at=DAY + timedelta(days=2),
    )

    registered = semantics_authority.register_semantics(
        semantic_input_record=input_rec,
        recorded_at=DAY + timedelta(days=3),
        known_at=DAY + timedelta(days=3),
    )
    assert registered.evidence_record_id == "evidence:1"
    assert registered.evidence_semantic_input_record_id == input_rec.record_id

    semantics = semantics_authority.strict_known_semantics(
        evidence_record_id="evidence:1",
        evidence_record_version="1.0.0",
        known_by=DAY + timedelta(days=5),
    )
    assert semantics is not None
    assert semantics.evidence_record_id == "evidence:1"
    assert semantics.shape_id == "official-period-specific-v1"
    assert semantics.accounting_meaning == "period_specific"
    assert semantics.pathway_id == "pathway:fees"


def test_end_to_end_assembly_with_semantics_authority(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    semantics_authority: CanonicalEvidenceSemanticsAuthority,
    value_capture_repo: SupplyAndValueCaptureRepository,
    tmp_path: Path,
) -> None:
    # 1. Evidence Shape Registry
    registry_repo = tmp_path / "registry.sqlite"
    from hunter.evidence_assembly import EvidenceShapeRegistryRepository

    registry_auth = CanonicalEvidenceShapeRegistryAuthority(
        repository=EvidenceShapeRegistryRepository(registry_repo),
        application_root=tmp_path,
    )
    registry_auth.persist_registry(
        version="reg-v1",
        shapes=(
            EvidenceShape(
                shape_id="official-period-specific-v1",
                registry_version="reg-v1",
                evidence_type="official_disclosure",
                accounting_meaning="period_specific",
                cadence="interval",
                composition_operation="exact_sum",
                active=True,
            ),
        ),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )

    # 2. Semantic Input & Semantics
    policy = semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=_rules(),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    ev1 = _evidence_record("ev:1", start_day=0, duration=2)
    ev2 = _evidence_record("ev:2", start_day=2, duration=2)
    _persist_native_evidence(value_capture_repo, ev1)
    _persist_native_evidence(value_capture_repo, ev2)

    input1 = semantic_input_authority.derive_and_persist_input(
        evidence_record=ev1,
        policy_snapshot=policy,
        recorded_at=DAY + timedelta(days=2),
        known_at=DAY + timedelta(days=2),
    )
    input2 = semantic_input_authority.derive_and_persist_input(
        evidence_record=ev2,
        policy_snapshot=policy,
        recorded_at=DAY + timedelta(days=4),
        known_at=DAY + timedelta(days=4),
    )

    semantics_authority.register_semantics(
        semantic_input_record=input1,
        recorded_at=DAY + timedelta(days=2),
        known_at=DAY + timedelta(days=2),
    )
    semantics_authority.register_semantics(
        semantic_input_record=input2,
        recorded_at=DAY + timedelta(days=4),
        known_at=DAY + timedelta(days=4),
    )

    # 3. Fakes for NativeQuery & ContractAuth
    class _NativeQuery:
        def overlapping_evidence(self, **_: object) -> tuple[FundamentalEvidenceRecord, ...]:
            return (ev1, ev2)

    class _ContractAuth:
        def strict_known_contract(self, **_: object) -> MethodologyEvidenceInputContract | None:
            return MethodologyEvidenceInputContract(
                contract_id="contract-1",
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

    c1 = AssemblyConstituent(
        record=ev1,
        shape_id="official-period-specific-v1",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis:fdv",
        pathway_id="pathway:fees",
    )
    c2 = AssemblyConstituent(
        record=ev2,
        shape_id="official-period-specific-v1",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis:fdv",
        pathway_id="pathway:fees",
    )

    assembly_repo = AssembledEvidenceRepository(tmp_path / "assembly.sqlite")
    assembly_service = CanonicalEvidenceAssemblyService(
        repository=assembly_repo,
        native_evidence_query=_NativeQuery(),
        methodology_contract_authority=_ContractAuth(),
        evidence_shape_registry_authority=registry_auth,
        evidence_semantics_authority=semantics_authority,
    )

    assembled = assembly_service.assemble(
        constituents=(c1, c2),
        accounting_window_start=DAY,
        accounting_window_end=DAY + timedelta(days=4),
        recorded_at=DAY + timedelta(days=6),
        replay_cutoff=DAY + timedelta(days=10),
        methodology_contract_id="contract-1",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="reg-v1",
    )
    assert assembled is not None
    assert assembled.amount == "200"
    assert assembled.evidence_marker == "assembled"
