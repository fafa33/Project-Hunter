from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.evidence_assembly import (
    EVIDENCE_ASSEMBLY_MIGRATION_ID,
    AssembledEvidenceRepository,
    AssemblyConstituent,
    AuthoritativeEvidenceSemantics,
    CanonicalEvidenceAssemblyError,
    CanonicalEvidenceAssemblyService,
    EvidenceAssemblyPersistenceError,
    EvidenceShape,
    EvidenceShapeRegistry,
    EvidenceShapeRegistryError,
    MethodologyEvidenceInputContract,
)
from hunter.value_capture.models import EconomicClaimIdentity, FundamentalEvidenceRecord

DAY = datetime(2026, 1, 1, tzinfo=UTC)
CUTOFF = DAY + timedelta(days=10)


def _identity(**changes: str) -> EconomicClaimIdentity:
    values = {
        "entity_id": "entity:alpha",
        "economic_claim_id": "claim:fees",
        "asset_id": "asset:alpha",
        "representation_id": "representation:alpha",
        "token_id": "token:alpha",
    }
    values.update(changes)
    return EconomicClaimIdentity(**values)


def _evidence(
    suffix: str,
    start: int,
    end: int,
    *,
    amount: str = "10",
    identity: EconomicClaimIdentity | None = None,
    unit: str = "USD",
    pathway: str = "pathway:fees",
    known_day: int = 5,
    conflict_state: str = "none",
    quality_state: str = "accepted",
    source_id: str = "source:official",
    source_reference: str | None = None,
    raw_hash: str | None = None,
) -> FundamentalEvidenceRecord:
    return FundamentalEvidenceRecord(
        record_id=f"evidence:{suffix}",
        logical_id=f"evidence-logical:{suffix}",
        schema_version="supply-value-capture-v3.5.0",
        semantic_version="1.0.0",
        identity=identity or _identity(),
        evidence_type="official_disclosure",
        source_id=source_id,
        source_authority_tier="primary",
        source_reference=source_reference or f"https://example.test/{suffix}",
        parser_version="parser-v1",
        extracted_claim=f"period {suffix}",
        amount=amount,
        unit=unit,
        accounting_period_start=DAY + timedelta(days=start),
        accounting_period_end=DAY + timedelta(days=end),
        attribution_rule_id=pathway,
        source_methodology="official-period-total",
        source_record_id=f"source-record:{suffix}",
        source_record_version="1",
        entity_link_confidence="0.9",
        evidence_confidence="0.8",
        uncertainty="0.1",
        effective_at=DAY + timedelta(days=end),
        recorded_at=DAY + timedelta(days=known_day),
        known_at=DAY + timedelta(days=known_day),
        raw_content_hash=raw_hash or ("a" * 63 + suffix[-1]),
        quality_state=quality_state,  # type: ignore[arg-type]
        conflict_state=conflict_state,  # type: ignore[arg-type]
        content_hash=("b" * 63 + suffix[-1]),
        acquisition_id=f"acquisition:{suffix}",
    )


def _constituent(record: FundamentalEvidenceRecord, **changes: str) -> AssemblyConstituent:
    values = {
        "record": record,
        "shape_id": "official-period-specific-v1",
        "currency": "USD",
        "raw_unit": "USD",
        "accounting_meaning": "period_specific",
        "supply_basis_id": "supply-basis:fdv",
        "pathway_id": "pathway:fees",
    }
    values.update(changes)
    return AssemblyConstituent(**values)  # type: ignore[arg-type]


@pytest.fixture
def registry() -> EvidenceShapeRegistry:
    return EvidenceShapeRegistry(
        version="registry-v1",
        effective_at=DAY,
        recorded_at=DAY,
        known_at=DAY,
        quality_state="accepted",
        conflict_state="none",
        content_hash="registry-hash",
        shapes=(
            EvidenceShape(
                shape_id="official-period-specific-v1",
                registry_version="registry-v1",
                evidence_type="official_disclosure",
                accounting_meaning="period_specific",
                cadence="interval",
                composition_operation="exact_sum",
                active=True,
            ),
            EvidenceShape(
                shape_id="cumulative-v1",
                registry_version="registry-v1",
                evidence_type="official_disclosure",
                accounting_meaning="cumulative",
                cadence="interval",
                composition_operation="none",
                active=True,
            ),
            EvidenceShape(
                shape_id="inactive-v1",
                registry_version="registry-v1",
                evidence_type="official_disclosure",
                accounting_meaning="period_specific",
                cadence="interval",
                composition_operation="exact_sum",
                active=False,
            ),
        ),
    )


@pytest.fixture
def repository(tmp_path: Path) -> AssembledEvidenceRepository:
    return AssembledEvidenceRepository(tmp_path / "authority.sqlite")


class _NativeEvidenceQuery:
    def __init__(self) -> None:
        self.records: tuple[FundamentalEvidenceRecord, ...] = ()

    def overlapping_evidence(self, **_: object) -> tuple[FundamentalEvidenceRecord, ...]:
        return self.records


class _ContractAuthority:
    def __init__(self) -> None:
        self.contract = _contract()

    def strict_known_contract(self, **_: object) -> MethodologyEvidenceInputContract | None:
        return self.contract


class _RegistryAuthority:
    def __init__(self, registry: EvidenceShapeRegistry) -> None:
        self.registry = registry

    def strict_known_registry(self, **_: object) -> EvidenceShapeRegistry | None:
        return self.registry


class _SemanticsAuthority:
    def __init__(self) -> None:
        self.items: dict[str, AuthoritativeEvidenceSemantics] = {}

    def strict_known_semantics(self, *, evidence_record_id: str, **_: object) -> AuthoritativeEvidenceSemantics | None:
        return self.items.get(evidence_record_id)


@pytest.fixture
def native_query() -> _NativeEvidenceQuery:
    return _NativeEvidenceQuery()


@pytest.fixture
def service(
    repository: AssembledEvidenceRepository,
    registry: EvidenceShapeRegistry,
    native_query: _NativeEvidenceQuery,
) -> CanonicalEvidenceAssemblyService:
    return CanonicalEvidenceAssemblyService(
        repository=repository,
        native_evidence_query=native_query,
        methodology_contract_authority=_ContractAuthority(),
        evidence_shape_registry_authority=_RegistryAuthority(registry),
        evidence_semantics_authority=_SemanticsAuthority(),
    )


def _contract(**changes: object) -> MethodologyEvidenceInputContract:
    values: dict[str, object] = {
        "contract_id": "future-contract",
        "contract_version": "1.0.0",
        "accepts_assembled_evidence": True,
        "accepted_shape_ids": ("official-period-specific-v1",),
        "accepted_assembly_rule_versions": ("lossless-exact-coverage-v1",),
        "accounting_window_start": DAY,
        "accounting_window_end": DAY + timedelta(days=4),
        "exact_gap_free_non_overlapping_coverage_required": True,
        "allow_representation_boundary_crossing": False,
        "allow_pathway_boundary_crossing": False,
        "allow_supply_basis_boundary_crossing": False,
        "provenance_content_hash_required": True,
        "conflict_policy": "reject",
        "minimum_quality_state": "accepted",
        "entity_id": "entity:alpha",
        "representation_id": "representation:alpha",
        "currency": "USD",
        "unit": "USD",
        "missingness_behavior": "unavailable",
        "strict_known_required": True,
        "effective_at": DAY,
        "recorded_at": DAY,
        "known_at": DAY,
        "quality_state": "accepted",
        "conflict_state": "none",
        "content_hash": "contract-hash",
    }
    values.update(changes)
    return MethodologyEvidenceInputContract(**values)  # type: ignore[arg-type]


def _assemble(
    service: CanonicalEvidenceAssemblyService,
    constituents: tuple[AssemblyConstituent, ...] | None = None,
    **changes: object,
):
    selected = constituents or (
        _constituent(_evidence("1", 0, 2)),
        _constituent(_evidence("2", 2, 4)),
    )
    _seed_authorities(service, selected)
    values: dict[str, object] = {
        "constituents": selected,
        "accounting_window_start": DAY,
        "accounting_window_end": DAY + timedelta(days=4),
        "recorded_at": DAY + timedelta(days=6),
        "replay_cutoff": CUTOFF,
        "methodology_contract_id": "future-contract",
        "methodology_contract_version": "1.0.0",
        "evidence_shape_registry_version": "registry-v1",
    }
    values.update(changes)
    return service.assemble(**values)  # type: ignore[arg-type]


def _seed_authorities(service: CanonicalEvidenceAssemblyService, selected: tuple[AssemblyConstituent, ...]) -> None:
    service.native_evidence_query.records = tuple(item.record for item in selected)  # type: ignore[attr-defined]
    service.evidence_semantics_authority.items = {  # type: ignore[attr-defined]
        item.record.record_id: AuthoritativeEvidenceSemantics(
            evidence_record_id=item.record.record_id,
            evidence_record_version=item.record.semantic_version,
            shape_id=item.shape_id,
            currency=item.currency,
            raw_unit=item.raw_unit,
            accounting_meaning=item.accounting_meaning,
            supply_basis_id=item.supply_basis_id,
            pathway_id=item.pathway_id,
            effective_at=item.record.effective_at,
            recorded_at=item.record.recorded_at,
            known_at=item.record.known_at,
            quality_state="accepted",
            conflict_state="none",
            content_hash="semantics-hash",
        )
        for item in selected
    }


def test_valid_assembly_temporal_provenance_and_deterministic_order(service: CanonicalEvidenceAssemblyService) -> None:
    second = _constituent(_evidence("2", 2, 4, known_day=7, amount="3"))
    first = _constituent(_evidence("1", 0, 2, known_day=5, amount="2"))
    record = _assemble(
        service,
        (second, first),
        recorded_at=DAY + timedelta(days=7),
        replay_cutoff=DAY + timedelta(days=8),
    )

    assert record.evidence_marker == "assembled"
    assert record.amount == "5"
    assert record.constituent_record_ids == ("evidence:1", "evidence:2")
    assert record.constituent_versions == ("1.0.0", "1.0.0")
    assert record.constituent_source_references == ("https://example.test/1", "https://example.test/2")
    assert record.effective_at == DAY + timedelta(days=4)
    assert record.recorded_at == DAY + timedelta(days=7)
    assert record.known_at == DAY + timedelta(days=7)
    assert record.methodology_contract_id == "future-contract"


def test_methodology_must_explicitly_opt_in(service: CanonicalEvidenceAssemblyService) -> None:
    service.methodology_contract_authority.contract = _contract(accepts_assembled_evidence=False)  # type: ignore[attr-defined]
    with pytest.raises(CanonicalEvidenceAssemblyError, match="not opted"):
        _assemble(service)


def test_forged_constituent_semantics_are_rejected(service: CanonicalEvidenceAssemblyService) -> None:
    selected = (
        _constituent(_evidence("1", 0, 2)),
        _constituent(_evidence("2", 2, 4)),
    )
    _seed_authorities(service, selected)
    forged = (replace(selected[0], accounting_meaning="cumulative"), selected[1])
    with pytest.raises(CanonicalEvidenceAssemblyError, match="authoritative strict-known evidence semantics"):
        service.assemble(
            constituents=forged,
            accounting_window_start=DAY,
            accounting_window_end=DAY + timedelta(days=4),
            recorded_at=DAY + timedelta(days=6),
            replay_cutoff=CUTOFF,
            methodology_contract_id="future-contract",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="registry-v1",
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda items: (
                replace(items[0], record=replace(items[0].record, identity=_identity(entity_id="other"))),
                items[1],
            ),
            "entity",
        ),
        (
            lambda items: (
                items[0],
                replace(items[1], record=replace(items[1].record, identity=_identity(representation_id="other"))),
            ),
            "representation",
        ),
        (
            lambda items: (
                items[0],
                replace(items[1], record=replace(items[1].record, identity=_identity(asset_id="other"))),
            ),
            "asset or token",
        ),
        (
            lambda items: (
                items[0],
                replace(items[1], record=replace(items[1].record, attribution_rule_id="other"), pathway_id="other"),
            ),
            "pathway",
        ),
        (lambda items: (items[0], replace(items[1], currency="EUR")), "currency"),
        (
            lambda items: (
                items[0],
                replace(items[1], record=replace(items[1].record, unit="EUR"), raw_unit="EUR"),
            ),
            "unit",
        ),
        (lambda items: (items[0], replace(items[1], accounting_meaning="cumulative")), "accounting meaning"),
        (lambda items: (items[0], replace(items[1], supply_basis_id="other")), "supply-basis"),
    ],
)
def test_scope_invariants_fail_independently(service: CanonicalEvidenceAssemblyService, mutation, message: str) -> None:
    items = (_constituent(_evidence("1", 0, 2)), _constituent(_evidence("2", 2, 4)))
    with pytest.raises(CanonicalEvidenceAssemblyError, match=message):
        _assemble(service, mutation(items))


def test_gap_rejected(service: CanonicalEvidenceAssemblyService) -> None:
    with pytest.raises(CanonicalEvidenceAssemblyError, match="gap"):
        _assemble(service, (_constituent(_evidence("1", 0, 1)), _constituent(_evidence("2", 2, 4))))


def test_overlap_rejected(service: CanonicalEvidenceAssemblyService) -> None:
    with pytest.raises(CanonicalEvidenceAssemblyError, match="overlap"):
        _assemble(service, (_constituent(_evidence("1", 0, 3)), _constituent(_evidence("2", 2, 4))))


def test_divergent_duplicate_interval_rejected(service: CanonicalEvidenceAssemblyService) -> None:
    items = (
        _constituent(_evidence("1", 0, 2, amount="10")),
        _constituent(_evidence("2", 0, 2, amount="11")),
        _constituent(_evidence("3", 2, 4)),
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="divergent duplicate"):
        _assemble(service, items)


def test_identical_duplicate_interval_is_deduplicated(service: CanonicalEvidenceAssemblyService) -> None:
    one = _evidence("1", 0, 2, source_reference="same", raw_hash="d" * 64)
    duplicate = replace(
        one,
        record_id="evidence:2",
        logical_id="evidence-logical:2",
        source_record_id="source-record:2",
        acquisition_id="acquisition:2",
        content_hash="e" * 64,
    )
    record = _assemble(
        service,
        (
            _constituent(duplicate),
            _constituent(one),
            _constituent(_evidence("3", 2, 4)),
        ),
    )
    assert record.constituent_record_ids == ("evidence:1", "evidence:3")


@pytest.mark.parametrize("shape_id", ["missing-v1", "inactive-v1", "cumulative-v1"])
def test_registry_rejections_fail_closed(service: CanonicalEvidenceAssemblyService, shape_id: str) -> None:
    items = (
        replace(_constituent(_evidence("1", 0, 2)), shape_id=shape_id),
        _constituent(_evidence("2", 2, 4)),
    )
    with pytest.raises(CanonicalEvidenceAssemblyError):
        _assemble(service, items)


def test_registry_itself_rejects_version_mismatch() -> None:
    shape = EvidenceShape(
        shape_id="shape",
        registry_version="wrong",
        evidence_type="official_disclosure",
        accounting_meaning="period_specific",
        cadence="interval",
        composition_operation="exact_sum",
        active=True,
    )
    with pytest.raises(EvidenceShapeRegistryError, match="version"):
        EvidenceShapeRegistry(
            version="registry-v1",
            shapes=(shape,),
            effective_at=DAY,
            recorded_at=DAY,
            known_at=DAY,
            quality_state="accepted",
            conflict_state="none",
            content_hash="registry-hash",
        )


@pytest.mark.parametrize(
    "record_change",
    [
        {"conflict_state": "open"},
        {"quality_state": "partial"},
        {"known_day": 11},
    ],
)
def test_authority_and_strict_known_rejections(
    service: CanonicalEvidenceAssemblyService, record_change: dict[str, object]
) -> None:
    items = (
        _constituent(_evidence("1", 0, 2, **record_change)),
        _constituent(_evidence("2", 2, 4)),
    )
    with pytest.raises(CanonicalEvidenceAssemblyError):
        _assemble(service, items)


def test_replay_cutoff_rejects_assembly_recording_after_cutoff(service: CanonicalEvidenceAssemblyService) -> None:
    with pytest.raises(CanonicalEvidenceAssemblyError, match="assembly time"):
        _assemble(service, recorded_at=DAY + timedelta(days=11))


def test_repository_migration_insert_identical_and_divergent_duplicate(
    service: CanonicalEvidenceAssemblyService, repository: AssembledEvidenceRepository
) -> None:
    record = _assemble(service)
    assert repository.migration_ids() == (EVIDENCE_ASSEMBLY_MIGRATION_ID,)
    assert _assemble(service) == record
    assert repository.assemblies_for_constituent("evidence:1") == (record,)
    assert repository.assemblies_for_constituent("absent") == ()
    with pytest.raises(EvidenceAssemblyPersistenceError, match="divergent duplicate"):
        repository._insert_authorized(replace(record, amount="999"))


def test_logical_history_strict_known_replay_and_correction_lineage(
    service: CanonicalEvidenceAssemblyService, repository: AssembledEvidenceRepository
) -> None:
    original = _assemble(service)
    corrected_constituents = (
        _constituent(_evidence("1c", 0, 2, amount="12", known_day=7)),
        _constituent(_evidence("2c", 2, 4, known_day=7)),
    )
    successor = _assemble(
        service,
        corrected_constituents,
        recorded_at=DAY + timedelta(days=8),
        replay_cutoff=DAY + timedelta(days=9),
        supersedes_record_id=original.record_id,
        correction_reason="official source correction",
    )
    assert successor.logical_id == original.logical_id
    history = repository.history(original.logical_id)
    assert history[0] == original
    assert service.is_superseded(original.record_id)
    assert history[1] == successor
    assert (
        service.strict_known(
            logical_id=original.logical_id,
            effective_as_of=DAY + timedelta(days=4),
            known_by=DAY + timedelta(days=7),
        )
        == original
    )
    assert (
        service.strict_known(
            logical_id=original.logical_id,
            effective_as_of=DAY + timedelta(days=4),
            known_by=DAY + timedelta(days=9),
        )
        == successor
    )


def test_branching_correction_lineage_rejected(
    service: CanonicalEvidenceAssemblyService,
) -> None:
    original = _assemble(service)
    base = (
        _constituent(_evidence("1c", 0, 2, known_day=7)),
        _constituent(_evidence("2c", 2, 4, known_day=7)),
    )
    _assemble(
        service,
        base,
        recorded_at=DAY + timedelta(days=8),
        replay_cutoff=DAY + timedelta(days=9),
        supersedes_record_id=original.record_id,
        correction_reason="first correction",
    )
    other = (
        _constituent(_evidence("1d", 0, 2, known_day=8)),
        _constituent(_evidence("2d", 2, 4, known_day=8)),
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="branching"):
        _assemble(
            service,
            other,
            recorded_at=DAY + timedelta(days=9),
            replay_cutoff=DAY + timedelta(days=10),
            supersedes_record_id=original.record_id,
            correction_reason="branch",
        )


def test_correction_requires_later_clocks(
    service: CanonicalEvidenceAssemblyService,
) -> None:
    original = _assemble(service)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="clocks"):
        _assemble(
            service,
            (
                _constituent(_evidence("1c", 0, 2)),
                _constituent(_evidence("2c", 2, 4)),
            ),
            supersedes_record_id=original.record_id,
            correction_reason="not later",
        )


def test_unresolved_conflict_query_is_deterministic_and_empty_for_persistable_records(
    service: CanonicalEvidenceAssemblyService,
) -> None:
    assert service.unresolved_assembly_conflicts() == ()


def test_single_native_record_cannot_be_relabelled(service: CanonicalEvidenceAssemblyService) -> None:
    with pytest.raises(CanonicalEvidenceAssemblyError, match="multiple granular"):
        _assemble(service, (_constituent(_evidence("1", 0, 4)),))


def test_native_precedence_and_omitted_overlap_fail_closed(
    service: CanonicalEvidenceAssemblyService, native_query: _NativeEvidenceQuery
) -> None:
    selected = (
        _constituent(_evidence("1", 0, 2)),
        _constituent(_evidence("2", 2, 4)),
    )
    base = dict(
        constituents=selected,
        accounting_window_start=DAY,
        accounting_window_end=DAY + timedelta(days=4),
        recorded_at=DAY + timedelta(days=6),
        replay_cutoff=CUTOFF,
        methodology_contract_id="future-contract",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="registry-v1",
    )
    native = _evidence("native", 0, 4)
    # Scope-compatible (same pathway/currency/unit/shape as `selected`) so it
    # correctly qualifies as a competing native disclosure for precedence purposes.
    _seed_authorities(service, selected + (_constituent(native),))
    native_query.records = tuple(item.record for item in selected) + (native,)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="native evidence takes precedence"):
        service.assemble(**base)
    assert service.unresolved_assembly_conflicts()[0].conflict_state == "open"
    other = _evidence("other", 1, 3, raw_hash="a" * 64)
    _seed_authorities(service, selected + (_constituent(other),))
    native_query.records = tuple(item.record for item in selected) + (other,)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="omitted competing"):
        service.assemble(**base)
    assert len(service.unresolved_assembly_conflicts()) == 2


def test_cross_scope_native_record_does_not_trigger_false_precedence(
    service: CanonicalEvidenceAssemblyService, native_query: _NativeEvidenceQuery
) -> None:
    """A full-window record sharing only entity_id/economic_claim_id but belonging
    to an incompatible pathway/currency must never trigger native precedence or a
    false conflict -- it is not a 'qualifying' native disclosure for this scope."""
    selected = (
        _constituent(_evidence("1", 0, 2)),
        _constituent(_evidence("2", 2, 4)),
    )
    _seed_authorities(service, selected)
    unrelated_full_window = _evidence("unrelated", 0, 4, pathway="pathway:staking", unit="EUR", raw_hash="c" * 64)
    native_query.records = tuple(item.record for item in selected) + (unrelated_full_window,)

    record = service.assemble(
        constituents=selected,
        accounting_window_start=DAY,
        accounting_window_end=DAY + timedelta(days=4),
        recorded_at=DAY + timedelta(days=6),
        replay_cutoff=CUTOFF,
        methodology_contract_id="future-contract",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="registry-v1",
    )
    assert record.source_count == 2
    assert service.unresolved_assembly_conflicts() == ()


def test_cross_scope_native_record_does_not_trigger_false_overlap_conflict(
    service: CanonicalEvidenceAssemblyService, native_query: _NativeEvidenceQuery
) -> None:
    """A partial-window record sharing only entity_id/economic_claim_id but
    belonging to an incompatible pathway must never trigger the omitted-competing/
    overlapping conflict path either."""
    selected = (
        _constituent(_evidence("1", 0, 2)),
        _constituent(_evidence("2", 2, 4)),
    )
    _seed_authorities(service, selected)
    unrelated_overlapping = _evidence("unrelated-partial", 1, 3, pathway="pathway:staking", raw_hash="d" * 64)
    native_query.records = tuple(item.record for item in selected) + (unrelated_overlapping,)

    record = service.assemble(
        constituents=selected,
        accounting_window_start=DAY,
        accounting_window_end=DAY + timedelta(days=4),
        recorded_at=DAY + timedelta(days=6),
        replay_cutoff=CUTOFF,
        methodology_contract_id="future-contract",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="registry-v1",
    )
    assert record.source_count == 2
    assert service.unresolved_assembly_conflicts() == ()


def test_second_independent_root_for_existing_logical_id_is_rejected(
    service: CanonicalEvidenceAssemblyService, native_query: _NativeEvidenceQuery
) -> None:
    """Proves: (1) first root succeeds; (2) a second independent root for the same
    logical_id fails; (3) a valid explicit correction still succeeds; (4) strict_known
    never has to silently choose between parallel roots because none can exist."""
    selected_a = (
        _constituent(_evidence("1a", 0, 2, known_day=5)),
        _constituent(_evidence("2a", 2, 4, known_day=5)),
    )
    root1 = _assemble(service, selected_a, recorded_at=DAY + timedelta(days=6))
    assert service.repository.history(root1.logical_id) == (root1,)

    selected_b = (
        _constituent(_evidence("1b", 0, 2, known_day=7, amount="20")),
        _constituent(_evidence("2b", 2, 4, known_day=7, amount="30")),
    )
    _seed_authorities(service, selected_b)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="root record already exists"):
        service.assemble(
            constituents=selected_b,
            accounting_window_start=DAY,
            accounting_window_end=DAY + timedelta(days=4),
            recorded_at=DAY + timedelta(days=8),
            replay_cutoff=CUTOFF,
            methodology_contract_id="future-contract",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="registry-v1",
            # supersedes_record_id intentionally omitted
        )
    assert service.repository.history(root1.logical_id) == (root1,)

    correction = service.assemble(
        constituents=selected_b,
        accounting_window_start=DAY,
        accounting_window_end=DAY + timedelta(days=4),
        recorded_at=DAY + timedelta(days=8),
        replay_cutoff=CUTOFF,
        methodology_contract_id="future-contract",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="registry-v1",
        supersedes_record_id=root1.record_id,
        correction_reason="corrected constituent amounts",
    )
    assert correction.logical_id == root1.logical_id
    assert correction.supersedes_record_id == root1.record_id

    tip = service.strict_known(logical_id=root1.logical_id, effective_as_of=CUTOFF, known_by=CUTOFF)
    assert tip is not None
    assert tip.record_id == correction.record_id
    candidates = tuple(
        record
        for record in service.repository.history(root1.logical_id)
        if record.effective_at <= CUTOFF and record.recorded_at <= CUTOFF and record.known_at <= CUTOFF
    )
    tips = [
        record for record in candidates if not any(item.supersedes_record_id == record.record_id for item in candidates)
    ]
    assert len(tips) == 1, "exactly one lineage tip must exist -- no silent choice among parallel roots is possible"


def test_recorded_at_equal_to_window_end_and_latest_known_is_accepted(
    service: CanonicalEvidenceAssemblyService, native_query: _NativeEvidenceQuery
) -> None:
    """Boundary case: recorded_at exactly equal to both accounting_window_end and
    the latest constituent known_at must be accepted (not treated as a violation)."""
    selected = (
        _constituent(_evidence("1", 0, 2, known_day=4)),
        _constituent(_evidence("2", 2, 4, known_day=4)),
    )
    _seed_authorities(service, selected)
    record = service.assemble(
        constituents=selected,
        accounting_window_start=DAY,
        accounting_window_end=DAY + timedelta(days=4),
        recorded_at=DAY + timedelta(days=4),
        replay_cutoff=CUTOFF,
        methodology_contract_id="future-contract",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="registry-v1",
    )
    assert record.effective_at == DAY + timedelta(days=4)
    assert record.recorded_at == DAY + timedelta(days=4)
    assert record.known_at == DAY + timedelta(days=4)


def test_recorded_at_before_accounting_window_end_is_rejected(
    service: CanonicalEvidenceAssemblyService, native_query: _NativeEvidenceQuery
) -> None:
    selected = (
        _constituent(_evidence("1", 0, 2, known_day=4)),
        _constituent(_evidence("2", 2, 4, known_day=4)),
    )
    _seed_authorities(service, selected)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="must not precede accounting_window_end"):
        service.assemble(
            constituents=selected,
            accounting_window_start=DAY,
            accounting_window_end=DAY + timedelta(days=4),
            recorded_at=DAY + timedelta(days=1),
            replay_cutoff=CUTOFF,
            methodology_contract_id="future-contract",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="registry-v1",
        )


def test_recorded_at_before_latest_constituent_known_at_is_rejected(
    service: CanonicalEvidenceAssemblyService, native_query: _NativeEvidenceQuery
) -> None:
    selected = (
        _constituent(_evidence("1", 0, 2, known_day=7)),
        _constituent(_evidence("2", 2, 4, known_day=7)),
    )
    _seed_authorities(service, selected)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="must not precede the latest constituent known_at"):
        service.assemble(
            constituents=selected,
            accounting_window_start=DAY,
            accounting_window_end=DAY + timedelta(days=4),
            recorded_at=DAY + timedelta(days=4),  # equals window end but precedes known_day=7
            replay_cutoff=CUTOFF,
            methodology_contract_id="future-contract",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="registry-v1",
        )


def test_rejection_is_contract_driven_not_id_driven(
    service: CanonicalEvidenceAssemblyService, native_query: _NativeEvidenceQuery
) -> None:
    """A contract that declares accepts_assembled_evidence=False must be rejected
    with the contract-driven message regardless of its contract_id -- proving the
    decision is sourced from the fetched contract's own fields, not a literal ID."""
    selected = (
        _constituent(_evidence("1", 0, 2)),
        _constituent(_evidence("2", 2, 4)),
    )
    _seed_authorities(service, selected)
    service.methodology_contract_authority.contract = _contract(  # type: ignore[attr-defined]
        contract_id="some-arbitrary-methodology-id",
        contract_version="1.0.0",
        accepts_assembled_evidence=False,
    )
    with pytest.raises(CanonicalEvidenceAssemblyError, match="not opted into assembled evidence"):
        service.assemble(
            constituents=selected,
            accounting_window_start=DAY,
            accounting_window_end=DAY + timedelta(days=4),
            recorded_at=DAY + timedelta(days=6),
            replay_cutoff=CUTOFF,
            methodology_contract_id="some-arbitrary-methodology-id",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="registry-v1",
        )


def test_future_version_of_current_methodology_id_is_accepted_when_contract_permits(
    service: CanonicalEvidenceAssemblyService, native_query: _NativeEvidenceQuery
) -> None:
    """A future, ADR-0022-authorized version of discounted-value-capture-flow-v1
    that explicitly declares acceptance must be honored -- Evidence Assembly must
    not hardcode a rejection keyed on that methodology's identity."""
    selected = (
        _constituent(_evidence("1", 0, 2)),
        _constituent(_evidence("2", 2, 4)),
    )
    _seed_authorities(service, selected)
    service.methodology_contract_authority.contract = _contract(  # type: ignore[attr-defined]
        contract_id="discounted-value-capture-flow-v1",
        contract_version="2.0.0",
        accepts_assembled_evidence=True,
    )
    record = service.assemble(
        constituents=selected,
        accounting_window_start=DAY,
        accounting_window_end=DAY + timedelta(days=4),
        recorded_at=DAY + timedelta(days=6),
        replay_cutoff=CUTOFF,
        methodology_contract_id="discounted-value-capture-flow-v1",
        methodology_contract_version="2.0.0",
        evidence_shape_registry_version="registry-v1",
    )
    assert record.methodology_contract_id == "discounted-value-capture-flow-v1"
    assert record.methodology_contract_version == "2.0.0"


def test_finer_granularity_is_deterministically_preferred(
    service: CanonicalEvidenceAssemblyService, native_query: _NativeEvidenceQuery
) -> None:
    fine = tuple(_constituent(_evidence(str(index + 1), index, index + 1)) for index in range(4))
    coarse = (_evidence("5", 0, 2), _evidence("6", 2, 4))
    _seed_authorities(service, fine + tuple(_constituent(record) for record in coarse))
    native_query.records = tuple(item.record for item in fine) + coarse
    record = service.assemble(
        constituents=fine,
        accounting_window_start=DAY,
        accounting_window_end=DAY + timedelta(days=4),
        recorded_at=DAY + timedelta(days=6),
        replay_cutoff=CUTOFF,
        methodology_contract_id="future-contract",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="registry-v1",
    )
    assert record.constituent_record_ids == ("evidence:1", "evidence:2", "evidence:3", "evidence:4")
    overlapping = _evidence("f", 1, 3)
    # Scope-compatible with `fine` so it correctly qualifies as a competing
    # overlapping disclosure rather than being excluded as unrelated.
    _seed_authorities(service, fine + tuple(_constituent(record) for record in coarse) + (_constituent(overlapping),))
    native_query.records = tuple(item.record for item in fine) + coarse + (overlapping,)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="omitted competing"):
        service.assemble(
            constituents=fine,
            accounting_window_start=DAY,
            accounting_window_end=DAY + timedelta(days=4),
            recorded_at=DAY + timedelta(days=7),
            replay_cutoff=CUTOFF,
            methodology_contract_id="future-contract",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="registry-v1",
        )

    coarse_bindings = tuple(_constituent(record) for record in coarse)
    _seed_authorities(service, coarse_bindings + fine)
    native_query.records = coarse + tuple(item.record for item in fine)
    with pytest.raises(CanonicalEvidenceAssemblyError, match="finer-granularity"):
        service.assemble(
            constituents=coarse_bindings,
            accounting_window_start=DAY,
            accounting_window_end=DAY + timedelta(days=4),
            recorded_at=DAY + timedelta(days=7),
            replay_cutoff=CUTOFF,
            methodology_contract_id="future-contract",
            methodology_contract_version="1.0.0",
            evidence_shape_registry_version="registry-v1",
        )


def test_finest_of_three_complete_granularities_is_preferred(
    service: CanonicalEvidenceAssemblyService, native_query: _NativeEvidenceQuery
) -> None:
    fine = tuple(_constituent(_evidence(f"{index + 1:x}", index, index + 1, known_day=7)) for index in range(6))
    medium = tuple(_evidence(f"{index + 7:x}", index * 2, index * 2 + 2, known_day=7) for index in range(3))
    coarse = tuple(_evidence(f"{index + 10:x}", index * 3, index * 3 + 3, known_day=7) for index in range(2))
    _seed_authorities(
        service,
        fine + tuple(_constituent(record) for record in medium + coarse),
    )
    service.methodology_contract_authority.contract = _contract(  # type: ignore[attr-defined]
        accounting_window_end=DAY + timedelta(days=6)
    )
    native_query.records = tuple(item.record for item in fine) + medium + coarse
    record = service.assemble(
        constituents=fine,
        accounting_window_start=DAY,
        accounting_window_end=DAY + timedelta(days=6),
        recorded_at=DAY + timedelta(days=7),
        replay_cutoff=CUTOFF,
        methodology_contract_id="future-contract",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="registry-v1",
    )
    assert record.source_count == 6


def test_no_valuation_or_methodology_authority_is_activated() -> None:
    from hunter.valuation.service import CanonicalValuationService

    assert not hasattr(CanonicalValuationService, "assemble")
    assert not hasattr(CanonicalValuationService, "compose")
    assert not hasattr(CanonicalValuationService, "correct_assembled_evidence")
    service_source_names = set(CanonicalEvidenceAssemblyService.assemble.__code__.co_names)
    assert "estimate_fair_value" not in service_source_names
    assert "CanonicalValuationService" not in service_source_names
