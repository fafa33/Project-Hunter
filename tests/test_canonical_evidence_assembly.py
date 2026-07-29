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
from hunter.evidence_assembly.registry import (
    REGISTRY_AUTHORITY_ID,
    REGISTRY_SCHEMA_VERSION,
    EvidenceShapeRegistrySnapshot,
)
from hunter.evidence_assembly.registry import (
    _content_hash as registry_content_hash,
)
from hunter.evidence_assembly.registry import (
    _snapshot as registry_snapshot,
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
from hunter.value_capture.service import canonical_value_capture_content_hash

T0 = datetime(2025, 1, 1, tzinfo=UTC)
T1 = datetime(2025, 7, 1, tzinfo=UTC)
T2 = datetime(2026, 1, 1, tzinfo=UTC)
T3 = datetime(2026, 4, 1, tzinfo=UTC)
RECORDED = datetime(2026, 2, 1, tzinfo=UTC)
CUTOFF = datetime(2026, 3, 1, tzinfo=UTC)
HASH = "a" * 64
REGISTRY_ID = "canonical-valuation-evidence-shapes"


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
    shape_id: str = "semiannual-revenue",
    cadence: str = "semiannual",
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
        supply_basis_selection_rule="circulating_supply",
        normalization_policy_id=None,
        correlation_group=REQUIRED_CORRELATION_GROUP,
        authorizing_adr_reference=AUTHORIZING_ADR_REFERENCE,
        authorized_by=CANONICAL_AUTHORITY_ID,
        accepts_assembled_evidence=True,
        accepted_evidence_shape_ids=(
            "daily-revenue",
            "monthly-revenue",
            "quarterly-revenue",
            "semiannual-revenue",
            "annual-revenue",
            "irregular",
        ),
        accepted_native_evidence_families=("audited_financial_disclosure:reported-annual",),
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


def _canonical_value_capture(record):
    return replace(record, content_hash=canonical_value_capture_content_hash(record))


def _test_registry_record(
    *,
    effective_start: datetime = T0,
    effective_end: datetime = datetime(2027, 1, 1, tzinfo=UTC),
    recorded_at: datetime = RECORDED,
    known_at: datetime = RECORDED,
) -> EvidenceShapeRegistrySnapshot:
    calendar = ("daily", "monthly", "quarterly", "semiannual", "annual")
    shapes = tuple(
        EvidenceShape(
            shape_id=f"{cadence}-revenue",
            evidence_type="audited_financial_disclosure",
            source_methodology=f"reported-{cadence}" if cadence != "semiannual" else "reported",
            accounting_meaning="period_specific",
            cadence=cadence,  # type: ignore[arg-type]
            composition_operation="none" if cadence == "annual" else "exact_sum",
            active=True,
            currency="USD",
            unit="USD",
            compatible_cadences=tuple(item for item in calendar if item != cadence),  # type: ignore[misc]
        )
        for cadence in calendar
    ) + (
        replace(_shape("irregular", "irregular", compatible=()), source_methodology="reported-irregular"),
        EvidenceShape(
            shape_id="event-driven",
            evidence_type="audited_financial_disclosure",
            source_methodology="reported-event",
            accounting_meaning="event",
            cadence="event_driven",
            composition_operation="none",
            active=False,
            currency="USD",
            unit="USD",
        ),
        EvidenceShape(
            shape_id="epoch-based",
            evidence_type="onchain_observation",
            source_methodology="reported-epoch",
            accounting_meaning="cumulative",
            cadence="epoch_based",
            composition_operation="none",
            active=False,
            currency="USD",
            unit="USD",
        ),
    )
    pending = EvidenceShapeRegistrySnapshot(
        record_id="pending",
        registry_id=REGISTRY_ID,
        registry_version="1",
        schema_version=REGISTRY_SCHEMA_VERSION,
        shapes=shapes,
        effective_start=effective_start,
        effective_end=effective_end,
        recorded_at=recorded_at,
        known_at=known_at,
        active=True,
        quality_state="accepted",
        conflict_state="none",
        authorized_by=REGISTRY_AUTHORITY_ID,
        authorizing_adr_reference="ADR-0025",
        content_hash="pending",
    )
    digest = registry_content_hash(pending)
    return replace(pending, record_id=f"test-registry:{digest}", content_hash=digest)


def _service(
    tmp_path: Path,
    *,
    evidence: tuple[FundamentalEvidenceRecord, ...] | None = None,
    methodology: ValuationMethodologySnapshot | None = None,
    supply: SupplyBasisSnapshot | None = None,
    rule: ValueCaptureRuleSnapshot | None = None,
) -> tuple[CanonicalEvidenceAssemblyService, EvidenceShapeRegistryAuthority]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "data_ops.sqlite"
    records = tuple(
        _canonical_value_capture(record) for record in (evidence or (_evidence("e1", T0, T1), _evidence("e2", T1, T2)))
    )
    supply_record = _canonical_value_capture(supply or _supply())
    rule_record = _canonical_value_capture(rule or _rule())
    _save_snapshots(
        path,
        *(record_snapshot(record) for record in (*records, supply_record, rule_record)),
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
        accepted_native_evidence_families=requested_methodology.accepted_native_evidence_families,
        accepted_assembly_rule_versions=requested_methodology.accepted_assembly_rule_versions,
        assembled_evidence_granularity_override=requested_methodology.assembled_evidence_granularity_override,
        effective_at=requested_methodology.effective_at,
        recorded_at=requested_methodology.recorded_at,
        known_at=requested_methodology.known_at,
    )
    registry_repository = EvidenceShapeRegistryRepository(path)
    registry_authority = EvidenceShapeRegistryAuthority(registry_repository, application_root=Path.cwd())
    _save_snapshots(path, registry_snapshot(_test_registry_record()))
    return (
        CanonicalEvidenceAssemblyService(
            repository=AssembledEvidenceRepository(path),
            value_capture_repository=SupplyAndValueCaptureRepository(path),
            methodology_authority=method_authority,
            registry_authority=registry_authority,
        ),
        registry_authority,
    )


def _constituents(
    *ids: str,
    supply_basis_record_id: str = "supply-record",
    pathway_rule_record_id: str = "rule-record",
) -> tuple[AssemblyConstituent, ...]:
    return tuple(
        AssemblyConstituent(
            evidence_record_id=record_id,
            supply_basis_record_id=supply_basis_record_id,
            pathway_rule_record_id=pathway_rule_record_id,
        )
        for record_id in ids
    )


def _assemble(service: CanonicalEvidenceAssemblyService, *ids: str, **kwargs: object):
    supply_basis_record_id = str(kwargs.pop("supply_basis_record_id", "supply-record"))
    pathway_rule_record_id = str(kwargs.pop("pathway_rule_record_id", "rule-record"))
    return service.assemble(
        constituents=_constituents(
            *ids,
            supply_basis_record_id=supply_basis_record_id,
            pathway_rule_record_id=pathway_rule_record_id,
        ),
        registry_id=REGISTRY_ID,
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
    assert record.registry_id == REGISTRY_ID
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
        supply_basis_selection_rule="circulating_supply",
        effective_at=T0,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert record.accepts_assembled_evidence is False
    assert record.accepted_evidence_shape_ids == ()
    assert record.accepted_native_evidence_families == ("fundamental-evidence",)


def test_registry_generic_sql_history_hash_and_strict_known(tmp_path: Path) -> None:
    path = tmp_path / "data_ops.sqlite"
    repository = EvidenceShapeRegistryRepository(path)
    authority = EvidenceShapeRegistryAuthority(repository, application_root=Path.cwd())
    with pytest.raises(EvidenceShapeRegistryError, match="authorizes no concrete entries"):
        authority.persist(
            registry_id=REGISTRY_ID,
            registry_version="1",
            effective_start=T0,
            effective_end=T3,
            recorded_at=T1,
            known_at=T1,
            authorizing_adr_reference="ADR-0025",
        )
    first = _test_registry_record(effective_end=T3, recorded_at=T1, known_at=T1)
    _save_snapshots(path, registry_snapshot(first), registry_snapshot(first))
    assert (
        repository.strict_known(registry_id=REGISTRY_ID, registry_version="1", effective_as_of=T2, known_by=T0) is None
    )
    assert (
        repository.strict_known(registry_id=REGISTRY_ID, registry_version="1", effective_as_of=T2, known_by=T2) == first
    )
    assert repository.migration_ids() == ("generic-sql-evidence-shape-registry-v1",)
    assert len(repository.history(REGISTRY_ID)) == 1
    with pytest.raises(EvidenceShapeRegistryError, match="newly accepted ADR"):
        authority.persist(
            registry_id=REGISTRY_ID,
            registry_version="1",
            effective_start=T0,
            effective_end=T3,
            recorded_at=T2,
            known_at=T2,
            authorizing_adr_reference="ADR-0025",
            supersedes_record_id=first.record_id,
            correction_reason="taxonomy amendment",
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
    native = replace(_evidence("native", T0, T2), source_methodology="reported-annual")
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
        *(record_snapshot(_canonical_value_capture(record)) for record in corrected_records),
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


def test_corrected_supply_and_pathway_remain_in_same_assembly_lineage(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    first = _assemble(service, "e1", "e2")
    later = RECORDED + timedelta(days=1)
    evidence = (
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
    supply = replace(
        _supply(),
        record_id="supply-c",
        recorded_at=later,
        known_at=later,
        content_hash="b" * 64,
        supersedes_record_id="supply-record",
        correction_reason="corrected",
    )
    rule = replace(
        _rule(),
        record_id="rule-c",
        recorded_at=later,
        known_at=later,
        content_hash="c" * 64,
        supersedes_record_id="rule-record",
        correction_reason="corrected",
    )
    _save_snapshots(
        service.repository.path,
        *(record_snapshot(_canonical_value_capture(record)) for record in (*evidence, supply, rule)),
    )
    second = _assemble(
        service,
        "e1-c",
        "e2-c",
        recorded_at=later,
        supply_basis_record_id="supply-c",
        pathway_rule_record_id="rule-c",
        supersedes_record_id=first.record_id,
        correction_reason="corrected dependencies",
    )
    assert second.logical_id == first.logical_id
    assert second.record_id != first.record_id
    assert service.repository.projected_history(first.logical_id)[-1].record == second


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
    path = tmp_path / "data_ops.sqlite"
    registry = _test_registry_record(effective_end=T3, recorded_at=T1, known_at=T1)
    _save_snapshots(path, registry_snapshot(registry))
    selected_shape = registry.shape(f"{selected}-revenue")
    alternative_shape = registry.shape(f"{alternative}-revenue")
    comparison = registry.compare_cadence(selected_shape, alternative_shape)
    assert (comparison <= 0) is allowed
    with pytest.raises(EvidenceShapeRegistryError, match="exact active"):
        registry.compare_cadence(_shape(f"{selected}-revenue", selected), alternative_shape)


@pytest.mark.parametrize(
    ("source_methodology", "message"),
    [
        ("reported-event", "classification"),
        ("reported-irregular", "override"),
        ("reported-epoch", "classification"),
    ],
)
def test_non_calendar_cadence_fails_closed_without_methodology_override(
    tmp_path: Path, source_methodology: str, message: str
) -> None:
    evidence = tuple(
        replace(record, source_methodology=source_methodology)
        for record in (_evidence("e1", T0, T1), _evidence("e2", T1, T2))
    )
    service, _ = _service(tmp_path, evidence=evidence)
    with pytest.raises(CanonicalEvidenceAssemblyError, match=message):
        _assemble(service, "e1", "e2")


def test_explicit_strict_known_methodology_override_permits_irregular(tmp_path: Path) -> None:
    evidence = tuple(
        replace(record, source_methodology="reported-irregular")
        for record in (_evidence("e1", T0, T1), _evidence("e2", T1, T2))
    )
    service, _ = _service(
        tmp_path,
        evidence=evidence,
        methodology=_methodology(override="irregular"),
    )
    assert _assemble(service, "e1", "e2").cadence == "irregular"


def test_service_prefers_governed_finer_cadence_and_honors_canonical_override(
    tmp_path: Path,
) -> None:
    quarter_boundaries = (
        T0,
        datetime(2025, 4, 1, tzinfo=UTC),
        datetime(2025, 7, 1, tzinfo=UTC),
        datetime(2025, 10, 1, tzinfo=UTC),
        T2,
    )
    quarterly = tuple(
        replace(
            _evidence(f"q{index}", quarter_boundaries[index - 1], quarter_boundaries[index]),
            source_methodology="reported-quarterly",
        )
        for index in range(1, 5)
    )
    evidence = (_evidence("e1", T0, T1), _evidence("e2", T1, T2), *quarterly)
    service, _ = _service(
        tmp_path / "default",
        evidence=evidence,
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="finest"):
        _assemble(service, "e1", "e2")

    service, _ = _service(
        tmp_path / "override",
        evidence=evidence,
        methodology=_methodology(override="semiannual"),
    )
    assert _assemble(service, "e1", "e2").cadence == "semiannual"


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
            registry_id=REGISTRY_ID,
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
    reacquired_at = RECORDED + timedelta(hours=1)
    duplicate = replace(
        _evidence("duplicate", T0, T1),
        source_id="source-e1",
        source_reference="ref-e1",
        source_record_id="source-record-e1",
        recorded_at=reacquired_at,
        known_at=reacquired_at,
        acquisition_id="reacquisition-e1",
    )
    service, _ = _service(
        tmp_path,
        evidence=(_evidence("e1", T0, T1), duplicate, _evidence("e2", T1, T2)),
    )
    record = service.assemble(
        constituents=_constituents("duplicate", "e1", "e2"),
        registry_id=REGISTRY_ID,
        registry_version="1",
        accounting_window_start=T0,
        accounting_window_end=T2,
        recorded_at=reacquired_at,
        replay_cutoff=CUTOFF,
    )
    assert record.constituent_record_ids == ("duplicate", "e2")


def test_replay_accepts_dependencies_effective_after_window_end_but_before_cutoff(tmp_path: Path) -> None:
    later_effective = T2 + timedelta(days=10)
    evidence = (
        replace(_evidence("e1", T0, T1), effective_at=later_effective),
        replace(_evidence("e2", T1, T2), effective_at=later_effective),
    )
    service, _ = _service(
        tmp_path,
        evidence=evidence,
        supply=replace(_supply(), effective_at=later_effective),
        rule=replace(_rule(), effective_at=later_effective),
    )
    record = _assemble(service, "e1", "e2")
    assert service.strict_known(logical_id=record.logical_id, effective_as_of=T2, known_by=CUTOFF) == record


def test_incompatible_exact_supply_record_boundary_fails_independently(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    alternative = _canonical_value_capture(
        replace(
            _supply(),
            record_id="supply-alternative",
            logical_id="supply-alternative-logical",
            source_record_id="supply-alternative-source",
        )
    )
    _save_snapshots(service.repository.path, record_snapshot(alternative))
    with pytest.raises(CanonicalEvidenceAssemblyError, match="supply-basis boundary"):
        service.assemble(
            constituents=(
                _constituents("e1")[0],
                _constituents("e2", supply_basis_record_id=alternative.record_id)[0],
            ),
            registry_id=REGISTRY_ID,
            registry_version="1",
            accounting_window_start=T0,
            accounting_window_end=T2,
            recorded_at=RECORDED,
            replay_cutoff=CUTOFF,
        )


def test_incompatible_exact_pathway_record_boundary_fails_independently(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    alternative = _canonical_value_capture(
        replace(
            _rule(),
            record_id="rule-alternative",
            logical_id="rule-alternative-logical",
            source_record_id="rule-alternative-source",
        )
    )
    _save_snapshots(service.repository.path, record_snapshot(alternative))
    with pytest.raises(CanonicalEvidenceAssemblyError, match="pathway boundary"):
        service.assemble(
            constituents=(
                _constituents("e1")[0],
                _constituents("e2", pathway_rule_record_id=alternative.record_id)[0],
            ),
            registry_id=REGISTRY_ID,
            registry_version="1",
            accounting_window_start=T0,
            accounting_window_end=T2,
            recorded_at=RECORDED,
            replay_cutoff=CUTOFF,
        )


def test_strict_known_replay_verifies_transitive_provenance(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    record = _assemble(service, "e1", "e2")
    original = service.value_capture_repository.evidence("e1")
    assert original is not None
    divergent = replace(original, amount="999")
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
    service, _ = _service(
        tmp_path,
        evidence=tuple(replace(record, source_methodology="reported-irregular") for record in records),
        methodology=_methodology(override="irregular"),
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match=message):
        _assemble(service, "e1", "e2")


def test_identity_representation_unit_and_accounting_meaning_fail_closed(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path / "representation",
        evidence=(
            _evidence("e1", T0, T1),
            replace(
                _evidence("e2", T1, T2),
                identity=replace(_identity(), representation_id="other-representation"),
            ),
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


def test_canonical_claim_identity_fails_independently_from_representation(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path,
        evidence=(
            _evidence("e1", T0, T1),
            replace(
                _evidence("e2", T1, T2),
                identity=replace(_identity(), economic_claim_id="other-claim"),
            ),
        ),
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="identity"):
        _assemble(service, "e1", "e2")

    event_evidence = tuple(
        replace(record, source_methodology="reported-event")
        for record in (_evidence("e1", T0, T1), _evidence("e2", T1, T2))
    )
    service, _ = _service(tmp_path / "meaning", evidence=event_evidence)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="classification"):
        _assemble(service, "e1", "e2")


def test_pathway_applicability_must_cover_full_window(tmp_path: Path) -> None:
    rule = replace(_rule(), applicability_start=T1, effective_at=T1)
    service, _ = _service(tmp_path, rule=rule)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="pathway"):
        _assemble(service, "e1", "e2")


def test_registry_unknown_inactive_future_effective_and_hash_invalid_fail_closed(tmp_path: Path) -> None:
    inactive_evidence = tuple(
        replace(record, source_methodology="reported-event")
        for record in (_evidence("e1", T0, T1), _evidence("e2", T1, T2))
    )
    service, _ = _service(tmp_path / "inactive", evidence=inactive_evidence)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="classification"):
        _assemble(service, "e1", "e2")

    path = tmp_path / "future" / "data_ops.sqlite"
    path.parent.mkdir(parents=True)
    authority = EvidenceShapeRegistryAuthority(EvidenceShapeRegistryRepository(path), application_root=Path.cwd())
    future_registry = _test_registry_record(effective_start=T2, effective_end=T3)
    _save_snapshots(path, registry_snapshot(future_registry))
    assert (
        authority.strict_known_registry(
            registry_id=REGISTRY_ID,
            registry_version="1",
            effective_as_of=T1,
            known_by=CUTOFF,
        )
        is None
    )

    snapshot = authority.repository.history(REGISTRY_ID)[0]
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
        evidence=tuple(
            replace(record, source_methodology="reported-irregular")
            for record in (_evidence("e1", T0, T1), _evidence("e2", T1, T2))
        ),
        methodology=future,
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="methodology"):
        _assemble(service, "e1", "e2")


def test_distinct_source_equal_value_series_is_not_deduplicated(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path,
        evidence=(
            _evidence("e1", T0, T1),
            _evidence("e2", T1, T2),
            _evidence("other-1", T0, T1),
            _evidence("other-2", T1, T2),
        ),
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="equal-cadence"):
        _assemble(service, "e1", "e2")


def test_override_cannot_resolve_equal_cadence_source_conflict(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path,
        methodology=_methodology(override="monthly"),
        evidence=(
            _evidence("e1", T0, T1),
            _evidence("e2", T1, T2),
            _evidence("other-1", T0, T1, "999"),
            _evidence("other-2", T1, T2, "888"),
        ),
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="equal-cadence"):
        _assemble(service, "e1", "e2")


def test_second_assembled_root_requires_explicit_correction(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    _assemble(service, "e1", "e2")
    later = RECORDED + timedelta(days=1)
    corrected = (
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
        *(record_snapshot(_canonical_value_capture(record)) for record in corrected),
    )
    with pytest.raises(EvidenceAssemblyPersistenceError, match="correction required"):
        _assemble(service, "e1-c", "e2-c", recorded_at=later)


def test_other_representation_does_not_enter_candidate_universe(tmp_path: Path) -> None:
    other = replace(
        _evidence("other-representation", T0, T2),
        identity=replace(_identity(), representation_id="other"),
    )
    service, _ = _service(
        tmp_path,
        evidence=(_evidence("e1", T0, T1), _evidence("e2", T1, T2), other),
    )
    assert _assemble(service, "e1", "e2")
    assert service.repository.unresolved_assembly_conflicts() == ()


def test_horizon_rejects_subday_overrun(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="horizon"):
        service.assemble(
            constituents=_constituents("e1", "e2"),
            registry_id=REGISTRY_ID,
            registry_version="1",
            accounting_window_start=T0,
            accounting_window_end=T2 + timedelta(hours=1),
            recorded_at=RECORDED,
            replay_cutoff=CUTOFF,
        )


def test_historical_registry_replay_uses_assembly_boundary(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    record = _assemble(service, "e1", "e2")
    assert (
        service.strict_known(
            logical_id=record.logical_id,
            effective_as_of=datetime(2028, 1, 1, tzinfo=UTC),
            known_by=datetime(2028, 1, 1, tzinfo=UTC),
        )
        == record
    )


def test_supply_and_rule_must_be_known_by_assembly_recording(tmp_path: Path) -> None:
    later = RECORDED + timedelta(days=1)
    service, _ = _service(
        tmp_path,
        supply=replace(_supply(), recorded_at=later, known_at=later),
        rule=replace(_rule(), recorded_at=later, known_at=later),
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="pathway or supply"):
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
