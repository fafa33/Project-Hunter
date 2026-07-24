from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_sqlite_engine
from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.providers import (
    RegisteredValueCaptureProvider,
    ValueCaptureVerificationKeyRegistry,
)
from hunter.value_capture.registry import ValueCaptureSourceConfig, ValueCaptureSourceRegistry
from hunter.value_capture.repository import (
    DEFAULT_VALUE_CAPTURE_DB,
    SupplyAndValueCaptureRepository,
    ValueCaptureIntegrityError,
)
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
    verification_keys = ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY})
    repository = SupplyAndValueCaptureRepository(tmp_path / "value-capture.sqlite")
    service = SupplyAndValueCaptureService(
        registry=ValueCaptureSourceRegistry(configs),
        repository=repository,
        verification_keys=verification_keys,
    )
    provider = RegisteredValueCaptureProvider(configs[0], signing_key_id=SIGNING_KEY_ID, signing_key=SIGNING_KEY)
    return service, repository, provider


def evidence_result(provider, *, acquired_at=NOW + timedelta(minutes=1), acquisition_id="evidence-1", **extra):
    payload = {
        "evidence_type": "official_disclosure",
        "source_reference": "official-tokenomics-page",
        "extracted_claim": "Protocol fees are distributed under the documented rule.",
        "accounting_period_start": NOW - timedelta(days=30),
        "accounting_period_end": NOW,
        "attribution_rule_id": "api3-fee-distribution-rule-v1",
        "source_methodology": "official-accrual-disclosure-v1",
        "source_record_id": "official-tokenomics-page",
        "source_record_version": "2026-07-20",
        "entity_link_confidence": "1",
        "evidence_confidence": "0.95",
        "uncertainty": "0.05",
        "effective_at": NOW,
        "quality_state": "accepted",
        "conflict_state": "none",
    }
    payload.update(extra)
    return provider.acquisition(
        kind="evidence",
        capability="evidence:official_disclosure",
        endpoint=ENDPOINT,
        acquisition_id=acquisition_id,
        acquired_at=acquired_at,
        identity=identity(),
        payload=payload,
    )


def supply_result(provider, evidence_id, *, acquired_at=NOW + timedelta(minutes=2), acquisition_id="supply-1", **extra):
    payload = {
        "supply_basis_type": "circulating_supply",
        "quantity": "86000000",
        "unit": "native_units",
        "denominator_meaning": "Provider-observed circulating units for the canonical representation.",
        "supply_policy_id": "canonical-token-supply-v1",
        "supply_policy_version": "1.0.0",
        "quantity_components": [
            ["circulating_supply", "86000000"],
            ["total_supply", "100000000"],
            ["fully_diluted_supply", "115000000"],
            ["locked_supply", "10000000"],
            ["treasury_held_supply", "2000000"],
        ],
        "observed_market_fact_ids": ["market-fact-api3-circulating"],
        "observed_market_fact_versions": ["observed-market-fact-v2"],
        "source_record_id": "official-api3-supply-disclosure",
        "source_record_version": "2026-07-20",
        "confidence": "0.9",
        "uncertainty": "0.1",
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


def test_provider_signature_and_receipt_tampering_are_rejected(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    result = evidence_result(provider)
    tampered = replace(result, payload={**result.payload, "extracted_claim": "forged"})
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="invalid"):
        service.ingest_evidence(provider, tampered)


def test_receipt_hash_is_recomputed_before_signature_verification(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    result = evidence_result(provider)
    forged_hash = "0" * 64
    forged_signature = (
        __import__("hmac").new(SIGNING_KEY, forged_hash.encode(), __import__("hashlib").sha256).hexdigest()
    )
    forged = replace(
        result,
        receipt=replace(result.receipt, receipt_hash=forged_hash, signature=forged_signature),
    )
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="invalid"):
        service.ingest_evidence(provider, forged)


def test_cross_provider_signature_forgery_is_rejected(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    result = evidence_result(provider)
    other = RegisteredValueCaptureProvider(
        source(), signing_key_id="other-key", signing_key=b"other-value-capture-signing-key-0001"
    )
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="invalid"):
        service.ingest_evidence(other, result)


def test_atomic_receipt_and_record_persistence(tmp_path) -> None:
    service, repository, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    assert repository.count("value_capture_acquisition_receipts") == 1
    assert repository.count("fundamental_evidence_records") == 1
    assert repository.receipt(evidence.acquisition_id) is not None


def test_canonical_default_and_generic_sql_persistence(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    repository = SupplyAndValueCaptureRepository()
    assert repository.path == DEFAULT_VALUE_CAPTURE_DB
    assert repository.path.resolve() == tmp_path / "data/data_ops.sqlite"
    with sqlite3.connect(repository.path) as connection:
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "persistence_records" in tables
    assert "fundamental_evidence_records" not in tables
    assert "supply_basis_snapshots" not in tables
    assert "value_capture_rule_snapshots" not in tables


def test_forged_or_tampered_acquisition_is_rejected(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    result = evidence_result(provider)
    forged = replace(result, receipt=replace(result.receipt, source_id="forged-source"))
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="invalid"):
        service.ingest_evidence(provider, forged)


def test_repository_exposes_read_only_supported_api(tmp_path) -> None:
    repository = SupplyAndValueCaptureRepository(tmp_path / "value-capture.sqlite")
    assert not hasattr(repository, "_commit_authoritative")
    assert not hasattr(repository, "apply")
    assert not hasattr(repository, "write")
    assert not hasattr(repository, "commit")


def test_future_known_evidence_is_rejected(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(
        provider,
        evidence_result(provider, acquired_at=NOW + timedelta(days=2)),
    )
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="future-known"):
        service.ingest_supply(
            provider,
            supply_result(provider, evidence.record_id, acquired_at=NOW + timedelta(days=1)),
        )


def test_supply_basis_contract_rejects_incoherent_components(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    with pytest.raises(ValueError, match="circulating supply"):
        service.ingest_supply(
            provider,
            supply_result(
                provider,
                evidence.record_id,
                quantity_components=[
                    ["circulating_supply", "101000000"],
                    ["total_supply", "100000000"],
                ],
                quantity="101000000",
            ),
        )


def test_supply_basis_contract_round_trips_policy_and_fact_versions(tmp_path) -> None:
    service, repository, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    record = service.ingest_supply(provider, supply_result(provider, evidence.record_id))
    restored = repository.supply(record.record_id)
    assert restored == record
    assert record.supply_policy_version == "1.0.0"
    assert record.observed_market_fact_versions == ("observed-market-fact-v2",)
    assert dict(record.quantity_components)["fully_diluted_supply"] == "115000000"


def test_supply_basis_contract_rejects_null_policy_before_string_coercion(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="supply_policy_id"):
        service.ingest_supply(
            provider,
            supply_result(provider, evidence.record_id, supply_policy_id=None),
        )


def test_temporal_invariants_reject_invalid_chronology(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    with pytest.raises(ValueError, match="effective_at"):
        service.ingest_evidence(
            provider,
            evidence_result(provider, acquired_at=NOW, effective_at=NOW + timedelta(days=1)),
        )


def test_fundamental_evidence_contract_rejects_invalid_period_and_confidence(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    with pytest.raises(ValueError, match="accounting_period_start"):
        service.ingest_evidence(
            provider,
            evidence_result(
                provider,
                accounting_period_start=NOW,
                accounting_period_end=NOW - timedelta(days=1),
            ),
        )
    with pytest.raises(ValueError, match="evidence_confidence"):
        service.ingest_evidence(
            provider,
            evidence_result(
                provider,
                acquisition_id="invalid-confidence",
                evidence_confidence="1.01",
            ),
        )


def test_fundamental_evidence_contract_round_trips_authority_fields(tmp_path) -> None:
    service, repository, provider = setup(tmp_path)
    record = service.ingest_evidence(provider, evidence_result(provider))
    restored = repository.evidence(record.record_id)
    assert restored == record
    assert record.attribution_rule_id == "api3-fee-distribution-rule-v1"
    assert record.source_record_version == "2026-07-20"
    assert record.accounting_period_start == NOW - timedelta(days=30)


def test_legacy_fundamental_evidence_snapshot_fails_closed_with_compatibility_error(
    tmp_path,
) -> None:
    service, repository, provider = setup(tmp_path)
    record = service.ingest_evidence(provider, evidence_result(provider))
    engine = create_sqlite_engine(repository.path)
    session = SessionFactory(engine).create()
    try:
        snapshots = RepositoryFactory(session).snapshots()
        current = snapshots.load(record.record_id)
        assert current is not None
        payload = dict(current.payload)
        for name in (
            "accounting_period_start",
            "accounting_period_end",
            "attribution_rule_id",
            "source_methodology",
            "source_record_id",
            "source_record_version",
            "entity_link_confidence",
            "evidence_confidence",
            "uncertainty",
        ):
            payload.pop(name)
        payload["record_id"] = "legacy-evidence"
        snapshots.save(
            SnapshotRecord(
                id="legacy-evidence",
                created_at=current.created_at,
                effective_at=current.effective_at,
                snapshot_type=current.snapshot_type,
                target_id=current.target_id,
                record_ids=("legacy-evidence",),
                payload=payload,
                metadata=current.metadata,
            )
        )
        session.commit()
    finally:
        session.close()
        engine.dispose()

    with pytest.raises(ValueCaptureIntegrityError, match="legacy fundamental evidence"):
        repository.evidence("legacy-evidence")


def test_null_required_provenance_is_rejected_before_string_coercion(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="attribution_rule_id"):
        service.ingest_evidence(
            provider,
            evidence_result(provider, attribution_rule_id=None),
        )


def test_branching_corrections_are_rejected_and_replay_is_strict_known(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
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


def test_concurrent_corrections_cannot_branch_lineage(tmp_path) -> None:
    service, repository, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    original = service.ingest_rule(provider, rule_result(provider, evidence.record_id))
    second_service = SupplyAndValueCaptureService(
        registry=service.registry,
        repository=SupplyAndValueCaptureRepository(repository.path),
        verification_keys=ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY}),
    )
    corrections = (
        rule_result(
            provider,
            evidence.record_id,
            acquired_at=NOW + timedelta(days=2),
            acquisition_id="concurrent-rule-1",
            supersedes_record_id=original.record_id,
            correction_reason="First concurrent correction",
            source_economic_flow="Concurrent scope one",
        ),
        rule_result(
            provider,
            evidence.record_id,
            acquired_at=NOW + timedelta(days=3),
            acquisition_id="concurrent-rule-2",
            supersedes_record_id=original.record_id,
            correction_reason="Second concurrent correction",
            source_economic_flow="Concurrent scope two",
        ),
    )

    def ingest(item):
        active_service, result = item
        try:
            return active_service.ingest_rule(provider, result)
        except ValueCaptureIntegrityError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(ingest, ((service, corrections[0]), (second_service, corrections[1]))))

    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, ValueCaptureIntegrityError) for item in outcomes) == 1


def test_correction_authority_downgrade_is_rejected(tmp_path) -> None:
    official = source()
    aggregator = source(
        source_id="aggregator-source",
        authority_tier="aggregator",
        correction_predecessor_tiers=("official",),
    )
    service, _, official_provider = setup(tmp_path, (official, aggregator))
    evidence = service.ingest_evidence(official_provider, evidence_result(official_provider))
    original = service.ingest_rule(official_provider, rule_result(official_provider, evidence.record_id))
    aggregator_provider = RegisteredValueCaptureProvider(
        aggregator, signing_key_id=SIGNING_KEY_ID, signing_key=SIGNING_KEY
    )
    with pytest.raises(ValueError, match="downgrade"):
        service.ingest_rule(
            aggregator_provider,
            rule_result(
                aggregator_provider,
                evidence.record_id,
                acquired_at=NOW + timedelta(days=2),
                acquisition_id="rule-downgrade",
                supersedes_record_id=original.record_id,
                correction_reason="Invalid lower-authority correction",
            ),
        )
