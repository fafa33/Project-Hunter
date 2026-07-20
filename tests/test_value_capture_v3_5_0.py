from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.value_capture.models import (
    VALUE_CAPTURE_SCHEMA_VERSION,
    EconomicClaimIdentity,
    FundamentalEvidenceRecord,
    SupplyBasisSnapshot,
    ValueCaptureRuleSnapshot,
)
from hunter.value_capture.registry import ValueCaptureSourceConfig, ValueCaptureSourceRegistry
from hunter.value_capture.repository import (
    SupplyAndValueCaptureRepository,
    ValueCaptureAuthorizationError,
    ValueCaptureWritePlan,
)
from hunter.value_capture.service import (
    SupplyAndValueCaptureAuthorityError,
    SupplyAndValueCaptureService,
)

NOW = datetime(2026, 7, 20, 18, 0, tzinfo=UTC)
ENDPOINT = "https://example.org/tokenomics/api3"


def source(*, enabled: bool = True) -> ValueCaptureSourceConfig:
    return ValueCaptureSourceConfig(
        source_id="official-api3-tokenomics",
        authority_tier="official",
        source_type="official_disclosure",
        allowed_hosts=("example.org",),
        endpoint_patterns=("https://example.org/tokenomics/",),
        parser_version="official-tokenomics-v1",
        capabilities=(
            "evidence:official_disclosure",
            "supply:circulating_supply",
            "supply:total_supply",
            "rule:fee_distribution",
            "rule:no_direct_value_capture",
        ),
        enabled=enabled,
    )


def service(tmp_path, *, enabled: bool = True) -> tuple[SupplyAndValueCaptureService, ValueCaptureSourceConfig]:
    config = source(enabled=enabled)
    repository = SupplyAndValueCaptureRepository(tmp_path / "value-capture.sqlite")
    return (
        SupplyAndValueCaptureService(registry=ValueCaptureSourceRegistry((config,)), repository=repository),
        config,
    )


def identity(*, representation_id: str = "api3-ethereum") -> EconomicClaimIdentity:
    return EconomicClaimIdentity(
        entity_id="api3-project",
        economic_claim_id="api3-token-claim",
        asset_id="api3-token",
        representation_id=representation_id,
        token_id="api3",
        chain="ethereum",
        contract_address="0x0b38210ea11411557c13457d4da7dc6ea731b88a",
    )


def evidence_record(*, claim_identity: EconomicClaimIdentity | None = None) -> FundamentalEvidenceRecord:
    return FundamentalEvidenceRecord(
        record_id="pending",
        logical_id="pending",
        schema_version=VALUE_CAPTURE_SCHEMA_VERSION,
        semantic_version="1.0.0",
        identity=claim_identity or identity(),
        evidence_type="official_disclosure",
        source_id="official-api3-tokenomics",
        source_authority_tier="official",
        source_reference="official-tokenomics-page",
        parser_version="official-tokenomics-v1",
        extracted_claim="Protocol fees are distributed to the represented claim under the documented rule.",
        amount=None,
        unit=None,
        effective_at=NOW,
        recorded_at=NOW + timedelta(minutes=1),
        known_at=NOW + timedelta(minutes=1),
        raw_content_hash="a" * 64,
        quality_state="accepted",
        conflict_state="none",
    )


def supply_record(evidence_id: str, *, quality_state: str = "accepted") -> SupplyBasisSnapshot:
    return SupplyBasisSnapshot(
        record_id="pending",
        logical_id="pending",
        schema_version=VALUE_CAPTURE_SCHEMA_VERSION,
        semantic_version="1.0.0",
        identity=identity(),
        supply_basis_type="circulating_supply",
        quantity="86000000",
        unit="native_units",
        denominator_meaning="Provider-observed circulating API3 units for this canonical representation.",
        effective_at=NOW,
        recorded_at=NOW + timedelta(minutes=2),
        known_at=NOW + timedelta(minutes=2),
        source_id="official-api3-tokenomics",
        parser_version="official-tokenomics-v1",
        evidence_record_ids=(evidence_id,),
        raw_payload_hash="b" * 64,
        quality_state=quality_state,  # type: ignore[arg-type]
        conflict_state="none",
    )


def rule_record(evidence_id: str) -> ValueCaptureRuleSnapshot:
    return ValueCaptureRuleSnapshot(
        record_id="pending",
        logical_id="pending",
        schema_version=VALUE_CAPTURE_SCHEMA_VERSION,
        semantic_version="1.0.0",
        identity=identity(),
        rule_type="fee_distribution",
        entitlement_scope="Documented protocol-fee distribution entitlement",
        beneficiary_scope="Holders satisfying the enacted rule",
        source_economic_flow="Protocol fees",
        destination_economic_flow="Eligible represented claim",
        trigger_condition="Rule is active and its documented conditions are met",
        distribution_formula="Documented formula only",
        rate_or_proportion=None,
        governance_or_contract_authority="Official enacted tokenomics rule",
        effective_at=NOW,
        recorded_at=NOW + timedelta(minutes=2),
        known_at=NOW + timedelta(minutes=2),
        source_id="official-api3-tokenomics",
        parser_version="official-tokenomics-v1",
        evidence_record_ids=(evidence_id,),
        raw_payload_hash="c" * 64,
        quality_state="accepted",
        conflict_state="none",
    )


def persist_evidence(service_: SupplyAndValueCaptureService, config: ValueCaptureSourceConfig):
    return service_.persist_evidence(evidence_record(), endpoint=ENDPOINT, registry_fingerprint=config.fingerprint)


def test_registry_rejects_unregistered_disabled_and_forged_sources(tmp_path) -> None:
    service_, config = service(tmp_path)
    record = evidence_record()
    with pytest.raises(ValueError, match="unregistered"):
        service_.persist_evidence(
            replace(record, source_id="forged"),
            endpoint=ENDPOINT,
            registry_fingerprint=config.fingerprint,
        )
    disabled_service, disabled = service(tmp_path / "disabled", enabled=False)
    with pytest.raises(ValueError, match="disabled"):
        disabled_service.persist_evidence(record, endpoint=ENDPOINT, registry_fingerprint=disabled.fingerprint)
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="fingerprint"):
        service_.persist_evidence(record, endpoint=ENDPOINT, registry_fingerprint="forged")


def test_repository_rejects_direct_authoritative_mutation(tmp_path) -> None:
    repository = SupplyAndValueCaptureRepository(tmp_path / "value-capture.sqlite")
    with pytest.raises(ValueCaptureAuthorizationError):
        repository.apply(ValueCaptureWritePlan(evidence=(evidence_record(),), authority=object()))


def test_evidence_supply_and_rule_persist_idempotently(tmp_path) -> None:
    service_, config = service(tmp_path)
    evidence = persist_evidence(service_, config)
    supply = service_.persist_supply(
        supply_record(evidence.record_id),
        endpoint=ENDPOINT,
        registry_fingerprint=config.fingerprint,
    )
    rule = service_.persist_rule(
        rule_record(evidence.record_id),
        endpoint=ENDPOINT,
        registry_fingerprint=config.fingerprint,
    )
    service_.persist_supply(
        supply_record(evidence.record_id),
        endpoint=ENDPOINT,
        registry_fingerprint=config.fingerprint,
    )
    assert service_.repository.count("fundamental_evidence_records") == 1
    assert service_.repository.count("supply_basis_snapshots") == 1
    assert service_.repository.count("value_capture_rule_snapshots") == 1
    assert supply.record_id != "pending"
    assert rule.content_hash


def test_supply_requires_matching_authoritative_evidence(tmp_path) -> None:
    service_, config = service(tmp_path)
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="does not exist"):
        service_.persist_supply(
            supply_record("missing"),
            endpoint=ENDPOINT,
            registry_fingerprint=config.fingerprint,
        )
    other = persist_evidence(service_, config)
    mismatched = replace(supply_record(other.record_id), identity=identity(representation_id="api3-base"))
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="identity"):
        service_.persist_supply(
            mismatched,
            endpoint=ENDPOINT,
            registry_fingerprint=config.fingerprint,
        )


def test_strict_known_replay_enforces_all_cutoffs_and_quality(tmp_path) -> None:
    service_, config = service(tmp_path)
    evidence = persist_evidence(service_, config)
    accepted = service_.persist_supply(
        supply_record(evidence.record_id),
        endpoint=ENDPOINT,
        registry_fingerprint=config.fingerprint,
    )
    before_known = service_.strict_known_supply(
        entity_id=accepted.identity.entity_id,
        economic_claim_id=accepted.identity.economic_claim_id,
        representation_id=accepted.identity.representation_id,
        supply_basis_type=accepted.supply_basis_type,
        effective_as_of=NOW + timedelta(days=1),
        known_by=NOW,
    )
    assert before_known is None
    selected = service_.strict_known_supply(
        entity_id=accepted.identity.entity_id,
        economic_claim_id=accepted.identity.economic_claim_id,
        representation_id=accepted.identity.representation_id,
        supply_basis_type=accepted.supply_basis_type,
        effective_as_of=NOW + timedelta(days=1),
        known_by=NOW + timedelta(days=1),
    )
    assert selected == accepted
    stale = replace(
        supply_record(evidence.record_id, quality_state="stale"),
        effective_at=NOW + timedelta(hours=1),
        recorded_at=NOW + timedelta(hours=1),
        known_at=NOW + timedelta(hours=1),
    )
    service_.persist_supply(stale, endpoint=ENDPOINT, registry_fingerprint=config.fingerprint)
    selected_again = service_.strict_known_supply(
        entity_id=accepted.identity.entity_id,
        economic_claim_id=accepted.identity.economic_claim_id,
        representation_id=accepted.identity.representation_id,
        supply_basis_type=accepted.supply_basis_type,
        effective_as_of=NOW + timedelta(days=1),
        known_by=NOW + timedelta(days=1),
    )
    assert selected_again == accepted


def test_corrections_append_successors_and_preserve_historical_selection(tmp_path) -> None:
    service_, config = service(tmp_path)
    evidence = persist_evidence(service_, config)
    original = service_.persist_rule(
        rule_record(evidence.record_id),
        endpoint=ENDPOINT,
        registry_fingerprint=config.fingerprint,
    )
    corrected_input = replace(
        rule_record(evidence.record_id),
        source_economic_flow="Corrected protocol fee scope",
        recorded_at=NOW + timedelta(days=2),
        known_at=NOW + timedelta(days=2),
        supersedes_record_id=original.record_id,
        correction_reason="Official disclosure correction",
    )
    corrected = service_.persist_rule(
        corrected_input,
        endpoint=ENDPOINT,
        registry_fingerprint=config.fingerprint,
    )
    historical = service_.strict_known_rule(
        entity_id=original.identity.entity_id,
        economic_claim_id=original.identity.economic_claim_id,
        representation_id=original.identity.representation_id,
        rule_type=original.rule_type,
        effective_as_of=NOW + timedelta(days=3),
        known_by=NOW + timedelta(days=1),
    )
    current = service_.strict_known_rule(
        entity_id=original.identity.entity_id,
        economic_claim_id=original.identity.economic_claim_id,
        representation_id=original.identity.representation_id,
        rule_type=original.rule_type,
        effective_as_of=NOW + timedelta(days=3),
        known_by=NOW + timedelta(days=3),
    )
    assert historical == original
    assert current == corrected
    assert corrected.logical_id == original.logical_id
    assert corrected.record_id != original.record_id


def test_revenue_or_tvl_labels_do_not_create_unregistered_value_capture(tmp_path) -> None:
    service_, config = service(tmp_path)
    evidence = persist_evidence(service_, config)
    unsupported = replace(rule_record(evidence.record_id), rule_type="revenue_distribution")
    with pytest.raises(ValueError, match="capability"):
        service_.persist_rule(
            unsupported,
            endpoint=ENDPOINT,
            registry_fingerprint=config.fingerprint,
        )
