from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.evidence_assembly import (
    ASSEMBLY_RULE_VERSION,
    AssembledEvidenceRepository,
    AssemblyConstituent,
    CanonicalEvidenceAssemblyError,
    CanonicalEvidenceAssemblyService,
    EvidenceAssemblyPersistenceError,
    EvidenceShape,
    EvidenceShapeRegistryAuthority,
    EvidenceShapeRegistryError,
    EvidenceShapeRegistryRepository,
)
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.serialization import record_to_json
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.persistence.sql.base import PersistenceRecordModel
from hunter.valuation_methodology.models import (
    AUTHORIZING_ADR_REFERENCE,
    CANONICAL_AUTHORITY_ID,
    PERMITTED_MODEL_IDENTIFIER,
    REQUIRED_CORRELATION_GROUP,
    REQUIRED_HORIZON_DAYS,
    VALUATION_METHODOLOGY_SCHEMA_VERSION,
    ValuationMethodologySnapshot,
)
from hunter.valuation_methodology.repository import ValuationMethodologyRepository
from hunter.valuation_methodology.service import CanonicalValuationMethodologyAuthority
from hunter.value_capture.models import (
    VALUE_CAPTURE_SCHEMA_VERSION,
    EconomicClaimIdentity,
    FundamentalEvidenceRecord,
    SupplyBasisSnapshot,
    ValueCaptureRuleSnapshot,
)
from hunter.value_capture.repository import SupplyAndValueCaptureRepository, record_snapshot

T0 = datetime(2025, 1, 1, tzinfo=UTC)
T1 = datetime(2025, 7, 2, tzinfo=UTC)
T2 = datetime(2026, 1, 1, tzinfo=UTC)
T3 = datetime(2026, 4, 1, tzinfo=UTC)
RECORDED = datetime(2026, 2, 1, tzinfo=UTC)
CUTOFF = datetime(2026, 3, 1, tzinfo=UTC)
HASH = "a" * 64


def _identity() -> EconomicClaimIdentity:
    return EconomicClaimIdentity(
        entity_id="entity",
        economic_claim_id="claim",
        asset_id="asset",
        representation_id="representation",
        token_id="token",
    )


def _evidence(record_id: str, start: datetime, end: datetime, amount: str = "10") -> FundamentalEvidenceRecord:
    return FundamentalEvidenceRecord(
        record_id=record_id,
        logical_id=f"logical-{record_id}",
        schema_version=VALUE_CAPTURE_SCHEMA_VERSION,
        semantic_version="1.0.0",
        identity=_identity(),
        evidence_type="audited_financial_disclosure",
        source_id=f"source-{record_id}",
        source_authority_tier="primary",
        source_reference=f"ref-{record_id}",
        parser_version="1",
        extracted_claim="period revenue",
        amount=amount,
        unit="USD",
        accounting_period_start=start,
        accounting_period_end=end,
        attribution_rule_id="policy",
        source_methodology="reported",
        source_record_id=f"source-record-{record_id}",
        source_record_version="1",
        entity_link_confidence="1",
        evidence_confidence="0.9",
        uncertainty="0.1",
        effective_at=end,
        recorded_at=RECORDED,
        known_at=RECORDED,
        raw_content_hash=HASH,
        quality_state="accepted",
        conflict_state="none",
        content_hash=HASH,
        acquisition_id=f"acquisition-{record_id}",
    )


def _supply() -> SupplyBasisSnapshot:
    return SupplyBasisSnapshot(
        record_id="supply-record",
        logical_id="supply-logical",
        schema_version=VALUE_CAPTURE_SCHEMA_VERSION,
        semantic_version="1.0.0",
        identity=_identity(),
        supply_basis_type="circulating_supply",
        quantity="100",
        unit="TOKEN",
        denominator_meaning="circulating",
        supply_policy_id="supply-policy",
        supply_policy_version="1",
        quantity_components=(("circulating_supply", "100"),),
        observed_market_fact_ids=("market",),
        observed_market_fact_versions=("1",),
        source_record_id="supply-source",
        source_record_version="1",
        confidence="1",
        uncertainty="0",
        effective_at=T0,
        recorded_at=RECORDED,
        known_at=RECORDED,
        source_id="supply-provider",
        parser_version="1",
        evidence_record_ids=("e1",),
        raw_payload_hash=HASH,
        quality_state="accepted",
        conflict_state="none",
        content_hash=HASH,
        acquisition_id="supply-acquisition",
    )


def _rule() -> ValueCaptureRuleSnapshot:
    return ValueCaptureRuleSnapshot(
        record_id="rule-record",
        logical_id="rule-logical",
        schema_version=VALUE_CAPTURE_SCHEMA_VERSION,
        semantic_version="1.0.0",
        identity=_identity(),
        rule_type="revenue_distribution",
        entitlement_scope="token holder",
        beneficiary_scope="token holder",
        source_economic_flow="revenue",
        destination_economic_flow="holder",
        trigger_condition="period close",
        distribution_formula="exact",
        rate_or_proportion="1",
        governance_or_contract_authority="contract",
        mechanism_policy_id="policy",
        mechanism_policy_version="1",
        dilution_treatment="none",
        claim_seniority="senior",
        applicability_start=T0,
        applicability_end=CUTOFF,
        limitations=("none",),
        evidence_record_versions=("1",),
        source_record_id="rule-source",
        source_record_version="1",
        confidence="1",
        uncertainty="0",
        effective_at=T0,
        recorded_at=RECORDED,
        known_at=RECORDED,
        source_id="rule-provider",
        parser_version="1",
        evidence_record_ids=("e1",),
        raw_payload_hash=HASH,
        quality_state="accepted",
        conflict_state="none",
        content_hash=HASH,
        acquisition_id="rule-acquisition",
    )


def _shape(
    shape_id: str = "monthly-revenue",
    cadence: str = "monthly",
    *,
    compatible: tuple[str, ...] = ("daily", "monthly", "quarterly"),
) -> EvidenceShape:
    return EvidenceShape(
        shape_id=shape_id,
        evidence_type="audited_financial_disclosure",
        source_methodology="reported",
        accounting_meaning="period_specific",
        cadence=cadence,  # type: ignore[arg-type]
        composition_operation="exact_sum",
        active=True,
        currency="USD",
        unit="USD",
        compatible_cadences=compatible,  # type: ignore[arg-type]
    )


def _methodology(*, override: str | None = None) -> ValuationMethodologySnapshot:
    return ValuationMethodologySnapshot(
        record_id="methodology-record",
        logical_id="methodology-logical",
        schema_version=VALUATION_METHODOLOGY_SCHEMA_VERSION,
        semantic_version="2.0.0",
        effective_at=T0,
        recorded_at=RECORDED,
        known_at=RECORDED,
        content_hash=HASH,
        quality_state="accepted",
        conflict_state="none",
        entity_class_criteria_id="criteria",
        entity_class_criteria_version="1",
        permitted_model_identifier=PERMITTED_MODEL_IDENTIFIER,
        horizon_days=REQUIRED_HORIZON_DAYS,
        currency="USD",
        discount_rate_policy_id="discount",
        discount_rate_policy_version="1",
        sensitivity_policy_id="sensitivity",
        sensitivity_policy_version="1",
        supply_basis_selection_rule="circulating",
        normalization_policy_id=None,
        correlation_group=REQUIRED_CORRELATION_GROUP,
        authorizing_adr_reference=AUTHORIZING_ADR_REFERENCE,
        authorized_by=CANONICAL_AUTHORITY_ID,
        accepts_assembled_evidence=True,
        accepted_evidence_shape_ids=("daily-revenue", "monthly-revenue", "quarterly-revenue", "irregular"),
        accepted_assembly_rule_versions=(ASSEMBLY_RULE_VERSION,),
        assembled_evidence_granularity_override=override,
    )


def _save_snapshots(path: Path, *snapshots: SnapshotRecord) -> None:
    engine = create_sqlite_engine(path)
    create_schema(engine)
    session = SessionFactory(engine).create()
    try:
        repository = RepositoryFactory(session).snapshots()
        for snapshot in snapshots:
            repository.save(snapshot)
        session.commit()
    finally:
        session.close()
        engine.dispose()


def _service(
    tmp_path: Path,
    *,
    evidence: tuple[FundamentalEvidenceRecord, ...] | None = None,
    shapes: tuple[EvidenceShape, ...] | None = None,
    methodology: ValuationMethodologySnapshot | None = None,
    supply: SupplyBasisSnapshot | None = None,
    rule: ValueCaptureRuleSnapshot | None = None,
) -> tuple[CanonicalEvidenceAssemblyService, EvidenceShapeRegistryAuthority]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "data_ops.sqlite"
    records = evidence or (_evidence("e1", T0, T1), _evidence("e2", T1, T2))
    _save_snapshots(
        path,
        *(record_snapshot(record) for record in (*records, supply or _supply(), rule or _rule())),
    )
    requested_methodology = methodology or _methodology()
    method_authority = CanonicalValuationMethodologyAuthority(
        repository=ValuationMethodologyRepository(path),
        application_root=tmp_path,
    )
    method_authority.persist_evidence_assembly_methodology(
        entity_class_criteria_id=requested_methodology.entity_class_criteria_id,
        entity_class_criteria_version=requested_methodology.entity_class_criteria_version,
        currency=requested_methodology.currency,
        discount_rate_policy_id=requested_methodology.discount_rate_policy_id,
        discount_rate_policy_version=requested_methodology.discount_rate_policy_version,
        sensitivity_policy_id=requested_methodology.sensitivity_policy_id,
        sensitivity_policy_version=requested_methodology.sensitivity_policy_version,
        supply_basis_selection_rule=requested_methodology.supply_basis_selection_rule,
        accepted_evidence_shape_ids=requested_methodology.accepted_evidence_shape_ids,
        accepted_assembly_rule_versions=requested_methodology.accepted_assembly_rule_versions,
        assembled_evidence_granularity_override=requested_methodology.assembled_evidence_granularity_override,
        effective_at=requested_methodology.effective_at,
        recorded_at=requested_methodology.recorded_at,
        known_at=requested_methodology.known_at,
    )
    registry_repository = EvidenceShapeRegistryRepository(path)
    registry_authority = EvidenceShapeRegistryAuthority(registry_repository)
    registry_authority.persist(
        registry_id="canonical-shapes",
        registry_version="1",
        shapes=shapes or (_shape(),),
        effective_start=T0,
        effective_end=datetime(2027, 1, 1, tzinfo=UTC),
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    return (
        CanonicalEvidenceAssemblyService(
            repository=AssembledEvidenceRepository(path),
            value_capture_repository=SupplyAndValueCaptureRepository(path),
            methodology_authority=method_authority,
            registry_authority=registry_authority,
        ),
        registry_authority,
    )


def _constituents(*ids: str) -> tuple[AssemblyConstituent, ...]:
    return tuple(
        AssemblyConstituent(
            evidence_record_id=record_id,
            supply_basis_record_id="supply-record",
            pathway_rule_record_id="rule-record",
        )
        for record_id in ids
    )


def _assemble(service: CanonicalEvidenceAssemblyService, *ids: str, **kwargs: object):
    return service.assemble(
        constituents=_constituents(*ids),
        registry_id="canonical-shapes",
        registry_version="1",
        accounting_window_start=kwargs.pop("start", T0),  # type: ignore[arg-type]
        accounting_window_end=kwargs.pop("end", T2),  # type: ignore[arg-type]
        recorded_at=kwargs.pop("recorded_at", RECORDED),  # type: ignore[arg-type]
        replay_cutoff=kwargs.pop("replay_cutoff", CUTOFF),  # type: ignore[arg-type]
        **kwargs,
    )


def test_production_component_assembly_and_provenance(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    record = _assemble(service, "e2", "e1")
    assert record.amount == "20"
    assert record.constituent_record_ids == ("e1", "e2")
    assert record.methodology_record_id
    assert record.methodology_logical_id
    assert record.registry_id == "canonical-shapes"
    assert record.effective_at == T2
    assert record.known_at == RECORDED
    assert service.strict_known(logical_id=record.logical_id, effective_as_of=T2, known_by=CUTOFF) == record


def test_current_canonical_methodology_remains_not_opted_in(tmp_path: Path) -> None:
    path = tmp_path / "data_ops.sqlite"
    authority = CanonicalValuationMethodologyAuthority(
        repository=ValuationMethodologyRepository(path), application_root=tmp_path
    )
    record = authority.persist_methodology(
        entity_class_criteria_id="criteria",
        entity_class_criteria_version="1",
        currency="USD",
        discount_rate_policy_id="discount",
        discount_rate_policy_version="1",
        sensitivity_policy_id="sensitivity",
        sensitivity_policy_version="1",
        supply_basis_selection_rule="circulating",
        effective_at=T0,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert record.accepts_assembled_evidence is False
    assert record.accepted_evidence_shape_ids == ()


def test_registry_generic_sql_history_hash_and_strict_known(tmp_path: Path) -> None:
    path = tmp_path / "data_ops.sqlite"
    repository = EvidenceShapeRegistryRepository(path)
    authority = EvidenceShapeRegistryAuthority(repository)
    first = authority.persist(
        registry_id="canonical-shapes",
        registry_version="1",
        shapes=(_shape(),),
        effective_start=T0,
        effective_end=T3,
        recorded_at=T1,
        known_at=T1,
    )
    assert (
        authority.persist(
            registry_id="canonical-shapes",
            registry_version="1",
            shapes=(_shape(),),
            effective_start=T0,
            effective_end=T3,
            recorded_at=T1,
            known_at=T1,
        )
        == first
    )
    assert (
        repository.strict_known(registry_id="canonical-shapes", registry_version="1", effective_as_of=T2, known_by=T0)
        is None
    )
    assert (
        repository.strict_known(registry_id="canonical-shapes", registry_version="1", effective_as_of=T2, known_by=T2)
        == first
    )
    assert repository.migration_ids() == ("generic-sql-evidence-shape-registry-v1",)
    assert len(repository.history("canonical-shapes")) == 1
    with pytest.raises(EvidenceShapeRegistryError):
        authority.persist(
            registry_id="canonical-shapes",
            registry_version="1",
            shapes=(_shape("different"),),
            effective_start=T0,
            effective_end=T3,
            recorded_at=T1,
            known_at=T1,
        )


def test_future_effective_candidate_has_zero_replay_influence(tmp_path: Path) -> None:
    future = replace(
        _evidence("future-native", T0, T2),
        effective_at=CUTOFF + timedelta(days=1),
        recorded_at=CUTOFF + timedelta(days=2),
        known_at=CUTOFF + timedelta(days=2),
    )
    service, _ = _service(
        tmp_path,
        evidence=(_evidence("e1", T0, T1), _evidence("e2", T1, T2), future),
    )
    assert _assemble(service, "e1", "e2").amount == "20"
    assert service.repository.unresolved_assembly_conflicts(known_by=CUTOFF) == ()


def test_conflict_chronology_and_complete_provenance(tmp_path: Path) -> None:
    native = _evidence("native", T0, T2)
    service, _ = _service(
        tmp_path,
        evidence=(_evidence("e1", T0, T1), _evidence("e2", T1, T2), native),
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="native"):
        _assemble(service, "e1", "e2")
    conflict = service.repository.unresolved_assembly_conflicts(known_by=CUTOFF)[0]
    assert conflict.known_at == RECORDED
    assert conflict.candidate_record_ids == ("e1", "e2", "native")
    assert len(conflict.candidate_content_hashes) == 3
    assert conflict.registry_record_id
    assert conflict.methodology_record_id
    assert conflict.assembly_request_id
    assert conflict.replay_cutoff == CUTOFF


def test_conflict_unknown_at_cutoff_is_not_persisted(tmp_path: Path) -> None:
    native = replace(
        _evidence("native", T0, T2),
        effective_at=T2,
        recorded_at=CUTOFF + timedelta(days=1),
        known_at=CUTOFF + timedelta(days=1),
    )
    service, _ = _service(
        tmp_path,
        evidence=(_evidence("e1", T0, T1), _evidence("e2", T1, T2), native),
    )
    assert _assemble(service, "e1", "e2")
    assert service.repository.unresolved_assembly_conflicts() == ()


def test_supersession_is_append_only_projected_state(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    first = _assemble(service, "e1", "e2")
    later = RECORDED + timedelta(days=1)
    corrected_records = (
        replace(
            _evidence("e1-c", T0, T1, "11"),
            recorded_at=later,
            known_at=later,
            supersedes_record_id="e1",
            correction_reason="corrected",
        ),
        replace(
            _evidence("e2-c", T1, T2, "12"),
            recorded_at=later,
            known_at=later,
            supersedes_record_id="e2",
            correction_reason="corrected",
        ),
    )
    _save_snapshots(
        service.repository.path,
        *(record_snapshot(record) for record in corrected_records),
    )
    second = _assemble(
        service,
        "e1-c",
        "e2-c",
        recorded_at=later,
        supersedes_record_id=first.record_id,
        correction_reason="corrected disclosure",
    )
    projection = service.repository.projected_history(first.logical_id)
    assert [(item.record.record_id, item.supersession_state) for item in projection] == [
        (first.record_id, "superseded"),
        (second.record_id, "active"),
    ]
    assert not hasattr(first, "supersession_state")


@pytest.mark.parametrize(
    ("selected", "alternative", "allowed"),
    [
        ("daily", "monthly", True),
        ("monthly", "quarterly", True),
        ("monthly", "monthly", True),
        ("quarterly", "monthly", False),
    ],
)
def test_governed_cadence_comparison(tmp_path: Path, selected: str, alternative: str, allowed: bool) -> None:
    selected_shape = _shape(f"{selected}-revenue", selected)
    alternative_shape = _shape(f"{alternative}-revenue", alternative)
    path = tmp_path / "data_ops.sqlite"
    authority = EvidenceShapeRegistryAuthority(EvidenceShapeRegistryRepository(path))
    registry = authority.persist(
        registry_id="cadence",
        registry_version="1",
        shapes=(
            (selected_shape, alternative_shape)
            if selected_shape.shape_id != alternative_shape.shape_id
            else (selected_shape,)
        ),
        effective_start=T0,
        effective_end=T3,
        recorded_at=T1,
        known_at=T1,
    )
    comparison = registry.compare_cadence(selected_shape, alternative_shape)
    assert (comparison <= 0) is allowed


@pytest.mark.parametrize("cadence", ["event_driven", "irregular", "epoch_based"])
def test_non_calendar_cadence_fails_closed_without_methodology_override(tmp_path: Path, cadence: str) -> None:
    shape = _shape("irregular", cadence, compatible=())
    service, _ = _service(tmp_path, shapes=(shape,))
    with pytest.raises(CanonicalEvidenceAssemblyError, match="override"):
        _assemble(service, "e1", "e2")


def test_explicit_strict_known_methodology_override_permits_irregular(tmp_path: Path) -> None:
    shape = _shape("irregular", "irregular", compatible=())
    service, _ = _service(tmp_path, shapes=(shape,), methodology=_methodology(override="irregular"))
    assert _assemble(service, "e1", "e2").cadence == "irregular"


def test_insert_identical_and_divergent_duplicate_rejection(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    record = _assemble(service, "e1", "e2")
    assert _assemble(service, "e1", "e2") == record
    with pytest.raises(EvidenceAssemblyPersistenceError):
        service.repository._insert_authorized(replace(record, amount="999"))


def test_methodology_horizon_and_currency_are_enforced(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="horizon"):
        service.assemble(
            constituents=_constituents("e1", "e2"),
            registry_id="canonical-shapes",
            registry_version="1",
            accounting_window_start=T0,
            accounting_window_end=T2 - timedelta(days=1),
            recorded_at=RECORDED,
            replay_cutoff=CUTOFF,
        )
    service, _ = _service(tmp_path / "currency", methodology=replace(_methodology(), currency="EUR"))
    with pytest.raises(CanonicalEvidenceAssemblyError, match="currency"):
        _assemble(service, "e1", "e2")


def test_unresolved_omitted_candidate_rejects_and_persists_conflict(tmp_path: Path) -> None:
    unresolved = replace(_evidence("open", T0, T1), conflict_state="open")
    service, _ = _service(
        tmp_path,
        evidence=(_evidence("e1", T0, T1), _evidence("e2", T1, T2), unresolved),
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="unresolved"):
        _assemble(service, "e1", "e2")
    assert service.repository.unresolved_assembly_conflicts()[0].conflict_category == "unresolved-source-conflict"


def test_divergent_equal_cadence_complete_series_is_conflict(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path,
        evidence=(
            _evidence("e1", T0, T1),
            _evidence("e2", T1, T2),
            _evidence("a1", T0, T1, "999"),
            _evidence("a2", T1, T2, "888"),
        ),
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="equal-cadence"):
        _assemble(service, "e1", "e2")
    assert service.repository.unresolved_assembly_conflicts()[0].conflict_category == "equal-cadence-alternative"


def test_identical_duplicate_interval_is_deduplicated_deterministically(tmp_path: Path) -> None:
    duplicate = replace(
        _evidence("duplicate", T0, T1),
        source_id="source-e1",
        source_reference="ref-e1",
        source_record_id="source-record-e1",
        acquisition_id="acquisition-e1",
    )
    service, _ = _service(
        tmp_path,
        evidence=(_evidence("e1", T0, T1), duplicate, _evidence("e2", T1, T2)),
    )
    record = service.assemble(
        constituents=_constituents("duplicate", "e1", "e2"),
        registry_id="canonical-shapes",
        registry_version="1",
        accounting_window_start=T0,
        accounting_window_end=T2,
        recorded_at=RECORDED,
        replay_cutoff=CUTOFF,
    )
    assert record.constituent_record_ids == ("duplicate", "e2")


def test_strict_known_replay_verifies_transitive_provenance(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    record = _assemble(service, "e1", "e2")
    original = service.value_capture_repository.evidence("e1")
    assert original is not None
    divergent = replace(original, content_hash="b" * 64)
    engine = create_sqlite_engine(service.repository.path)
    session = SessionFactory(engine).create()
    try:
        model = session.get(PersistenceRecordModel, "e1")
        assert model is not None
        model.payload = record_to_json(record_snapshot(divergent))
        session.commit()
    finally:
        session.close()
        engine.dispose()
    with pytest.raises(CanonicalEvidenceAssemblyError, match="provenance"):
        service.strict_known(logical_id=record.logical_id, effective_as_of=T2, known_by=CUTOFF)


@pytest.mark.parametrize(
    ("records", "message"),
    [
        (
            (
                _evidence("e1", T0, T1),
                _evidence("e2", T1 + timedelta(days=1), T2),
            ),
            "gap",
        ),
        (
            (
                _evidence("e1", T0, T1),
                _evidence("e2", T1 - timedelta(days=1), T2),
            ),
            "overlap",
        ),
    ],
)
def test_gap_and_overlap_fail_independently(
    tmp_path: Path, records: tuple[FundamentalEvidenceRecord, ...], message: str
) -> None:
    service, _ = _service(tmp_path, evidence=records)
    with pytest.raises(CanonicalEvidenceAssemblyError, match=message):
        _assemble(service, "e1", "e2")


def test_identity_representation_unit_and_accounting_meaning_fail_closed(tmp_path: Path) -> None:
    different_identity = replace(
        _identity(),
        representation_id="other-representation",
    )
    service, _ = _service(
        tmp_path / "identity",
        evidence=(
            _evidence("e1", T0, T1),
            replace(_evidence("e2", T1, T2), identity=different_identity),
        ),
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="identity"):
        _assemble(service, "e1", "e2")

    service, _ = _service(
        tmp_path / "unit",
        evidence=(
            _evidence("e1", T0, T1),
            replace(_evidence("e2", T1, T2), unit="EUR"),
        ),
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="classification|unit"):
        _assemble(service, "e1", "e2")

    cumulative = replace(_shape(), accounting_meaning="cumulative")
    service, _ = _service(tmp_path / "meaning", shapes=(cumulative,))
    with pytest.raises(CanonicalEvidenceAssemblyError, match="summation"):
        _assemble(service, "e1", "e2")


def test_pathway_applicability_must_cover_full_window(tmp_path: Path) -> None:
    rule = replace(_rule(), applicability_start=T1, effective_at=T1)
    service, _ = _service(tmp_path, rule=rule)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="pathway"):
        _assemble(service, "e1", "e2")


def test_registry_unknown_inactive_future_effective_and_hash_invalid_fail_closed(tmp_path: Path) -> None:
    inactive = replace(_shape(), active=False)
    service, _ = _service(tmp_path / "inactive", shapes=(inactive,))
    with pytest.raises(CanonicalEvidenceAssemblyError, match="classification"):
        _assemble(service, "e1", "e2")

    path = tmp_path / "future" / "data_ops.sqlite"
    path.parent.mkdir(parents=True)
    authority = EvidenceShapeRegistryAuthority(EvidenceShapeRegistryRepository(path))
    authority.persist(
        registry_id="future",
        registry_version="1",
        shapes=(_shape(),),
        effective_start=T2,
        effective_end=T3,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert (
        authority.strict_known_registry(
            registry_id="future",
            registry_version="1",
            effective_as_of=T1,
            known_by=CUTOFF,
        )
        is None
    )

    snapshot = authority.repository.history("future")[0]
    engine = create_sqlite_engine(path)
    session = SessionFactory(engine).create()
    try:
        model = session.get(PersistenceRecordModel, snapshot.record_id)
        assert model is not None
        stored = authority.repository.get(snapshot.record_id)
        assert stored is not None
        stored_snapshot = RepositoryFactory(session).snapshots().load(snapshot.record_id)
        assert stored_snapshot is not None
        payload = dict(stored_snapshot.payload)
        payload["content_hash"] = "b" * 64
        model.payload = record_to_json(replace(stored_snapshot, payload=payload))
        session.commit()
    finally:
        session.close()
        engine.dispose()
    with pytest.raises(EvidenceShapeRegistryError, match="hash"):
        authority.repository.get(snapshot.record_id)


def test_future_known_methodology_override_is_excluded(tmp_path: Path) -> None:
    future = replace(
        _methodology(override="irregular"),
        recorded_at=CUTOFF + timedelta(days=1),
        known_at=CUTOFF + timedelta(days=1),
    )
    service, _ = _service(
        tmp_path,
        shapes=(_shape("irregular", "irregular", compatible=()),),
        methodology=future,
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="methodology"):
        _assemble(service, "e1", "e2")


def test_no_valuation_runtime_or_duplicate_authority_surface() -> None:
    import hunter.evidence_assembly.models as models
    import hunter.evidence_assembly.service as service
    from hunter.valuation.service import CanonicalValuationService

    assert not hasattr(models, "MethodologyEvidenceInputContract")
    assert not hasattr(models, "AuthoritativeEvidenceSemantics")
    assert not hasattr(service, "MethodologyContractAuthority")
    assert not hasattr(service, "EvidenceSemanticsAuthority")
    assert not hasattr(CanonicalValuationService, "assemble")
