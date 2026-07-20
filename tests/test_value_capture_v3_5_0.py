from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.providers import RegisteredValueCaptureProvider, ValueCaptureAcquisitionResult
from hunter.value_capture.registry import ValueCaptureSourceConfig, ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository, ValueCaptureIntegrityError
from hunter.value_capture.service import SupplyAndValueCaptureAuthorityError, SupplyAndValueCaptureService

NOW = datetime(2026, 7, 20, 18, 0, tzinfo=UTC)
ENDPOINT = "https://example.org/tokenomics/api3"


def source(*, parser_version: str = "official-tokenomics-v1") -> ValueCaptureSourceConfig:
    return ValueCaptureSourceConfig(
        source_id="official-api3-tokenomics",
        authority_tier="official",
        source_type="official_disclosure",
        allowed_hosts=("example.org",),
        endpoint_patterns=("https://example.org/tokenomics/",),
        parser_version=parser_version,
        capabilities=(
            "evidence:official_disclosure",
            "supply:circulating_supply",
            "rule:fee_distribution",
        ),
        enabled=True,
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


def setup(tmp_path):
    config = source()
    repository = SupplyAndValueCaptureRepository(tmp_path / "value-capture.sqlite")
    service = SupplyAndValueCaptureService(
        registry=ValueCaptureSourceRegistry((config,)),
        repository=repository,
    )
    provider = RegisteredValueCaptureProvider(config)
    return service, repository, provider


def evidence_result(provider, *, acquired_at=NOW + timedelta(minutes=1), acquisition_id="evidence-1"):
    return provider.acquisition(
        kind="evidence",
        capability="evidence:official_disclosure",
        endpoint=ENDPOINT,
        acquisition_id=acquisition_id,
        acquired_at=acquired_at,
        identity=identity(),
        payload={
            "evidence_type": "official_disclosure",
            "source_reference": "official-tokenomics-page",
            "extracted_claim": "Protocol fees are distributed under the documented rule.",
            "effective_at": NOW,
            "quality_state": "accepted",
            "conflict_state": "none",
        },
    )


def supply_result(provider, evidence_id, *, acquired_at=NOW + timedelta(minutes=2), acquisition_id="supply-1", **extra):
    payload = {
        "supply_basis_type": "circulating_supply",
        "quantity": "86000000",
        "unit": "native_units",
        "denominator_meaning": "Provider-observed circulating units for the canonical representation.",
        "effective_at": NOW,
        "evidence_record_ids": [evidence_id],
        "quality_state": "accepted",
        "conflict_state": "none",
    }
    payload.update(extra)
    return provider.acquisition(
        kind="supply",
        capability="supply:circulating_supply",
        endpoint=ENDPOINT,
        acquisition_id=acquisition_id,
        acquired_at=acquired_at,
        identity=identity(),
        payload=payload,
    )


def rule_result(provider, evidence_id, *, acquired_at=NOW + timedelta(minutes=2), acquisition_id="rule-1", **extra):
    payload = {
        "rule_type": "fee_distribution",
        "entitlement_scope": "Documented protocol-fee distribution entitlement",
        "beneficiary_scope": "Eligible holders",
        "source_economic_flow": "Protocol fees",
        "destination_economic_flow": "Eligible represented claim",
        "trigger_condition": "Documented rule conditions are met",
        "distribution_formula": "Documented formula only",
        "governance_or_contract_authority": "Official enacted tokenomics rule",
        "effective_at": NOW,
        "evidence_record_ids": [evidence_id],
        "quality_state": "accepted",
        "conflict_state": "none",
    }
    payload.update(extra)
    return provider.acquisition(
        kind="rule",
        capability="rule:fee_distribution",
        endpoint=ENDPOINT,
        acquisition_id=acquisition_id,
        acquired_at=acquired_at,
        identity=identity(),
        payload=payload,
    )


def test_caller_cannot_construct_provider_result() -> None:
    with pytest.raises(ValueError, match="caller-constructed"):
        ValueCaptureAcquisitionResult(
            kind="evidence",
            capability="evidence:official_disclosure",
            source=source(),
            endpoint=ENDPOINT,
            acquisition_id="forged",
            acquired_at=NOW,
            identity=identity(),
            payload={},
            seal=object(),
        )


def test_repository_has_no_public_write_api(tmp_path) -> None:
    repository = SupplyAndValueCaptureRepository(tmp_path / "value-capture.sqlite")
    assert not hasattr(repository, "apply")
    assert not hasattr(repository, "write")
    assert not hasattr(repository, "_authority")


def test_provider_ingestion_is_idempotent_and_provenance_bound(tmp_path) -> None:
    service, repository, provider = setup(tmp_path)
    evidence = service.ingest_evidence(evidence_result(provider))
    supply = service.ingest_supply(supply_result(provider, evidence.record_id))
    rule = service.ingest_rule(rule_result(provider, evidence.record_id))
    service.ingest_supply(supply_result(provider, evidence.record_id))
    assert repository.count("fundamental_evidence_records") == 1
    assert repository.count("supply_basis_snapshots") == 1
    assert repository.count("value_capture_rule_snapshots") == 1
    assert supply.source_id == evidence.source_id
    assert rule.parser_version == evidence.parser_version
    assert supply.acquisition_id == "supply-1"


def test_future_known_evidence_is_rejected(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(evidence_result(provider, acquired_at=NOW + timedelta(days=2)))
    result = supply_result(provider, evidence.record_id, acquired_at=NOW + timedelta(days=1))
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="future-known"):
        service.ingest_supply(result)


def test_source_parser_mismatch_is_rejected(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(evidence_result(provider))
    other_config = source(parser_version="official-tokenomics-v2")
    other_provider = RegisteredValueCaptureProvider(other_config)
    mismatched = other_provider.acquisition(
        kind="supply",
        capability="supply:circulating_supply",
        endpoint=ENDPOINT,
        acquisition_id="supply-mismatch",
        acquired_at=NOW + timedelta(minutes=2),
        identity=identity(),
        payload={
            "supply_basis_type": "circulating_supply",
            "quantity": "1",
            "unit": "native_units",
            "denominator_meaning": "Mismatch",
            "effective_at": NOW,
            "evidence_record_ids": [evidence.record_id],
        },
    )
    with pytest.raises(ValueError, match="parser version"):
        service.ingest_supply(mismatched)


def test_temporal_invariants_reject_invalid_chronology(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    invalid = evidence_result(provider, acquired_at=NOW)
    invalid_payload = dict(invalid.payload)
    invalid_payload["effective_at"] = NOW + timedelta(days=1)
    invalid = provider.acquisition(
        kind="evidence",
        capability="evidence:official_disclosure",
        endpoint=ENDPOINT,
        acquisition_id="invalid-time",
        acquired_at=NOW,
        identity=identity(),
        payload=invalid_payload,
    )
    with pytest.raises(ValueError, match="effective_at"):
        service.ingest_evidence(invalid)


def test_branching_corrections_are_rejected_and_replay_is_strict_known(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(evidence_result(provider))
    original = service.ingest_rule(rule_result(provider, evidence.record_id))
    corrected = service.ingest_rule(
        rule_result(
            provider,
            evidence.record_id,
            acquired_at=NOW + timedelta(days=2),
            acquisition_id="rule-2",
            supersedes_record_id=original.record_id,
            correction_reason="Official correction",
            source_economic_flow="Corrected protocol fee scope",
        )
    )
    with pytest.raises(ValueCaptureIntegrityError, match="branching"):
        service.ingest_rule(
            rule_result(
                provider,
                evidence.record_id,
                acquired_at=NOW + timedelta(days=3),
                acquisition_id="rule-3",
                supersedes_record_id=original.record_id,
                correction_reason="Competing correction",
                source_economic_flow="Another scope",
            )
        )
    historical = service.strict_known_rule(
        entity_id=original.identity.entity_id,
        economic_claim_id=original.identity.economic_claim_id,
        representation_id=original.identity.representation_id,
        rule_type=original.rule_type,
        effective_as_of=NOW + timedelta(days=3),
        known_by=NOW + timedelta(days=1),
    )
    current = service.strict_known_rule(
        entity_id=original.identity.entity_id,
        economic_claim_id=original.identity.economic_claim_id,
        representation_id=original.identity.representation_id,
        rule_type=original.rule_type,
        effective_as_of=NOW + timedelta(days=3),
        known_by=NOW + timedelta(days=3),
    )
    assert historical == original
    assert current == corrected
