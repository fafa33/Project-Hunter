from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from hunter.evidence_assembly import (
    EVIDENCE_SEMANTICS_SCHEMA_VERSION,
    AssembledEvidenceRepository,
    AssemblyConstituent,
    AuthoritativeEvidenceSemanticsRecord,
    CanonicalEvidenceAssemblyError,
    CanonicalEvidenceAssemblyService,
    CanonicalEvidenceSemanticsAuthority,
    CanonicalEvidenceSemanticsAuthorityError,
    CanonicalEvidenceShapeRegistryAuthority,
    EvidenceSemanticsIntegrityError,
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


def _canonical_digest(content: dict[str, Any]) -> str:
    """Independent restatement of the canonical content-hash encoding used by the authorities."""

    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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


def test_equivalent_multiple_matching_rules_succeeds(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    value_capture_repo: SupplyAndValueCaptureRepository,
) -> None:
    equivalent_rules = (
        EvidenceSemanticInputRule(
            rule_id="rule-a",
            match_evidence_type="official_disclosure",
            shape_id="official-period-specific-v1",
            accounting_meaning="period_specific",
            supply_basis_id="supply-basis:fdv",
            pathway_id="pathway:fees",
            currency="USD",
            raw_unit="USD",
            semantic_dimensions={"tier": "primary"},
        ),
        EvidenceSemanticInputRule(
            rule_id="rule-b",
            match_evidence_type="official_disclosure",
            match_attribution_rule_id="pathway:fees",
            shape_id="official-period-specific-v1",
            accounting_meaning="period_specific",
            supply_basis_id="supply-basis:fdv",
            pathway_id="pathway:fees",
            currency="USD",
            raw_unit="USD",
            semantic_dimensions={"tier": "primary"},
        ),
    )
    policy = semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=equivalent_rules,
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
    assert input_rec is not None
    assert input_rec.matched_rule_id == "rule-a"


def test_tamper_detection_surfaces_integrity_error(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    semantic_input_repo: EvidenceSemanticInputRepository,
    value_capture_repo: SupplyAndValueCaptureRepository,
    tmp_path: Path,
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

    semantic_input_authority.derive_and_persist_input(
        evidence_record=ev_record,
        policy_snapshot=policy,
        recorded_at=DAY + timedelta(days=2),
        known_at=DAY + timedelta(days=2),
    )

    # Tamper with the persisted input record payload in SQLite
    import json
    import sqlite3

    with sqlite3.connect(semantic_input_repo.path) as conn:
        rows = conn.execute("SELECT id, payload FROM persistence_records").fetchall()
        assert rows
        for row_id, payload_str in rows:
            outer_payload = json.loads(payload_str)
            inner_payload = outer_payload.get("fields", {}).get("payload", {})
            if inner_payload.get("currency") == "USD":
                inner_payload["currency"] = "TAMPERED_CURRENCY"
                conn.execute(
                    "UPDATE persistence_records SET payload = ? WHERE id = ?", (json.dumps(outer_payload), row_id)
                )
        conn.commit()

    # CodeRabbit Finding 3 / 6: strict_known_input must fail closed with EvidenceSemanticInputIntegrityError
    with pytest.raises(EvidenceSemanticInputIntegrityError, match="content hash mismatch"):
        semantic_input_authority.strict_known_input(
            evidence_record_id="evidence:1",
            evidence_record_version="1.0.0",
            effective_as_of=DAY + timedelta(days=5),
            known_by=DAY + timedelta(days=5),
        )


def test_mutable_semantic_dimensions_alias_safety(
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

    # CodeRabbit Finding 1: Mutate original input_rec.semantic_dimensions
    input_rec.semantic_dimensions["tier"] = "MUTATED"

    # Registered semantics record must remain unchanged
    assert registered.semantic_dimensions == {"tier": "primary"}

    fetched = semantics_authority.strict_known_semantics(
        evidence_record_id="evidence:1",
        evidence_record_version="1.0.0",
        known_by=DAY + timedelta(days=5),
    )
    assert fetched is not None
    assert fetched.semantic_dimensions == {"tier": "primary"}


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


def test_policy_correction_re_derivation_lifecycle(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    value_capture_repo: SupplyAndValueCaptureRepository,
) -> None:
    # 1. Initial Policy v1.0.0
    policy_v1 = semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=_rules(),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    ev_record = _evidence_record("evidence:1")
    _persist_native_evidence(value_capture_repo, ev_record)

    # Derive initial input v1
    input_v1 = semantic_input_authority.derive_and_persist_input(
        evidence_record=ev_record,
        policy_snapshot=policy_v1,
        recorded_at=DAY + timedelta(days=2),
        known_at=DAY + timedelta(days=2),
    )

    # State A: Before policy correction => predecessor input resolves
    fetched_a = semantic_input_authority.strict_known_input(
        evidence_record_id="evidence:1",
        evidence_record_version="1.0.0",
        effective_as_of=DAY + timedelta(days=3),
        known_by=DAY + timedelta(days=3),
    )
    assert fetched_a is not None
    assert fetched_a.record_id == input_v1.record_id
    assert fetched_a.policy_record_id == policy_v1.record_id

    # 2. Corrected Policy v1.1.0 at DAY + 5
    corrected_rules = (
        EvidenceSemanticInputRule(
            rule_id="rule-official-fees-corrected",
            match_evidence_type="official_disclosure",
            match_attribution_rule_id="pathway:fees",
            shape_id="official-period-specific-v1",
            accounting_meaning="period_specific",
            supply_basis_id="supply-basis:fdv",
            pathway_id="pathway:fees",
            asserts_representation_continuity=True,
            currency="USD",
            raw_unit="USD",
            semantic_dimensions={"tier": "primary", "corrected": "true"},
        ),
    )
    policy_v2 = semantic_input_authority.persist_policy(
        version="1.1.0",
        rules=corrected_rules,
        effective_at=DAY,
        recorded_at=DAY + timedelta(days=5),
        known_at=DAY + timedelta(days=5),
        supersedes_version="1.0.0",
        supersedes_record_id=policy_v1.record_id,
        correction_reason="updated classification rules",
    )

    # State B: After policy correction (at DAY + 6) but BEFORE successor input derivation => explicit unavailable (None)
    fetched_b = semantic_input_authority.strict_known_input(
        evidence_record_id="evidence:1",
        evidence_record_version="1.0.0",
        effective_as_of=DAY + timedelta(days=6),
        known_by=DAY + timedelta(days=6),
    )
    assert fetched_b is None

    # 3. Derive Successor Input v2 under corrected Policy v1.1.0 at DAY + 7
    input_v2 = semantic_input_authority.derive_and_persist_input(
        evidence_record=ev_record,
        policy_snapshot=policy_v2,
        recorded_at=DAY + timedelta(days=7),
        known_at=DAY + timedelta(days=7),
        supersedes_record_id=input_v1.record_id,
        correction_reason="re-derivation under corrected policy v1.1.0",
    )
    assert input_v2.supersedes_record_id == input_v1.record_id
    assert input_v2.policy_record_id == policy_v2.record_id

    # State C: After successor input derivation (at DAY + 8) => successor input resolves
    fetched_c = semantic_input_authority.strict_known_input(
        evidence_record_id="evidence:1",
        evidence_record_version="1.0.0",
        effective_as_of=DAY + timedelta(days=8),
        known_by=DAY + timedelta(days=8),
    )
    assert fetched_c is not None
    assert fetched_c.record_id == input_v2.record_id
    assert fetched_c.policy_record_id == policy_v2.record_id
    assert fetched_c.semantic_dimensions == {"tier": "primary", "corrected": "true"}

    # State D: Historical cutoff before correction (e.g., cutoff = DAY + 3) still returns predecessor input_v1
    fetched_d = semantic_input_authority.strict_known_input(
        evidence_record_id="evidence:1",
        evidence_record_version="1.0.0",
        effective_as_of=DAY + timedelta(days=3),
        known_by=DAY + timedelta(days=3),
    )
    assert fetched_d is not None
    assert fetched_d.record_id == input_v1.record_id
    assert fetched_d.policy_record_id == policy_v1.record_id


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
    assert semantics.content_hash == registered.content_hash
    assert semantics.evidence_semantic_input_record_id == input_rec.record_id
    assert semantics.evidence_semantic_input_record_version == input_rec.semantic_version
    assert semantics.evidence_semantic_input_record_content_hash == input_rec.content_hash
    assert semantics.policy_snapshot_id == policy.record_id
    assert semantics.policy_snapshot_version == policy.version
    assert semantics.policy_snapshot_content_hash == policy.content_hash
    assert semantics.representation_continuity_asserted is True
    assert semantics.representation_continuity_proof_id == policy.record_id
    assert semantics.semantic_dimensions == {"tier": "primary"}

    # Exact content-hash verification.
    #
    # `semantics.content_hash == registered.content_hash` alone is a round-trip identity check:
    # both sides come from the same computation, so it holds even if that computation ignores the
    # record's content entirely. Mutation-verified: collapsing _semantics_content_hash() to a
    # content-independent constant left the whole suite green. That is defect class MA-003 --
    # an invariant assertion that cannot fail for the reason it is named for.
    #
    # The canonical hash contract is therefore restated here independently: sha256 over the
    # compact, key-sorted JSON encoding of every content-bearing field, with record_id and
    # content_hash excluded, and record_id derived as sha256("<logical_id>:<content_hash>").
    # Every expected value is stated from the upstream authority record or the fixture, never
    # read back from the record under test, so this is simultaneously the complete-provenance
    # assertion: a single wrong or omitted provenance field changes the digest.
    expected_content = {
        "logical_id": "semantics:evidence:1:1.0.0",
        "schema_version": EVIDENCE_SEMANTICS_SCHEMA_VERSION,
        "semantic_version": "1.0.0",
        "evidence_record_id": "evidence:1",
        "evidence_record_version": "1.0.0",
        "evidence_semantic_input_record_id": input_rec.record_id,
        "evidence_semantic_input_record_version": input_rec.semantic_version,
        "evidence_semantic_input_record_content_hash": input_rec.content_hash,
        "policy_snapshot_id": policy.record_id,
        "policy_snapshot_version": policy.version,
        "policy_snapshot_content_hash": policy.content_hash,
        "shape_id": "official-period-specific-v1",
        "currency": "USD",
        "raw_unit": "USD",
        "accounting_meaning": "period_specific",
        "supply_basis_id": "supply-basis:fdv",
        "pathway_id": "pathway:fees",
        "representation_continuity_asserted": True,
        "representation_continuity_proof_id": policy.record_id,
        "semantic_dimensions": {"tier": "primary"},
        "effective_at": (DAY + timedelta(days=2)).isoformat(),
        "recorded_at": (DAY + timedelta(days=3)).isoformat(),
        "known_at": (DAY + timedelta(days=3)).isoformat(),
        "quality_state": "accepted",
        "conflict_state": "none",
        "authorizing_adr_reference": "ADR-0028",
        "authorized_by": "canonical-evidence-semantics-authority-v1",
        "supersedes_record_id": None,
        "correction_reason": "",
    }
    # A field added to the record without being covered here would otherwise silently escape the
    # provenance assertion above.
    assert set(expected_content) == {field.name for field in fields(AuthoritativeEvidenceSemanticsRecord)} - {
        "record_id",
        "content_hash",
    }

    expected_content_hash = _canonical_digest(expected_content)
    assert registered.content_hash == expected_content_hash
    assert (
        registered.record_id
        == hashlib.sha256(f"semantics:evidence:1:1.0.0:{expected_content_hash}".encode()).hexdigest()
    )
    assert semantics.content_hash == expected_content_hash


def _tamper_registered_semantics(semantics_repo: EvidenceSemanticsRepository, *, rebind_content_hash: bool) -> None:
    """Rewrite the persisted semantics payload behind the authority's write boundary.

    With `rebind_content_hash` the tampered payload also carries a recomputed canonical digest, so
    only the record_id binding remains stale. Without it the stored digest is left untouched.
    """

    import sqlite3

    with sqlite3.connect(semantics_repo.path) as conn:
        rows = conn.execute("SELECT id, payload FROM persistence_records").fetchall()
        assert rows
        tampered = 0
        for row_id, payload_str in rows:
            outer_payload = json.loads(payload_str)
            inner_payload = outer_payload.get("fields", {}).get("payload", {})
            if inner_payload.get("pathway_id") != "pathway:fees":
                continue
            inner_payload["pathway_id"] = "pathway:tampered"
            if rebind_content_hash:
                inner_payload["content_hash"] = _canonical_digest(
                    {key: value for key, value in inner_payload.items() if key not in {"record_id", "content_hash"}}
                )
            conn.execute("UPDATE persistence_records SET payload = ? WHERE id = ?", (json.dumps(outer_payload), row_id))
            tampered += 1
        conn.commit()
    assert tampered == 1


def _register_semantics_for_tamper_tests(
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
    semantics_authority.register_semantics(
        semantic_input_record=input_rec,
        recorded_at=DAY + timedelta(days=3),
        known_at=DAY + timedelta(days=3),
    )


def test_registered_semantics_tamper_surfaces_integrity_error(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    semantics_authority: CanonicalEvidenceSemanticsAuthority,
    semantics_repo: EvidenceSemanticsRepository,
    value_capture_repo: SupplyAndValueCaptureRepository,
) -> None:
    """A registered semantics record edited in storage must fail closed, not be served."""

    _register_semantics_for_tamper_tests(semantic_input_authority, semantics_authority, value_capture_repo)

    _tamper_registered_semantics(semantics_repo, rebind_content_hash=False)

    with pytest.raises(EvidenceSemanticsIntegrityError, match="content hash mismatch"):
        semantics_authority.strict_known_semantics(
            evidence_record_id="evidence:1",
            evidence_record_version="1.0.0",
            known_by=DAY + timedelta(days=5),
        )


def test_registered_semantics_tamper_with_rebound_content_hash_surfaces_integrity_error(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    semantics_authority: CanonicalEvidenceSemanticsAuthority,
    semantics_repo: EvidenceSemanticsRepository,
    value_capture_repo: SupplyAndValueCaptureRepository,
) -> None:
    """Recomputing the digest over tampered content must not launder the record.

    The record_id is itself derived from the content hash, so an attacker who can recompute the
    digest still cannot produce a self-consistent record without also restating the identity.
    """

    _register_semantics_for_tamper_tests(semantic_input_authority, semantics_authority, value_capture_repo)

    _tamper_registered_semantics(semantics_repo, rebind_content_hash=True)

    with pytest.raises(EvidenceSemanticsIntegrityError, match="record_id mismatch"):
        semantics_authority.strict_known_semantics(
            evidence_record_id="evidence:1",
            evidence_record_version="1.0.0",
            known_by=DAY + timedelta(days=5),
        )


def test_semantics_lookup_fails_closed_across_policy_correction_re_derivation(
    semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
    semantics_authority: CanonicalEvidenceSemanticsAuthority,
    value_capture_repo: SupplyAndValueCaptureRepository,
) -> None:
    """The re-derivation lifecycle must hold at the consumer-facing semantics boundary too.

    test_policy_correction_re_derivation_lifecycle establishes the behaviour one layer down, on
    strict_known_input. What consumers actually read is strict_known_semantics, and a registered
    semantics record outlives the policy correction that invalidated its derivation: without the
    upstream strict-known gate it would keep serving semantics derived under the superseded policy.
    """

    policy_v1 = semantic_input_authority.persist_policy(
        version="1.0.0",
        rules=_rules(),
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
    )
    ev_record = _evidence_record("evidence:1")
    _persist_native_evidence(value_capture_repo, ev_record)

    input_v1 = semantic_input_authority.derive_and_persist_input(
        evidence_record=ev_record,
        policy_snapshot=policy_v1,
        recorded_at=DAY + timedelta(days=2),
        known_at=DAY + timedelta(days=2),
    )
    semantics_v1 = semantics_authority.register_semantics(
        semantic_input_record=input_v1,
        recorded_at=DAY + timedelta(days=3),
        known_at=DAY + timedelta(days=3),
    )

    # State A: before the policy correction the registered semantics resolve.
    state_a = semantics_authority.strict_known_semantics(
        evidence_record_id="evidence:1",
        evidence_record_version="1.0.0",
        known_by=DAY + timedelta(days=4),
    )
    assert state_a is not None
    assert state_a.content_hash == semantics_v1.content_hash
    assert state_a.policy_snapshot_id == policy_v1.record_id
    assert state_a.semantic_dimensions == {"tier": "primary"}

    corrected_rules = (
        EvidenceSemanticInputRule(
            rule_id="rule-official-fees-corrected",
            match_evidence_type="official_disclosure",
            match_attribution_rule_id="pathway:fees",
            shape_id="official-period-specific-v1",
            accounting_meaning="period_specific",
            supply_basis_id="supply-basis:fdv",
            pathway_id="pathway:fees",
            asserts_representation_continuity=True,
            currency="USD",
            raw_unit="USD",
            semantic_dimensions={"tier": "primary", "corrected": "true"},
        ),
    )
    policy_v2 = semantic_input_authority.persist_policy(
        version="1.1.0",
        rules=corrected_rules,
        effective_at=DAY,
        recorded_at=DAY + timedelta(days=5),
        known_at=DAY + timedelta(days=5),
        supersedes_version="1.0.0",
        supersedes_record_id=policy_v1.record_id,
        correction_reason="updated classification rules",
    )

    # State B: after the correction and before re-derivation, semantics are explicitly unavailable
    # rather than silently served from the superseded derivation.
    assert (
        semantics_authority.strict_known_semantics(
            evidence_record_id="evidence:1",
            evidence_record_version="1.0.0",
            known_by=DAY + timedelta(days=6),
        )
        is None
    )

    input_v2 = semantic_input_authority.derive_and_persist_input(
        evidence_record=ev_record,
        policy_snapshot=policy_v2,
        recorded_at=DAY + timedelta(days=7),
        known_at=DAY + timedelta(days=7),
        supersedes_record_id=input_v1.record_id,
        correction_reason="re-derivation under corrected policy v1.1.0",
    )

    # The re-derived input alone does not restore the boundary: semantics must be re-registered
    # against it, otherwise the stale registration and the new input disagree and lookup stays closed.
    assert (
        semantics_authority.strict_known_semantics(
            evidence_record_id="evidence:1",
            evidence_record_version="1.0.0",
            known_by=DAY + timedelta(days=7),
        )
        is None
    )

    semantics_v2 = semantics_authority.register_semantics(
        semantic_input_record=input_v2,
        recorded_at=DAY + timedelta(days=8),
        known_at=DAY + timedelta(days=8),
        supersedes_record_id=semantics_v1.record_id,
        correction_reason="re-registration under corrected policy v1.1.0",
    )

    # State C: the successor registration resolves, carrying the corrected policy provenance.
    state_c = semantics_authority.strict_known_semantics(
        evidence_record_id="evidence:1",
        evidence_record_version="1.0.0",
        known_by=DAY + timedelta(days=9),
    )
    assert state_c is not None
    assert state_c.content_hash == semantics_v2.content_hash
    assert state_c.content_hash != semantics_v1.content_hash
    assert state_c.policy_snapshot_id == policy_v2.record_id
    assert state_c.policy_snapshot_version == "1.1.0"
    assert state_c.policy_snapshot_content_hash == policy_v2.content_hash
    assert state_c.evidence_semantic_input_record_id == input_v2.record_id
    assert state_c.evidence_semantic_input_record_content_hash == input_v2.content_hash
    assert state_c.semantic_dimensions == {"tier": "primary", "corrected": "true"}

    # State D: a historical cutoff before the correction still replays the predecessor semantics.
    state_d = semantics_authority.strict_known_semantics(
        evidence_record_id="evidence:1",
        evidence_record_version="1.0.0",
        known_by=DAY + timedelta(days=4),
    )
    assert state_d is not None
    assert state_d.content_hash == semantics_v1.content_hash
    assert state_d.policy_snapshot_id == policy_v1.record_id
    assert state_d.evidence_semantic_input_record_id == input_v1.record_id
    assert state_d.semantic_dimensions == {"tier": "primary"}


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
