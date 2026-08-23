from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.market_validation.models import EngineValidationSource, ProjectValidationTarget
from hunter.market_validation.runner import EvidenceBackedProjectExecutor
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_sqlite_engine
from hunter.valuation_methodology.models import (
    AUTHORIZING_ADR_REFERENCE,
    CANONICAL_AUTHORITY_ID,
    PERMITTED_MODEL_IDENTIFIER,
    REQUIRED_CORRELATION_GROUP,
    REQUIRED_HORIZON_DAYS,
    VALUATION_METHODOLOGY_SCHEMA_VERSION,
    ValuationMethodologySnapshot,
)
from hunter.valuation_methodology.repository import (
    VALUATION_METHODOLOGY_MIGRATION_ID,
    ValuationMethodologyRepository,
)
from hunter.valuation_methodology.service import (
    CanonicalValuationMethodologyAuthority,
    CanonicalValuationMethodologyAuthorityError,
    ValuationMethodologyIntegrityError,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def setup(tmp_path: Path) -> tuple[CanonicalValuationMethodologyAuthority, ValuationMethodologyRepository]:
    repository = ValuationMethodologyRepository(tmp_path / "valuation-methodology.sqlite")
    service = CanonicalValuationMethodologyAuthority(repository=repository, application_root=tmp_path)
    return service, repository


def payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "entity_class_criteria_id": "adr-0022-first-entity-class-v1",
        "entity_class_criteria_version": "1.0.0",
        "currency": "usd",
        "discount_rate_policy_id": "canonical-discount-rate-v1",
        "discount_rate_policy_version": "1.0.0",
        "sensitivity_policy_id": "canonical-sensitivity-v1",
        "sensitivity_policy_version": "1.0.0",
        "supply_basis_selection_rule": "fully_diluted_supply",
        "effective_at": NOW,
        "recorded_at": NOW,
        "known_at": NOW,
    }
    base.update(overrides)
    return base


def model_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "record_id": "r",
        "logical_id": "l",
        "schema_version": VALUATION_METHODOLOGY_SCHEMA_VERSION,
        "semantic_version": "1.0.0",
        "effective_at": NOW,
        "recorded_at": NOW,
        "known_at": NOW,
        "content_hash": "h" * 64,
        "quality_state": "accepted",
        "conflict_state": "none",
        "entity_class_criteria_id": "adr-0022-first-entity-class-v1",
        "entity_class_criteria_version": "1.0.0",
        "permitted_model_identifier": PERMITTED_MODEL_IDENTIFIER,
        "horizon_days": REQUIRED_HORIZON_DAYS,
        "currency": "usd",
        "discount_rate_policy_id": "canonical-discount-rate-v1",
        "discount_rate_policy_version": "1.0.0",
        "sensitivity_policy_id": "canonical-sensitivity-v1",
        "sensitivity_policy_version": "1.0.0",
        "supply_basis_selection_rule": "fully_diluted_supply",
        "normalization_policy_id": None,
        "correlation_group": REQUIRED_CORRELATION_GROUP,
        "authorizing_adr_reference": AUTHORIZING_ADR_REFERENCE,
        "authorized_by": CANONICAL_AUTHORITY_ID,
    }
    base.update(overrides)
    return base


# 1. Fixed-parameter rejection tests -----------------------------------------------


def test_fixed_parameters_cannot_be_supplied_through_the_service(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    record = service.persist_methodology(**payload())
    assert record.permitted_model_identifier == PERMITTED_MODEL_IDENTIFIER
    assert record.horizon_days == REQUIRED_HORIZON_DAYS
    assert record.correlation_group == REQUIRED_CORRELATION_GROUP
    assert record.normalization_policy_id is None
    assert record.authorizing_adr_reference == AUTHORIZING_ADR_REFERENCE
    assert record.authorized_by == CANONICAL_AUTHORITY_ID
    # persist_methodology's signature has no such parameter at all: there is no code
    # path through the service's public API that can even attempt an override.
    with pytest.raises(TypeError):
        service.persist_methodology(**payload(), horizon_days=100)  # type: ignore[call-arg]


def test_model_rejects_forged_permitted_model_identifier(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="permitted_model_identifier"):
        ValuationMethodologySnapshot(**model_kwargs(permitted_model_identifier="some-other-model"))


def test_model_rejects_forged_horizon_days(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="horizon_days"):
        ValuationMethodologySnapshot(**model_kwargs(horizon_days=100))


def test_model_rejects_forged_correlation_group(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="correlation_group"):
        ValuationMethodologySnapshot(**model_kwargs(correlation_group="some-other-group"))


def test_model_rejects_forged_authorizing_adr_reference(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="authorizing_adr_reference"):
        ValuationMethodologySnapshot(**model_kwargs(authorizing_adr_reference="ADR-9999"))


# 2. Blank policy/reference rejection tests -----------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "entity_class_criteria_id",
        "entity_class_criteria_version",
        "currency",
        "discount_rate_policy_id",
        "discount_rate_policy_version",
        "sensitivity_policy_id",
        "sensitivity_policy_version",
        "supply_basis_selection_rule",
        "authorized_by",
    ],
)
def test_blank_required_fields_are_rejected(field: str) -> None:
    with pytest.raises(ValueError, match=f"{field} must not be blank"):
        ValuationMethodologySnapshot(**model_kwargs(**{field: "  "}))


# 3. normalization_policy_id non-None rejection --------------------------------------


def test_normalization_policy_id_must_remain_unavailable_through_milestone_2() -> None:
    with pytest.raises(ValueError, match="normalization_policy_id"):
        ValuationMethodologySnapshot(**model_kwargs(normalization_policy_id="some-future-policy-v1"))


# 4. Bitemporal chronology tests ------------------------------------------------------


def test_chronology_requires_effective_before_recorded_before_known() -> None:
    with pytest.raises(ValueError, match="effective_at must be <= recorded_at"):
        ValuationMethodologySnapshot(**model_kwargs(effective_at=NOW + timedelta(days=1), recorded_at=NOW))
    with pytest.raises(ValueError, match="recorded_at must be <= known_at"):
        ValuationMethodologySnapshot(**model_kwargs(recorded_at=NOW + timedelta(days=1), known_at=NOW))


def test_chronology_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ValuationMethodologySnapshot(**model_kwargs(effective_at=datetime(2026, 1, 1)))


# 5. Persistence round-trip fidelity --------------------------------------------------


def test_persistence_round_trip_preserves_all_fields(tmp_path: Path) -> None:
    service, repository = setup(tmp_path)
    record = service.persist_methodology(**payload())
    restored = repository.get(record.record_id)
    assert restored == record
    assert repository.count("valuation_methodology_snapshots") == 1
    assert repository.migration_ids() == (VALUATION_METHODOLOGY_MIGRATION_ID,)


# 6. Logical history ordering ----------------------------------------------------------


def test_methodology_history_orders_deterministically(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    original = service.persist_methodology(**payload())
    corrected = service.persist_methodology(
        **payload(
            discount_rate_policy_version="1.1.0",
            recorded_at=NOW + timedelta(days=1),
            known_at=NOW + timedelta(days=1),
        ),
        supersedes_record_id=original.record_id,
        correction_reason="Discount-rate policy revision",
    )
    assert service.methodology_history(original.logical_id) == (original, corrected)
    assert service.methodology_history("0" * 64) == ()


# 7. Strict-known replay across a correction boundary -----------------------------------


def test_strict_known_replay_across_correction_boundary(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    original = service.persist_methodology(**payload())
    correction_known = NOW + timedelta(days=10)
    corrected = service.persist_methodology(
        **payload(
            discount_rate_policy_version="1.1.0",
            recorded_at=correction_known,
            known_at=correction_known,
        ),
        supersedes_record_id=original.record_id,
        correction_reason="Discount-rate policy revision",
    )

    before = service.strict_known_methodology(effective_as_of=correction_known, known_by=NOW)
    after = service.strict_known_methodology(effective_as_of=correction_known, known_by=correction_known)

    assert before == original
    assert after == corrected


# 8. Correction timestamp ordering -------------------------------------------------------


def test_correction_recorded_at_not_advancing_predecessor_is_rejected(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    original = service.persist_methodology(**payload())
    with pytest.raises(ValuationMethodologyIntegrityError, match="recorded_at must follow predecessor"):
        service.persist_methodology(
            **payload(discount_rate_policy_version="1.1.0"),
            supersedes_record_id=original.record_id,
            correction_reason="Attempted non-advancing correction",
        )


def test_correction_known_at_not_advancing_predecessor_is_rejected(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    # A gap between the predecessor's own recorded_at and known_at is required to make
    # this scenario constructible at all: recorded_at <= known_at is always enforced on
    # every record individually, so a correction cannot advance past a predecessor's
    # known_at without advancing past its recorded_at too, unless the predecessor itself
    # has such a gap.
    original = service.persist_methodology(**payload(recorded_at=NOW, known_at=NOW + timedelta(hours=1)))
    with pytest.raises(ValuationMethodologyIntegrityError, match="known_at must follow predecessor"):
        service.persist_methodology(
            **payload(
                discount_rate_policy_version="1.1.0",
                recorded_at=NOW + timedelta(minutes=30),
                known_at=NOW + timedelta(hours=1),
            ),
            supersedes_record_id=original.record_id,
            correction_reason="Attempted non-advancing known_at",
        )


# 9. Anti-branching ------------------------------------------------------------------------


def test_branching_correction_lineage_is_rejected(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    original = service.persist_methodology(**payload())
    service.persist_methodology(
        **payload(
            discount_rate_policy_version="1.1.0",
            recorded_at=NOW + timedelta(days=1),
            known_at=NOW + timedelta(days=1),
        ),
        supersedes_record_id=original.record_id,
        correction_reason="First correction",
    )
    with pytest.raises(ValuationMethodologyIntegrityError, match="branching"):
        service.persist_methodology(
            **payload(
                discount_rate_policy_version="1.2.0",
                recorded_at=NOW + timedelta(days=2),
                known_at=NOW + timedelta(days=2),
            ),
            supersedes_record_id=original.record_id,
            correction_reason="Competing correction",
        )


def test_second_root_record_for_same_logical_id_is_rejected(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    service.persist_methodology(**payload())
    with pytest.raises(ValuationMethodologyIntegrityError, match="root methodology record already exists"):
        service.persist_methodology(**payload(discount_rate_policy_version="9.9.9"))


# 10. Idempotent identical retry -------------------------------------------------------------


def test_retrying_identical_methodology_persistence_is_idempotent(tmp_path: Path) -> None:
    service, repository = setup(tmp_path)
    first = service.persist_methodology(**payload())
    second = service.persist_methodology(**payload())
    assert first == second
    assert first.record_id == second.record_id
    assert repository.count("valuation_methodology_snapshots") == 1


def test_retrying_identical_correction_is_idempotent(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    original = service.persist_methodology(**payload())
    correction_kwargs = payload(
        discount_rate_policy_version="1.1.0",
        recorded_at=NOW + timedelta(days=1),
        known_at=NOW + timedelta(days=1),
    )
    first = service.persist_methodology(
        **correction_kwargs, supersedes_record_id=original.record_id, correction_reason="Official correction"
    )
    second = service.persist_methodology(
        **correction_kwargs, supersedes_record_id=original.record_id, correction_reason="Official correction"
    )
    assert first == second
    assert first.record_id == second.record_id


# 11. Concurrent correction race ---------------------------------------------------------------


def test_concurrent_corrections_cannot_branch_lineage(tmp_path: Path) -> None:
    service, repository = setup(tmp_path)
    original = service.persist_methodology(**payload())
    second_service = CanonicalValuationMethodologyAuthority(
        repository=ValuationMethodologyRepository(repository.path), application_root=tmp_path
    )

    def attempt(item: tuple[CanonicalValuationMethodologyAuthority, str, datetime]):
        active_service, version, known_at = item
        try:
            return active_service.persist_methodology(
                **payload(discount_rate_policy_version=version, recorded_at=known_at, known_at=known_at),
                supersedes_record_id=original.record_id,
                correction_reason="Concurrent correction",
            )
        except ValuationMethodologyIntegrityError as exc:
            return exc

    first_known = NOW + timedelta(days=1)
    second_known = NOW + timedelta(days=2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(
            executor.map(
                attempt,
                ((service, "1.1.0", first_known), (second_service, "1.2.0", second_known)),
            )
        )

    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, ValuationMethodologyIntegrityError) for item in outcomes) == 1


# 12. Unauthorized/unattested write rejection ----------------------------------------------------


def test_unattested_construction_without_application_root_env_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HUNTER_APPLICATION_ROOT", raising=False)
    repository = ValuationMethodologyRepository(tmp_path / "valuation-methodology.sqlite")
    with pytest.raises(CanonicalValuationMethodologyAuthorityError, match="HUNTER_APPLICATION_ROOT"):
        CanonicalValuationMethodologyAuthority(repository=repository)


def test_relative_application_root_is_rejected(tmp_path: Path) -> None:
    repository = ValuationMethodologyRepository(tmp_path / "valuation-methodology.sqlite")
    with pytest.raises(CanonicalValuationMethodologyAuthorityError, match="absolute"):
        CanonicalValuationMethodologyAuthority(repository=repository, application_root=Path("relative/root"))


def test_application_root_env_var_is_honored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    repository = ValuationMethodologyRepository(tmp_path / "valuation-methodology.sqlite")
    service = CanonicalValuationMethodologyAuthority(repository=repository)
    record = service.persist_methodology(**payload())
    assert record.authorized_by == CANONICAL_AUTHORITY_ID


# 13. Malformed-row fault isolation -----------------------------------------------------------


def test_malformed_row_does_not_deny_reads_of_valid_methodology_records(tmp_path: Path) -> None:
    service, repository = setup(tmp_path)
    valid = service.persist_methodology(**payload())

    engine = create_sqlite_engine(repository.path)
    session = SessionFactory(engine).create()
    try:
        malformed_payload = dict(payload())
        malformed_payload["record_id"] = "malformed-methodology"
        malformed_payload["logical_id"] = "malformed-logical-id"
        # Omit required v1 fields entirely to simulate a legacy/incompatible row.
        RepositoryFactory(session).snapshots().save(
            SnapshotRecord(
                id="malformed-methodology",
                created_at=NOW,
                effective_at=NOW,
                snapshot_type="valuation-methodology-snapshot",
                target_id="malformed-logical-id",
                record_ids=("malformed-methodology",),
                payload=malformed_payload,
                metadata={"known_at": NOW.isoformat()},
            )
        )
        session.commit()
    finally:
        session.close()
        engine.dispose()

    assert service.methodology_history(valid.logical_id) == (valid,)
    assert service.strict_known_methodology(effective_as_of=NOW, known_by=NOW) == valid


# 14. Non-activation test -----------------------------------------------------------------------


def test_persisting_methodology_does_not_activate_valuation_in_market_validation(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    service.persist_methodology(**payload())

    source = EngineValidationSource(
        engine="valuation",
        score=0.95,
        confidence=0.95,
        timestamp=NOW,
        freshness=0.95,
        source_record_ids=("record:valuation",),
        evidence_ids=("evidence:valuation",),
        repository_ids=("repository:valuation",),
        source="persisted-upstream",
        collector="repository",
    )
    result = EvidenceBackedProjectExecutor(NOW, {"bitcoin": (source,)}).execute_project(
        ProjectValidationTarget("bitcoin", "Bitcoin", "store-of-value"), run_id="run"
    )

    assert result.valuation == 0.0
    assert "valuation" in result.missing_evidence
    rejected = next(item for item in result.engine_sources if item.engine == "valuation")
    assert rejected.status == "UNAVAILABLE"
    assert rejected.warnings == ("contract_unavailable:valuation",)


# 15. Methodology Contract Authority tests (Issue #194) ----------------------------------------


def contract_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "contract_id": "test-contract-v1",
        "contract_version": "1.0.0",
        "methodology_logical_id": "test-methodology-logical-id",
        "accepts_assembled_evidence": True,
        "accepted_shape_ids": ("shape-quarterly-v1", "shape-annual-v1"),
        "accepted_assembly_rule_versions": ("lossless-exact-coverage-v1",),
        "accounting_window_start": NOW,
        "accounting_window_end": NOW + timedelta(days=365),
        "entity_id": "entity-btc",
        "representation_id": "rep-spot",
        "value_capture_pathway_id": "pathway-direct-spot-v1",
        "currency": "usd",
        "unit": "usd_amount",
        "effective_at": NOW,
        "recorded_at": NOW,
        "known_at": NOW,
    }
    base.update(overrides)
    return base


def test_persist_and_get_contract_round_trip(tmp_path: Path) -> None:
    service, repository = setup(tmp_path)
    contract = service.persist_contract(**contract_payload())
    assert contract.contract_id == "test-contract-v1"
    assert contract.contract_version == "1.0.0"
    assert contract.accepts_assembled_evidence is True
    assert contract.value_capture_pathway_id == "pathway-direct-spot-v1"

    fetched = service.get_contract("test-contract-v1", "1.0.0")
    assert fetched == contract
    assert repository.get_contract("test-contract-v1", "1.0.0") == contract
    assert repository.count("valuation_methodology_contracts") == 1


def test_contract_pathway_binding_and_isolation(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    c1 = service.persist_contract(
        **contract_payload(contract_id="contract-spot", value_capture_pathway_id="pathway-spot")
    )
    c2 = service.persist_contract(
        **contract_payload(contract_id="contract-derivatives", value_capture_pathway_id="pathway-derivatives")
    )

    assert c1.value_capture_pathway_id == "pathway-spot"
    assert c2.value_capture_pathway_id == "pathway-derivatives"
    assert c1.content_hash != c2.content_hash


def test_strict_known_contract_returns_unavailable_when_governing_snapshot_does_not_accept_assembly(
    tmp_path: Path,
) -> None:
    service, _ = setup(tmp_path)
    # Methodology snapshot created without accepts_assembled_evidence (default False)
    m = service.persist_methodology(**payload())
    service.persist_contract(**contract_payload(methodology_logical_id=m.logical_id))

    # Should return None because governing snapshot has accepts_assembled_evidence = False
    assert (
        service.strict_known_contract(
            contract_id="test-contract-v1", contract_version="1.0.0", effective_as_of=NOW, known_by=NOW
        )
        is None
    )


def test_strict_known_contract_returns_contract_when_governing_snapshot_accepts_assembly(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    # Methodology snapshot created with accepts_assembled_evidence = True
    m = service.persist_methodology(**payload(accepts_assembled_evidence=True))
    contract = service.persist_contract(**contract_payload(methodology_logical_id=m.logical_id))

    result = service.strict_known_contract(
        contract_id="test-contract-v1", contract_version="1.0.0", effective_as_of=NOW, known_by=NOW
    )
    assert result == contract


def test_strict_known_contract_governing_snapshot_logical_id_binding(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    # Family A: accepts_assembled_evidence = False
    m_a = service.persist_methodology(**payload(entity_class_criteria_id="class-a", accepts_assembled_evidence=False))
    contract_a = service.persist_contract(
        **contract_payload(contract_id="contract-a", methodology_logical_id=m_a.logical_id)
    )
    assert contract_a.contract_id == "contract-a"

    # Family B: created later, accepts_assembled_evidence = True
    later = NOW + timedelta(days=1)
    service.persist_methodology(
        **payload(
            entity_class_criteria_id="class-b",
            accepts_assembled_evidence=True,
            effective_at=later,
            recorded_at=later,
            known_at=later,
        )
    )

    # Query contract A at `later`: its governing snapshot (family A) has accepts_assembled_evidence = False.
    # The presence of a newer snapshot for family B MUST NOT activate contract A.
    res = service.strict_known_contract(
        contract_id="contract-a",
        contract_version="1.0.0",
        effective_as_of=later,
        known_by=later,
    )
    assert res is None


def test_strict_known_contract_requires_explicit_effective_as_of_and_delayed_replay(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    m = service.persist_methodology(**payload(accepts_assembled_evidence=True))
    v1 = service.persist_contract(**contract_payload(methodology_logical_id=m.logical_id))

    later = NOW + timedelta(days=10)
    v2 = service.persist_contract(
        **contract_payload(
            contract_version="1.1.0",
            methodology_logical_id=m.logical_id,
            effective_at=later,
            recorded_at=later,
            known_at=later,
        ),
        supersedes_contract_version="1.0.0",
        correction_reason="Updated accepted shape IDs",
    )

    # Historical cutoff before v2 effective date returns v1
    assert (
        service.strict_known_contract(
            contract_id="test-contract-v1",
            contract_version="1.0.0",
            effective_as_of=NOW,
            known_by=later,
        )
        == v1
    )

    # Cutoff after v2 effective date returns v2 when queried for version 1.1.0
    assert (
        service.strict_known_contract(
            contract_id="test-contract-v1",
            contract_version="1.1.0",
            effective_as_of=later,
            known_by=later,
        )
        == v2
    )


def test_contract_correction_lineage_append_only(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    m = service.persist_methodology(**payload(accepts_assembled_evidence=True))
    v1 = service.persist_contract(**contract_payload(methodology_logical_id=m.logical_id))

    later = NOW + timedelta(days=10)
    v2 = service.persist_contract(
        **contract_payload(
            contract_version="1.1.0",
            methodology_logical_id=m.logical_id,
            recorded_at=later,
            known_at=later,
        ),
        supersedes_contract_version="1.0.0",
        correction_reason="Updated accepted shape IDs",
    )

    history = service.contract_history("test-contract-v1")
    assert history == (v1, v2)

    assert (
        service.strict_known_contract(
            contract_id="test-contract-v1",
            contract_version="1.0.0",
            effective_as_of=NOW,
            known_by=NOW,
        )
        == v1
    )
    assert (
        service.strict_known_contract(
            contract_id="test-contract-v1",
            contract_version="1.1.0",
            effective_as_of=later,
            known_by=later,
        )
        == v2
    )


def test_contract_bitemporal_chronology_validation(tmp_path: Path) -> None:
    # effective_at > recorded_at
    with pytest.raises(ValueError, match="effective_at must be <= recorded_at"):
        service, _ = setup(tmp_path)
        service.persist_contract(
            **contract_payload(
                effective_at=NOW + timedelta(days=1),
                recorded_at=NOW,
                known_at=NOW + timedelta(days=1),
            )
        )

    # recorded_at > known_at
    with pytest.raises(ValueError, match="recorded_at must be <= known_at"):
        service, _ = setup(tmp_path)
        service.persist_contract(
            **contract_payload(
                effective_at=NOW,
                recorded_at=NOW + timedelta(days=1),
                known_at=NOW,
            )
        )


def test_malformed_contract_row_fault_isolation(tmp_path: Path) -> None:
    from dataclasses import asdict

    service, repository = setup(tmp_path)
    m = service.persist_methodology(**payload(accepts_assembled_evidence=True))
    valid = service.persist_contract(**contract_payload(methodology_logical_id=m.logical_id))

    engine = create_sqlite_engine(repository.path)
    session = SessionFactory(engine).create()
    try:
        malformed_payload = asdict(valid)
        malformed_payload["contract_id"] = "malformed-contract"
        # Omit required non-defaulted field value_capture_pathway_id
        del malformed_payload["value_capture_pathway_id"]

        target_id = "malformed-contract:1.0.0"
        RepositoryFactory(session).snapshots().save(
            SnapshotRecord(
                id=target_id,
                created_at=NOW,
                effective_at=NOW,
                snapshot_type="methodology-evidence-input-contract",
                target_id="malformed-contract",
                record_ids=(target_id,),
                payload=malformed_payload,
                metadata={"known_at": NOW.isoformat()},
            )
        )
        session.commit()
    finally:
        session.close()
        engine.dispose()

    # Direct get_contract raises ValuationMethodologyIntegrityError for malformed row
    with pytest.raises(
        ValuationMethodologyIntegrityError, match="legacy methodology contract snapshot is missing required fields"
    ):
        repository.get_contract("malformed-contract", "1.0.0")

    # contracts() and contract_history() skip malformed row cleanly
    all_contracts = repository.contracts()
    assert all_contracts == (valid,)
    assert service.contract_history("malformed-contract") == ()


def test_contract_correction_non_advancing_clock_rejected(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    service.persist_contract(**contract_payload())
    with pytest.raises(ValuationMethodologyIntegrityError, match="recorded_at must follow predecessor"):
        service.persist_contract(
            **contract_payload(contract_version="1.1.0"),
            supersedes_contract_version="1.0.0",
            correction_reason="Non-advancing correction",
        )


def test_contract_second_root_and_branching_rejected(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    service.persist_contract(**contract_payload())

    # Second root contract for same contract_id is rejected
    with pytest.raises(ValuationMethodologyIntegrityError, match="root contract already exists"):
        service.persist_contract(**contract_payload(contract_version="2.0.0"))

    # Branching correction (two successors pointing to 1.0.0) is rejected
    service.persist_contract(
        **contract_payload(
            contract_version="1.1.0", recorded_at=NOW + timedelta(days=1), known_at=NOW + timedelta(days=1)
        ),
        supersedes_contract_version="1.0.0",
        correction_reason="First successor",
    )
    with pytest.raises(ValuationMethodologyIntegrityError, match="branching contract correction lineage is prohibited"):
        service.persist_contract(
            **contract_payload(
                contract_version="1.2.0", recorded_at=NOW + timedelta(days=2), known_at=NOW + timedelta(days=2)
            ),
            supersedes_contract_version="1.0.0",
            correction_reason="Competing successor",
        )


def test_no_reverse_dependency_from_methodology_to_evidence_assembly() -> None:
    import sys

    original_modules = dict(sys.modules)
    try:
        # Clear both methodology and evidence_assembly from sys.modules
        for mod in list(sys.modules.keys()):
            if mod.startswith("hunter.valuation_methodology") or mod.startswith("hunter.evidence_assembly"):
                sys.modules.pop(mod, None)

        import hunter.valuation_methodology

        assert hunter.valuation_methodology.__file__ is not None

        # Check that hunter.evidence_assembly is NOT imported by hunter.valuation_methodology
        assembly_loaded = any(mod.startswith("hunter.evidence_assembly") for mod in sys.modules)
        assert not assembly_loaded, "hunter.valuation_methodology must not import hunter.evidence_assembly"
    finally:
        sys.modules.clear()
        sys.modules.update(original_modules)
