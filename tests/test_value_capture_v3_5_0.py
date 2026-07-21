from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.providers import RegisteredValueCaptureProvider
from hunter.value_capture.registry import ValueCaptureSourceConfig, ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository, ValueCaptureIntegrityError
from hunter.value_capture.service import SupplyAndValueCaptureAuthorityError, SupplyAndValueCaptureService

NOW = datetime(2026, 7, 20, 18, 0, tzinfo=UTC)
SIGNING_KEY = b"v3.5.0-value-capture-test-key-0001"
SIGNING_KEY_ID = "value-capture-test-key-v1"
ENDPOINT = "https://example.org/tokenomics/api3"


def source(
    *,
    source_id: str = "official-api3-tokenomics",
    parser_version: str = "official-tokenomics-v1",
    authority_tier: str = "official",
    correction_predecessor_tiers: tuple[str, ...] = (),
) -> ValueCaptureSourceConfig:
    return ValueCaptureSourceConfig(
        source_id=source_id,
        authority_tier=authority_tier,
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
        correction_predecessor_tiers=correction_predecessor_tiers,
    )


def identity() -> EconomicClaimIdentity:
    return EconomicClaimIdentity(
        entity_id="api3-project",
        economic_claim_id="api3-token-claim",
        asset_id="api3-token",
        representation_id="api3-ethereum",
        token_id="api3",
        chain="ethereum",
        contract_address="0x0b38210ea11411557c13457d4da7dc6ea731b88a",
    )


def setup(tmp_path, configs: tuple[ValueCaptureSourceConfig, ...] | None = None):
    configs = configs or (source(),)
    repository = SupplyAndValueCaptureRepository(
        tmp_path / "value-capture.sqlite",
        verification_keys=ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY}),
    )
    service = SupplyAndValueCaptureService(
        registry=ValueCaptureSourceRegistry(configs),
        repository=repository,
    )
    providers = tuple(RegisteredValueCaptureProvider(config) for config in configs)
    return service, service.repository, providers


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


def test_forged_or_tampered_acquisition_is_rejected(tmp_path) -> None:
    service, _, (provider,) = setup(tmp_path)
    valid = evidence_result(provider)
    tampered = replace(valid, payload={**valid.payload, "extracted_claim": "forged"})
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="signature"):
        service.ingest_evidence(provider, tampered)

    other_provider = RegisteredValueCaptureProvider(source())
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="signature"):
        service.ingest_evidence(other_provider, valid)


def test_repository_exposes_read_only_supported_api(tmp_path) -> None:
    repository = SupplyAndValueCaptureRepository(
        tmp_path / "value-capture.sqlite",
        verification_keys=ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY}),
    )
    assert not hasattr(repository, "apply")
    assert not hasattr(repository, "write")
    assert not hasattr(repository, "commit")
    assert not hasattr(repository, "_connect")


def test_receipt_and_records_persist_atomically_and_idempotently(tmp_path) -> None:
    service, repository, (provider,) = setup(tmp_path)
    evidence_result_ = evidence_result(provider)
    evidence = service.ingest_evidence(provider, evidence_result_)
    supply_result_ = supply_result(provider, evidence.record_id)
    rule_result_ = rule_result(provider, evidence.record_id)
    supply = service.ingest_supply(provider, supply_result_)
    rule = service.ingest_rule(provider, rule_result_)
    service.ingest_supply(provider, supply_result_)

    assert repository.count("value_capture_acquisition_receipts") == 3
    assert repository.count("fundamental_evidence_records") == 1
    assert repository.count("supply_basis_snapshots") == 1
    assert repository.count("value_capture_rule_snapshots") == 1
    receipt = repository.receipt(supply.acquisition_id)
    assert receipt is not None
    assert receipt.raw_payload_hash == supply.raw_payload_hash
    assert receipt.source_id == supply.source_id
    assert receipt.identity == supply.identity
    assert rule.acquisition_id == "rule-1"


def test_future_known_evidence_is_rejected(tmp_path) -> None:
    service, _, (provider,) = setup(tmp_path)
    evidence = service.ingest_evidence(
        provider,
        evidence_result(provider, acquired_at=NOW + timedelta(days=2)),
    )
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="future-known"):
        service.ingest_supply(
            provider,
            supply_result(provider, evidence.record_id, acquired_at=NOW + timedelta(days=1)),
        )


def test_temporal_invariants_reject_invalid_chronology(tmp_path) -> None:
    service, _, (provider,) = setup(tmp_path)
    invalid = evidence_result(provider, acquired_at=NOW)
    invalid = provider.acquisition(
        kind="evidence",
        capability="evidence:official_disclosure",
        endpoint=ENDPOINT,
        acquisition_id="invalid-time",
        acquired_at=NOW,
        identity=identity(),
        payload={**invalid.payload, "effective_at": NOW + timedelta(days=1)},
    )
    with pytest.raises(ValueError, match="effective_at"):
        service.ingest_evidence(provider, invalid)


def test_branching_corrections_are_rejected_and_replay_is_strict_known(tmp_path) -> None:
    service, _, (provider,) = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    original = service.ingest_rule(provider, rule_result(provider, evidence.record_id))
    corrected = service.ingest_rule(
        provider,
        rule_result(
            provider,
            evidence.record_id,
            acquired_at=NOW + timedelta(days=2),
            acquisition_id="rule-2",
            supersedes_record_id=original.record_id,
            correction_reason="Official correction",
            source_economic_flow="Corrected protocol fee scope",
        ),
    )
    with pytest.raises(ValueCaptureIntegrityError, match="branching"):
        service.ingest_rule(
            provider,
            rule_result(
                provider,
                evidence.record_id,
                acquired_at=NOW + timedelta(days=3),
                acquisition_id="rule-3",
                supersedes_record_id=original.record_id,
                correction_reason="Competing correction",
                source_economic_flow="Another scope",
            ),
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


def test_cross_source_correction_requires_explicit_non_downgrade_policy(tmp_path) -> None:
    official = source()
    aggregator = source(
        source_id="aggregator-api3-tokenomics",
        parser_version="aggregator-v1",
        authority_tier="aggregator",
        correction_predecessor_tiers=("official",),
    )
    service, _, (official_provider, aggregator_provider) = setup(tmp_path, (official, aggregator))
    evidence = service.ingest_evidence(official_provider, evidence_result(official_provider))
    original = service.ingest_rule(official_provider, rule_result(official_provider, evidence.record_id))
    downgraded = rule_result(
        aggregator_provider,
        evidence.record_id,
        acquired_at=NOW + timedelta(days=2),
        acquisition_id="rule-downgrade",
        supersedes_record_id=original.record_id,
        correction_reason="Aggregator correction",
    )
    with pytest.raises(ValueError, match="downgrade"):
        service.ingest_rule(aggregator_provider, downgraded)
