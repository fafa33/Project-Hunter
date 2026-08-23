from __future__ import annotations

import ast
import hashlib
import json
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hunter.evidence_assembly.composition import (
    ProductionEvidenceAssemblyCompositionError,
    build_production_evidence_assembly_service,
)
from hunter.evidence_assembly.models import (
    ASSEMBLY_RULE_VERSION,
    AssemblyConstituent,
    EvidenceShape,
)
from hunter.evidence_assembly.registry import (
    CanonicalEvidenceShapeRegistryAuthority,
    EvidenceShapeRegistryRepository,
)
from hunter.evidence_assembly.repository import AssembledEvidenceRepository
from hunter.evidence_assembly.semantics import (
    CanonicalEvidenceSemanticsAuthority,
    EvidenceSemanticsRepository,
)
from hunter.evidence_assembly.service import (
    CanonicalEvidenceAssemblyError,
    CanonicalEvidenceAssemblyService,
)
from hunter.evidence_semantic_inputs import (
    CanonicalEvidenceSemanticInputAuthority,
    EvidenceSemanticInputRepository,
    EvidenceSemanticInputRule,
)
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_sqlite_engine
from hunter.valuation_methodology.repository import ValuationMethodologyRepository
from hunter.valuation_methodology.service import CanonicalValuationMethodologyAuthority
from hunter.value_capture.models import EconomicClaimIdentity, FundamentalEvidenceRecord
from hunter.value_capture.providers import ValueCaptureVerificationKeyRegistry
from hunter.value_capture.registry import ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository, record_snapshot
from hunter.value_capture.service import SupplyAndValueCaptureService


def _dt(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _json_safe(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _logical_id(record: FundamentalEvidenceRecord) -> str:
    identity = record.identity
    category = f"evidence:{record.evidence_type}:{record.source_reference}"
    raw = "|".join(
        (
            identity.entity_id,
            identity.economic_claim_id,
            identity.asset_id,
            identity.representation_id,
            identity.token_id,
            identity.chain,
            identity.contract_address,
            category,
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _content_hash(record: FundamentalEvidenceRecord, *, logical_id: str) -> str:
    payload = asdict(record)
    payload["record_id"] = ""
    payload["logical_id"] = logical_id
    payload["content_hash"] = ""
    raw = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _create_and_persist_evidence(
    repo: SupplyAndValueCaptureRepository,
    *,
    identity: EconomicClaimIdentity,
    accounting_period_start: datetime,
    accounting_period_end: datetime,
    amount: str,
    unit: str = "USD",
    source_reference: str = "ref-1",
    supersedes_record_id: str | None = None,
    correction_reason: str = "",
) -> FundamentalEvidenceRecord:
    rec = FundamentalEvidenceRecord(
        record_id="pending",
        logical_id="pending",
        schema_version="supply-value-capture-v3.5.0",
        semantic_version="1.0.0",
        identity=identity,
        evidence_type="official_disclosure",
        source_id="source-1",
        source_authority_tier="tier-1",
        source_reference=source_reference,
        parser_version="1.0.0",
        extracted_claim="revenue disclosure",
        amount=amount,
        unit=unit,
        accounting_period_start=accounting_period_start,
        accounting_period_end=accounting_period_end,
        attribution_rule_id="pathway-test",
        source_methodology="methodology-1",
        source_record_id="src-rec-1",
        source_record_version="1.0.0",
        entity_link_confidence="1.0",
        evidence_confidence="0.95",
        uncertainty="0.05",
        effective_at=accounting_period_end,
        recorded_at=accounting_period_end,
        known_at=accounting_period_end,
        raw_content_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        quality_state="accepted",
        conflict_state="none",
        supersedes_record_id=supersedes_record_id,
        correction_reason=correction_reason,
        acquisition_id="acq-1",
    )
    logical_id = _logical_id(rec)
    content_hash = _content_hash(rec, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    normalized = replace(rec, logical_id=logical_id, content_hash=content_hash, record_id=record_id)

    engine = create_sqlite_engine(repo.path)
    session = SessionFactory(engine).create()
    try:
        RepositoryFactory(session).snapshots().save(record_snapshot(normalized))
        session.commit()
    finally:
        session.close()
        engine.dispose()
    return normalized


def _seed_production_environment(
    *,
    monkeypatch: pytest.MonkeyPatch,
    db_path: Path,
    app_root: Path,
    entity_id: str = "entity-test",
    economic_claim_id: str = "claim-test",
    representation_id: str = "rep-test",
    pathway_id: str = "pathway-test",
    supply_basis_id: str = "supply-basis-test",
    currency: str = "USD",
    unit: str = "USD",
    start_1: datetime | None = None,
    end_1: datetime | None = None,
    start_2: datetime | None = None,
    end_2: datetime | None = None,
    recorded_at: datetime | None = None,
    known_at: datetime | None = None,
    methodology_accepts_assembled_evidence: bool = True,
    contract_accepts_assembled_evidence: bool = True,
    contract_id: str = "contract-test-1",
    contract_version: str = "1.0.0",
    registry_version: str = "1.0.0",
    policy_version: str = "1.0.0",
    amount_1: str = "100.00",
    amount_2: str = "150.00",
):
    if start_1 is None:
        start_1 = _dt(2026, 1, 1)
    if end_1 is None:
        end_1 = _dt(2026, 2, 1)
    if start_2 is None:
        start_2 = _dt(2026, 2, 1)
    if end_2 is None:
        end_2 = _dt(2026, 3, 1)
    if recorded_at is None:
        recorded_at = _dt(2026, 3, 2)
    if known_at is None:
        known_at = _dt(2026, 3, 2)

    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(app_root))
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_KEY_ID", "key-test-1")
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_KEY_SECRET", "0123456789abcdef0123456789abcdef")

    # 1. Native Value Capture
    vc_repo = SupplyAndValueCaptureRepository(db_path)

    identity = EconomicClaimIdentity(
        entity_id=entity_id,
        economic_claim_id=economic_claim_id,
        asset_id="asset-1",
        token_id="token-1",
        chain="ethereum",
        contract_address="0x123",
        representation_id=representation_id,
    )

    ev1 = _create_and_persist_evidence(
        vc_repo,
        identity=identity,
        accounting_period_start=start_1,
        accounting_period_end=end_1,
        amount=amount_1,
        unit=unit,
        source_reference="ref-1",
    )

    ev2 = _create_and_persist_evidence(
        vc_repo,
        identity=identity,
        accounting_period_start=start_2,
        accounting_period_end=end_2,
        amount=amount_2,
        unit=unit,
        source_reference="ref-2",
    )

    # 2. Methodology Authority
    meth_repo = ValuationMethodologyRepository(db_path)
    meth_auth = CanonicalValuationMethodologyAuthority(repository=meth_repo, application_root=app_root)

    meth_snapshot = meth_auth.persist_methodology(
        entity_class_criteria_id="criteria-1",
        entity_class_criteria_version="1.0.0",
        currency=currency,
        discount_rate_policy_id="discount-1",
        discount_rate_policy_version="1.0.0",
        sensitivity_policy_id="sens-1",
        sensitivity_policy_version="1.0.0",
        supply_basis_selection_rule="rule-1",
        effective_at=start_1,
        recorded_at=recorded_at,
        known_at=known_at,
        accepts_assembled_evidence=methodology_accepts_assembled_evidence,
    )

    contract = meth_auth.persist_contract(
        contract_id=contract_id,
        contract_version=contract_version,
        methodology_logical_id=meth_snapshot.logical_id,
        accepts_assembled_evidence=contract_accepts_assembled_evidence,
        accepted_shape_ids=("monthly-revenue-shape",),
        accepted_assembly_rule_versions=(ASSEMBLY_RULE_VERSION,),
        accounting_window_start=start_1,
        accounting_window_end=end_2,
        entity_id=entity_id,
        representation_id=representation_id,
        value_capture_pathway_id=pathway_id,
        currency=currency,
        unit=unit,
        effective_at=start_1,
        recorded_at=recorded_at,
        known_at=known_at,
    )

    # 3. Shape Registry Authority
    reg_repo = EvidenceShapeRegistryRepository(db_path)
    reg_auth = CanonicalEvidenceShapeRegistryAuthority(repository=reg_repo, application_root=app_root)

    shape = EvidenceShape(
        shape_id="monthly-revenue-shape",
        evidence_type="official_disclosure",
        accounting_meaning="period_specific",
        composition_operation="exact_sum",
        cadence="monthly",
        active=True,
        registry_version=registry_version,
    )

    registry = reg_auth.persist_registry(
        version=registry_version,
        shapes=(shape,),
        effective_at=start_1,
        recorded_at=recorded_at,
        known_at=known_at,
    )

    # 4. Semantic Input Authority
    si_repo = EvidenceSemanticInputRepository(db_path)
    si_auth = CanonicalEvidenceSemanticInputAuthority(
        repository=si_repo, value_capture_repository=vc_repo, application_root=app_root
    )

    rule = EvidenceSemanticInputRule(
        rule_id="rule-revenue-1",
        match_evidence_type="official_disclosure",
        shape_id="monthly-revenue-shape",
        accounting_meaning="period_specific",
        supply_basis_id=supply_basis_id,
        pathway_id=pathway_id,
        asserts_representation_continuity=False,
        currency=currency,
        raw_unit=unit,
    )

    policy = si_auth.persist_policy(
        version=policy_version,
        rules=(rule,),
        effective_at=start_1,
        recorded_at=recorded_at,
        known_at=known_at,
    )

    si_record_1 = si_auth.derive_and_persist_input(
        evidence_record=ev1,
        policy_snapshot=policy,
        recorded_at=recorded_at,
        known_at=known_at,
    )

    si_record_2 = si_auth.derive_and_persist_input(
        evidence_record=ev2,
        policy_snapshot=policy,
        recorded_at=recorded_at,
        known_at=known_at,
    )

    # 5. Evidence Semantics Authority
    sem_repo = EvidenceSemanticsRepository(db_path)
    sem_auth = CanonicalEvidenceSemanticsAuthority(
        semantic_input_authority=si_auth, repository=sem_repo, application_root=app_root
    )

    sem_rec_1 = sem_auth.register_semantics(
        semantic_input_record=si_record_1,
        recorded_at=recorded_at,
        known_at=known_at,
    )

    sem_rec_2 = sem_auth.register_semantics(
        semantic_input_record=si_record_2,
        recorded_at=recorded_at,
        known_at=known_at,
    )

    vc_service = SupplyAndValueCaptureService(
        repository=vc_repo,
        registry=ValueCaptureSourceRegistry(sources=()),
        verification_keys=ValueCaptureVerificationKeyRegistry(keys={"key-test-1": b"0" * 32}),
    )

    return {
        "ev1": ev1,
        "ev2": ev2,
        "contract": contract,
        "registry": registry,
        "policy": policy,
        "si_1": si_record_1,
        "si_2": si_record_2,
        "sem_1": sem_rec_1,
        "sem_2": sem_rec_2,
        "vc_repo": vc_repo,
        "meth_repo": meth_repo,
        "reg_repo": reg_repo,
        "sem_repo": sem_repo,
        "vc_service": vc_service,
        "meth_auth": meth_auth,
        "reg_auth": reg_auth,
        "si_auth": si_auth,
        "sem_auth": sem_auth,
    }


# --- PROOF 1 & 13: Production Construction & Verification Key Enforcement ---


def test_proof_1_and_13_production_construction(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(app_root))
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_KEY_ID", "key-test-1")
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_KEY_SECRET", "0123456789abcdef0123456789abcdef")

    service = build_production_evidence_assembly_service(
        db_path=db_path,
        application_root=app_root,
    )

    assert isinstance(service, CanonicalEvidenceAssemblyService)
    assert isinstance(service.repository, AssembledEvidenceRepository)
    assert isinstance(service.native_evidence_query, SupplyAndValueCaptureService)
    assert isinstance(service.methodology_contract_authority, CanonicalValuationMethodologyAuthority)
    assert isinstance(service.evidence_shape_registry_authority, CanonicalEvidenceShapeRegistryAuthority)
    assert isinstance(service.evidence_semantics_authority, CanonicalEvidenceSemanticsAuthority)


def test_production_composition_fails_closed_when_key_unconfigured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app_root = tmp_path
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(app_root))
    monkeypatch.delenv("HUNTER_VALUE_CAPTURE_KEY_ID", raising=False)
    monkeypatch.delenv("HUNTER_VALUE_CAPTURE_KEY_SECRET", raising=False)

    with pytest.raises(
        ProductionEvidenceAssemblyCompositionError,
        match="production Value Capture verification keys require",
    ):
        build_production_evidence_assembly_service(application_root=app_root)


def test_production_composition_fails_closed_when_key_secret_too_short(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app_root = tmp_path
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(app_root))
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_KEY_ID", "key-test-1")
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_KEY_SECRET", "short_secret")

    with pytest.raises(
        ProductionEvidenceAssemblyCompositionError,
        match="must be at least 32 bytes",
    ):
        build_production_evidence_assembly_service(application_root=app_root)


# --- PROOF 2: Methodology Strict-Known Available / Unavailable ---


def test_proof_2_methodology_strict_known_availability(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    seeded = _seed_production_environment(
        monkeypatch=monkeypatch,
        db_path=db_path,
        app_root=app_root,
        methodology_accepts_assembled_evidence=True,
        contract_accepts_assembled_evidence=True,
    )

    service = build_production_evidence_assembly_service(db_path=db_path, application_root=app_root)

    constituent_1 = AssemblyConstituent(
        record=seeded["ev1"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )
    constituent_2 = AssemblyConstituent(
        record=seeded["ev2"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )

    record = service.assemble(
        constituents=(constituent_1, constituent_2),
        accounting_window_start=_dt(2026, 1, 1),
        accounting_window_end=_dt(2026, 3, 1),
        recorded_at=_dt(2026, 3, 2),
        replay_cutoff=_dt(2026, 3, 2),
        methodology_contract_id="contract-test-1",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="1.0.0",
    )
    assert record.record_id.startswith("assembled-evidence:")


def test_proof_2_methodology_contract_rejects_assembled_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Methodology snapshot allows assembled evidence, but contract rejects it."""
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    seeded = _seed_production_environment(
        monkeypatch=monkeypatch,
        db_path=db_path,
        app_root=app_root,
        methodology_accepts_assembled_evidence=True,  # Snapshot allows!
        contract_accepts_assembled_evidence=False,  # Contract rejects!
    )

    service = build_production_evidence_assembly_service(db_path=db_path, application_root=app_root)

    constituent_1 = AssemblyConstituent(
        record=seeded["ev1"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )
    constituent_2 = AssemblyConstituent(
        record=seeded["ev2"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )

    with pytest.raises(
        CanonicalEvidenceAssemblyError,
        match="methodology contract has not opted into assembled evidence",
    ):
        service.assemble(
            constituents=(constituent_1, constituent_2),
            accounting_window_start=_dt(2026, 1, 1),
            accounting_window_end=_dt(2026, 3, 1),
            recorded_at=_dt(2026, 3, 2),
            replay_cutoff=_dt(2026, 3, 2),
            methodology_contract_id="contract-test-1",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="1.0.0",
        )


# --- PROOF 3: Shape Registry Strict-Known Available / Unavailable ---


def test_proof_3_shape_registry_strict_known_availability(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    seeded = _seed_production_environment(monkeypatch=monkeypatch, db_path=db_path, app_root=app_root)

    service = build_production_evidence_assembly_service(db_path=db_path, application_root=app_root)

    constituent_1 = AssemblyConstituent(
        record=seeded["ev1"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )
    constituent_2 = AssemblyConstituent(
        record=seeded["ev2"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )

    with pytest.raises(CanonicalEvidenceAssemblyError, match="no exact strict-known Evidence Shape Registry snapshot"):
        service.assemble(
            constituents=(constituent_1, constituent_2),
            accounting_window_start=_dt(2026, 1, 1),
            accounting_window_end=_dt(2026, 3, 1),
            recorded_at=_dt(2026, 3, 2),
            replay_cutoff=_dt(2026, 3, 2),
            methodology_contract_id="contract-test-1",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="9.9.9",
        )


# --- PROOF 4: Semantics Strict-Known Available / Unavailable ---


def test_proof_4_semantics_strict_known_availability(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    seeded = _seed_production_environment(monkeypatch=monkeypatch, db_path=db_path, app_root=app_root)

    service = build_production_evidence_assembly_service(db_path=db_path, application_root=app_root)

    fake_ev = FundamentalEvidenceRecord(
        record_id="fake-ev-999",
        logical_id="fake-logical",
        schema_version="1.0.0",
        semantic_version="1.0.0",
        identity=seeded["ev1"].identity,
        evidence_type="official_disclosure",
        source_id="source-1",
        source_authority_tier="tier-1",
        source_reference="ref-fake",
        parser_version="1.0.0",
        extracted_claim="revenue",
        amount="100.00",
        unit="USD",
        accounting_period_start=_dt(2026, 1, 1),
        accounting_period_end=_dt(2026, 2, 1),
        attribution_rule_id="pathway-test",
        source_methodology="methodology-1",
        source_record_id="src-fake",
        source_record_version="1.0.0",
        entity_link_confidence="1.0",
        evidence_confidence="0.95",
        uncertainty="0.05",
        effective_at=_dt(2026, 2, 1),
        recorded_at=_dt(2026, 2, 1),
        known_at=_dt(2026, 2, 1),
        raw_content_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        quality_state="accepted",
        conflict_state="none",
        acquisition_id="acq-fake",
    )

    constituent_fake = AssemblyConstituent(
        record=fake_ev,
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )
    constituent_2 = AssemblyConstituent(
        record=seeded["ev2"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )

    with pytest.raises(CanonicalEvidenceAssemblyError, match="no exact strict-known authoritative evidence semantics"):
        service.assemble(
            constituents=(constituent_fake, constituent_2),
            accounting_window_start=_dt(2026, 1, 1),
            accounting_window_end=_dt(2026, 3, 1),
            recorded_at=_dt(2026, 3, 2),
            replay_cutoff=_dt(2026, 3, 2),
            methodology_contract_id="contract-test-1",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="1.0.0",
        )


# --- PROOF 5: Assembly Write / Read ---


def test_proof_5_assembly_write_and_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    seeded = _seed_production_environment(monkeypatch=monkeypatch, db_path=db_path, app_root=app_root)

    service = build_production_evidence_assembly_service(db_path=db_path, application_root=app_root)

    c1 = AssemblyConstituent(
        record=seeded["ev1"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )
    c2 = AssemblyConstituent(
        record=seeded["ev2"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )

    assembled = service.assemble(
        constituents=(c1, c2),
        accounting_window_start=_dt(2026, 1, 1),
        accounting_window_end=_dt(2026, 3, 1),
        recorded_at=_dt(2026, 3, 2),
        replay_cutoff=_dt(2026, 3, 2),
        methodology_contract_id="contract-test-1",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="1.0.0",
    )

    assert assembled.amount == "250.00"
    assert assembled.evidence_marker == "assembled"

    read_back = service.strict_known(
        logical_id=assembled.logical_id,
        effective_as_of=_dt(2026, 3, 1),
        known_by=_dt(2026, 3, 2),
    )

    assert read_back is not None
    assert read_back.record_id == assembled.record_id
    assert read_back.amount == "250.00"


# --- PROOF 6: Correction Behavior & Lineage Protection ---


def test_proof_6_correction_behavior_and_branching_prohibition(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    seeded = _seed_production_environment(monkeypatch=monkeypatch, db_path=db_path, app_root=app_root)

    service = build_production_evidence_assembly_service(db_path=db_path, application_root=app_root)

    c1 = AssemblyConstituent(
        record=seeded["ev1"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )
    c2 = AssemblyConstituent(
        record=seeded["ev2"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )

    initial = service.assemble(
        constituents=(c1, c2),
        accounting_window_start=_dt(2026, 1, 1),
        accounting_window_end=_dt(2026, 3, 1),
        recorded_at=_dt(2026, 3, 2),
        replay_cutoff=_dt(2026, 3, 2),
        methodology_contract_id="contract-test-1",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="1.0.0",
    )

    vc_repo = seeded["vc_repo"]
    ev2_corrected = _create_and_persist_evidence(
        vc_repo,
        identity=seeded["ev1"].identity,
        accounting_period_start=_dt(2026, 2, 1),
        accounting_period_end=_dt(2026, 3, 1),
        amount="160.00",
        unit="USD",
        source_reference="ref-2",
        supersedes_record_id=seeded["ev2"].record_id,
        correction_reason="routine native evidence correction",
    )

    si_record_2_corr = seeded["si_auth"].derive_and_persist_input(
        evidence_record=ev2_corrected,
        policy_snapshot=seeded["policy"],
        recorded_at=_dt(2026, 3, 3),
        known_at=_dt(2026, 3, 3),
    )

    seeded["sem_auth"].register_semantics(
        semantic_input_record=si_record_2_corr,
        recorded_at=_dt(2026, 3, 3),
        known_at=_dt(2026, 3, 3),
    )

    c2_corr = AssemblyConstituent(
        record=ev2_corrected,
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )

    corrected = service.assemble(
        constituents=(c1, c2_corr),
        accounting_window_start=_dt(2026, 1, 1),
        accounting_window_end=_dt(2026, 3, 1),
        recorded_at=_dt(2026, 3, 3),
        replay_cutoff=_dt(2026, 3, 3),
        methodology_contract_id="contract-test-1",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="1.0.0",
        supersedes_record_id=initial.record_id,
        correction_reason="routine policy correction",
    )

    assert corrected.supersedes_record_id == initial.record_id
    assert service.is_superseded(initial.record_id)
    assert not service.is_superseded(corrected.record_id)

    with pytest.raises(CanonicalEvidenceAssemblyError, match="branching correction lineage is prohibited"):
        service.assemble(
            constituents=(c1, c2_corr),
            accounting_window_start=_dt(2026, 1, 1),
            accounting_window_end=_dt(2026, 3, 1),
            recorded_at=_dt(2026, 3, 4),
            replay_cutoff=_dt(2026, 3, 4),
            methodology_contract_id="contract-test-1",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="1.0.0",
            supersedes_record_id=initial.record_id,
            correction_reason="competing correction attempt",
        )


# --- PROOF 7: Conflict Behavior & Query ---


def test_proof_7_conflict_behavior_qualifying_native_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    seeded = _seed_production_environment(monkeypatch=monkeypatch, db_path=db_path, app_root=app_root)

    vc_repo = seeded["vc_repo"]

    full_ev = _create_and_persist_evidence(
        vc_repo,
        identity=seeded["ev1"].identity,
        accounting_period_start=_dt(2026, 1, 1),
        accounting_period_end=_dt(2026, 3, 1),
        amount="250.00",
        unit="USD",
        source_reference="ref-full",
    )

    si_rec_full = seeded["si_auth"].derive_and_persist_input(
        evidence_record=full_ev,
        policy_snapshot=seeded["policy"],
        recorded_at=_dt(2026, 3, 2),
        known_at=_dt(2026, 3, 2),
    )
    seeded["sem_auth"].register_semantics(
        semantic_input_record=si_rec_full,
        recorded_at=_dt(2026, 3, 2),
        known_at=_dt(2026, 3, 2),
    )

    service = build_production_evidence_assembly_service(db_path=db_path, application_root=app_root)

    c1 = AssemblyConstituent(
        record=seeded["ev1"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )
    c2 = AssemblyConstituent(
        record=seeded["ev2"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )

    with pytest.raises(CanonicalEvidenceAssemblyError, match="qualifying native evidence takes precedence"):
        service.assemble(
            constituents=(c1, c2),
            accounting_window_start=_dt(2026, 1, 1),
            accounting_window_end=_dt(2026, 3, 1),
            recorded_at=_dt(2026, 3, 2),
            replay_cutoff=_dt(2026, 3, 2),
            methodology_contract_id="contract-test-1",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="1.0.0",
        )

    conflicts = service.unresolved_assembly_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].reason == "qualifying native evidence takes precedence over assembly"


# --- PROOF 8: Provenance Continuity ---


def test_proof_8_provenance_continuity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    seeded = _seed_production_environment(monkeypatch=monkeypatch, db_path=db_path, app_root=app_root)

    service = build_production_evidence_assembly_service(db_path=db_path, application_root=app_root)

    c1 = AssemblyConstituent(
        record=seeded["ev1"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )
    c2 = AssemblyConstituent(
        record=seeded["ev2"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )

    assembled = service.assemble(
        constituents=(c1, c2),
        accounting_window_start=_dt(2026, 1, 1),
        accounting_window_end=_dt(2026, 3, 1),
        recorded_at=_dt(2026, 3, 2),
        replay_cutoff=_dt(2026, 3, 2),
        methodology_contract_id="contract-test-1",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="1.0.0",
    )

    assert assembled.constituent_record_ids == (seeded["ev1"].record_id, seeded["ev2"].record_id)
    assert assembled.constituent_logical_ids == (seeded["ev1"].logical_id, seeded["ev2"].logical_id)
    assert assembled.constituent_content_hashes == (seeded["ev1"].content_hash, seeded["ev2"].content_hash)
    assert assembled.constituent_shape_ids == ("monthly-revenue-shape", "monthly-revenue-shape")


# --- PROOF 9: Historical / Strict-Known Replay ---


def test_proof_9_historical_strict_known_replay(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    seeded = _seed_production_environment(monkeypatch=monkeypatch, db_path=db_path, app_root=app_root)

    service = build_production_evidence_assembly_service(db_path=db_path, application_root=app_root)

    c1 = AssemblyConstituent(
        record=seeded["ev1"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )
    c2 = AssemblyConstituent(
        record=seeded["ev2"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )

    assembled = service.assemble(
        constituents=(c1, c2),
        accounting_window_start=_dt(2026, 1, 1),
        accounting_window_end=_dt(2026, 3, 1),
        recorded_at=_dt(2026, 3, 2),
        replay_cutoff=_dt(2026, 3, 2),
        methodology_contract_id="contract-test-1",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="1.0.0",
    )

    prior_replay = service.strict_known(
        logical_id=assembled.logical_id,
        effective_as_of=_dt(2026, 3, 1),
        known_by=_dt(2026, 3, 1),
    )
    assert prior_replay is None

    exact_replay = service.strict_known(
        logical_id=assembled.logical_id,
        effective_as_of=_dt(2026, 3, 1),
        known_by=_dt(2026, 3, 2),
    )
    assert exact_replay is not None
    assert exact_replay.record_id == assembled.record_id


# --- PROOF 10: Separate Production Repositories Isolation ---


def test_proof_10_separate_repositories_isolation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app_root = tmp_path
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(app_root))
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_KEY_ID", "key-test-1")
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_KEY_SECRET", "0123456789abcdef0123456789abcdef")

    db_assembly = tmp_path / "assembly.sqlite"
    db_vc = tmp_path / "vc.sqlite"
    db_meth = tmp_path / "meth.sqlite"
    db_reg = tmp_path / "reg.sqlite"
    db_si = tmp_path / "si.sqlite"
    db_sem = tmp_path / "sem.sqlite"

    assembled_repo = AssembledEvidenceRepository(db_assembly)
    vc_repo = SupplyAndValueCaptureRepository(db_vc)
    vc_service = SupplyAndValueCaptureService(
        repository=vc_repo,
        registry=ValueCaptureSourceRegistry(sources=()),
        verification_keys=ValueCaptureVerificationKeyRegistry(keys={"key-test-1": b"0" * 32}),
    )
    meth_repo = ValuationMethodologyRepository(db_meth)
    meth_auth = CanonicalValuationMethodologyAuthority(repository=meth_repo, application_root=app_root)
    reg_repo = EvidenceShapeRegistryRepository(db_reg)
    reg_auth = CanonicalEvidenceShapeRegistryAuthority(repository=reg_repo, application_root=app_root)
    si_repo = EvidenceSemanticInputRepository(db_si)
    si_auth = CanonicalEvidenceSemanticInputAuthority(
        repository=si_repo, value_capture_repository=vc_repo, application_root=app_root
    )
    sem_repo = EvidenceSemanticsRepository(db_sem)
    sem_auth = CanonicalEvidenceSemanticsAuthority(
        semantic_input_authority=si_auth, repository=sem_repo, application_root=app_root
    )

    service = CanonicalEvidenceAssemblyService(
        repository=assembled_repo,
        native_evidence_query=vc_service,
        methodology_contract_authority=meth_auth,
        evidence_shape_registry_authority=reg_auth,
        evidence_semantics_authority=sem_auth,
    )

    assert isinstance(service, CanonicalEvidenceAssemblyService)
    for path in (db_assembly, db_vc, db_meth, db_reg, db_si, db_sem):
        assert path.exists()


# --- PROOF 11 & 12: Read-Only Operations Create No Records & Mutate No Persistence & Repository Snapshot ---


def test_proof_11_12_readonly_operations_no_writes_and_upstream_immutability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    seeded = _seed_production_environment(monkeypatch=monkeypatch, db_path=db_path, app_root=app_root)

    service = build_production_evidence_assembly_service(db_path=db_path, application_root=app_root)

    initial_assembly_conflicts = len(service.repository.conflict_records())

    meth_records_before = len(seeded["meth_repo"].records())
    reg_records_before = len(seeded["reg_repo"].records())
    sem_records_before = len(seeded["sem_repo"].records())
    vc_records_before = len(seeded["vc_repo"].evidence_records())

    result = service.strict_known(
        logical_id="nonexistent-logical",
        effective_as_of=_dt(2026, 3, 1),
        known_by=_dt(2026, 3, 2),
    )
    assert result is None

    assert not service.is_superseded("nonexistent-record")
    conflicts = service.unresolved_assembly_conflicts()

    assert len(conflicts) == initial_assembly_conflicts
    assert len(service.repository.conflict_records()) == initial_assembly_conflicts

    c1 = AssemblyConstituent(
        record=seeded["ev1"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )
    c2 = AssemblyConstituent(
        record=seeded["ev2"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )

    service.assemble(
        constituents=(c1, c2),
        accounting_window_start=_dt(2026, 1, 1),
        accounting_window_end=_dt(2026, 3, 1),
        recorded_at=_dt(2026, 3, 2),
        replay_cutoff=_dt(2026, 3, 2),
        methodology_contract_id="contract-test-1",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="1.0.0",
    )

    assert len(seeded["meth_repo"].records()) == meth_records_before
    assert len(seeded["reg_repo"].records()) == reg_records_before
    assert len(seeded["sem_repo"].records()) == sem_records_before
    assert len(seeded["vc_repo"].evidence_records()) == vc_records_before


# --- PROOF 14: Dependency Graph Remains Acyclic (AST Analysis) ---


def test_proof_14_dependency_graph_acyclic_ast_analysis() -> None:
    """Parse AST of all upstream modules to prove none of them import hunter.evidence_assembly."""
    upstream_packages = (
        "hunter.value_capture",
        "hunter.valuation_methodology",
        "hunter.evidence_semantic_inputs",
    )

    repo_root = Path(__file__).resolve().parent.parent / "src"

    for pkg_name in upstream_packages:
        pkg_path = repo_root / pkg_name.replace(".", "/")
        assert pkg_path.is_dir(), f"package directory missing: {pkg_path}"

        for root_dir, _, files in os.walk(pkg_path):
            for file in files:
                if file.endswith(".py"):
                    full_path = Path(root_dir) / file
                    tree = ast.parse(full_path.read_text(encoding="utf-8"), filename=str(full_path))

                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                assert not alias.name.startswith(
                                    "hunter.evidence_assembly"
                                ), f"upstream file {full_path} contains forbidden import: {alias.name}"
                        elif isinstance(node, ast.ImportFrom):
                            if node.module:
                                assert not node.module.startswith(
                                    "hunter.evidence_assembly"
                                ), f"upstream file {full_path} contains forbidden import from: {node.module}"


# --- PROOF 15: Authority Ownership Remains Singular ---


def test_proof_15_authority_ownership_singular(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    _seed_production_environment(monkeypatch=monkeypatch, db_path=db_path, app_root=app_root)

    service = build_production_evidence_assembly_service(db_path=db_path, application_root=app_root)

    assert not hasattr(service, "persist_contract")
    assert not hasattr(service, "persist_registry")
    assert not hasattr(service, "register_semantics")


# --- HOSTILE TESTS ---


def test_hostile_caller_override_of_canonical_authority(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Caller attempts to supply declared constituent metadata that differs from authoritative semantics."""
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    seeded = _seed_production_environment(monkeypatch=monkeypatch, db_path=db_path, app_root=app_root)

    service = build_production_evidence_assembly_service(db_path=db_path, application_root=app_root)

    override_c1 = AssemblyConstituent(
        record=seeded["ev1"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="fake-supply-basis",  # Hostile override!
        pathway_id="pathway-test",
    )
    c2 = AssemblyConstituent(
        record=seeded["ev2"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )

    with pytest.raises(
        CanonicalEvidenceAssemblyError, match="metadata does not match authoritative strict-known evidence semantics"
    ):
        service.assemble(
            constituents=(override_c1, c2),
            accounting_window_start=_dt(2026, 1, 1),
            accounting_window_end=_dt(2026, 3, 1),
            recorded_at=_dt(2026, 3, 2),
            replay_cutoff=_dt(2026, 3, 2),
            methodology_contract_id="contract-test-1",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="1.0.0",
        )


def test_hostile_fake_authority_substitution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Attempt passing a fake/stub authority object to production service assembly."""
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    seeded = _seed_production_environment(monkeypatch=monkeypatch, db_path=db_path, app_root=app_root)

    class FakeSemanticsAuthority:
        def strict_known_semantics(self, **kwargs):
            return None

    repo = AssembledEvidenceRepository(db_path)
    service = CanonicalEvidenceAssemblyService(
        repository=repo,
        native_evidence_query=seeded["vc_service"],
        methodology_contract_authority=seeded["meth_auth"],
        evidence_shape_registry_authority=seeded["reg_auth"],
        evidence_semantics_authority=FakeSemanticsAuthority(),  # type: ignore
    )

    c1 = AssemblyConstituent(
        record=seeded["ev1"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )
    c2 = AssemblyConstituent(
        record=seeded["ev2"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )

    with pytest.raises(CanonicalEvidenceAssemblyError, match="no exact strict-known authoritative evidence semantics"):
        service.assemble(
            constituents=(c1, c2),
            accounting_window_start=_dt(2026, 1, 1),
            accounting_window_end=_dt(2026, 3, 1),
            recorded_at=_dt(2026, 3, 2),
            replay_cutoff=_dt(2026, 3, 2),
            methodology_contract_id="contract-test-1",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="1.0.0",
        )


def test_hostile_inconsistent_provenance_hash_tampering(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Constituent with tampered content hash fails closed."""
    db_path = tmp_path / "data_ops.sqlite"
    app_root = tmp_path
    seeded = _seed_production_environment(monkeypatch=monkeypatch, db_path=db_path, app_root=app_root)

    service = build_production_evidence_assembly_service(db_path=db_path, application_root=app_root)

    tampered_ev1 = replace(seeded["ev1"], content_hash="")

    c1_tampered = AssemblyConstituent(
        record=tampered_ev1,
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )
    c2 = AssemblyConstituent(
        record=seeded["ev2"],
        shape_id="monthly-revenue-shape",
        currency="USD",
        raw_unit="USD",
        accounting_meaning="period_specific",
        supply_basis_id="supply-basis-test",
        pathway_id="pathway-test",
    )

    with pytest.raises(
        CanonicalEvidenceAssemblyError, match="constituent canonical content hash is required for provenance"
    ):
        service.assemble(
            constituents=(c1_tampered, c2),
            accounting_window_start=_dt(2026, 1, 1),
            accounting_window_end=_dt(2026, 3, 1),
            recorded_at=_dt(2026, 3, 2),
            replay_cutoff=_dt(2026, 3, 2),
            methodology_contract_id="contract-test-1",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="1.0.0",
        )
