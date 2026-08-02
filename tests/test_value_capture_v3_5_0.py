from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.market_facts.models import (
    MarketFactAcquisitionResult,
    MarketFactIdentity,
    MarketFactRequest,
    NormalizedMarketFact,
    ObservedMarketFactRecord,
)
from hunter.market_facts.registry import MarketFactSourceRegistry
from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.market_facts.service import ObservedMarketFactService
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
    record_snapshot,
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


def market_fact_registry() -> MarketFactSourceRegistry:
    return MarketFactSourceRegistry.from_mapping(
        {
            "sources": [
                {
                    "source_id": "official-api3-market-facts",
                    "provider_id": "official-api3-market-facts-provider",
                    "endpoint_template": "https://example.org/market-facts/{listing_id}",
                    "allowed_hosts": ["example.org"],
                    "parser_version": "official-market-facts-v1",
                    "enabled": True,
                    "capabilities": ["circulating_supply"],
                    "quote_currencies": ["usd"],
                    "units": {"circulating_supply": "native_units"},
                    "supported_entity_scope": ["canonical_asset_representation"],
                    "identity_bindings": [
                        {
                            "entity_id": "api3-project",
                            "asset_id": "api3-token",
                            "representation_id": "api3-ethereum",
                            "chain": "ethereum",
                            "contract_address": "0x0b38210ea11411557c13457d4da7dc6ea731b88a",
                            "provider_listing_id": "api3-listing",
                        },
                        {
                            "entity_id": "other-project",
                            "asset_id": "other-token",
                            "representation_id": "other-ethereum",
                            "chain": "",
                            "contract_address": "",
                            "provider_listing_id": "other-listing",
                        },
                    ],
                    "freshness_seconds": 3600,
                    "observation_confidence": "0.9",
                    "historical_support": "current-only",
                    "limitations": "test fixture observed market fact only",
                }
            ]
        }
    )


def seed_observed_market_fact(
    tmp_path,
    *,
    quantity: str = "86000000",
    effective_at: datetime = NOW,
    known_at: datetime | None = None,
) -> ObservedMarketFactRecord:
    known_at = known_at or effective_at
    market_fact_repository = ObservedMarketFactRepository(tmp_path / "value-capture.sqlite")
    market_fact_service = ObservedMarketFactService(market_fact_repository, market_fact_registry())
    request = MarketFactRequest(
        source_id="official-api3-market-facts",
        provider_id="official-api3-market-facts-provider",
        identity=MarketFactIdentity(
            entity_id="api3-project",
            asset_id="api3-token",
            representation_id="api3-ethereum",
            chain="ethereum",
            contract_address="0x0b38210ea11411557c13457d4da7dc6ea731b88a",
            provider_listing_id="api3-listing",
        ),
        quote_currency="usd",
        requested_fact_types=("circulating_supply",),
        requested_at=effective_at,
    )
    fact = NormalizedMarketFact(
        fact_type="circulating_supply",
        value=quantity,
        unit="native_units",
        quote_currency=None,
        effective_at=effective_at,
        observed_at=effective_at,
        confidence="0.9",
    )
    result = MarketFactAcquisitionResult(
        source_id="official-api3-market-facts",
        provider_id="official-api3-market-facts-provider",
        endpoint="https://example.org/market-facts/api3-listing",
        parser_version="official-market-facts-v1",
        registry_fingerprint=market_fact_registry().require("official-api3-market-facts").fingerprint,
        provider_source_record_id="api3-listing",
        provider_source_record_version="2026-07-20",
        request=request,
        status="success",
        acquired_at=known_at,
        known_at=known_at,
        raw_payload_hash="sha256:" + "a" * 64,
        facts=(fact,),
    )
    records = market_fact_service.ingest(request, result, recorded_at=known_at)
    return records[0]


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
        "mechanism_policy_id": "canonical-fee-distribution-v1",
        "mechanism_policy_version": "1.0.0",
        "dilution_treatment": "Distribution entitlement is measured after documented dilution.",
        "claim_seniority": "Pro rata eligible-holder claim after protocol liabilities.",
        "applicability_start": NOW,
        "applicability_end": NOW + timedelta(days=365),
        "limitations": [
            "No entitlement outside the enacted applicability period.",
            "No inference from undistributed protocol revenue.",
        ],
        "evidence_record_versions": ["1.0.0"],
        "source_record_id": "official-api3-fee-rule",
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


def test_supply_basis_accepts_total_above_fully_diluted_within_precision_tolerance(tmp_path) -> None:
    """Real provider data (observed for Sky on CoinGecko) sometimes reports
    total_supply fractionally above its own fully_diluted_supply for the same
    snapshot — provider rounding/timing, not a real economic contradiction.
    Both values are persisted exactly as observed, and the record is accepted
    with no conflict flag."""
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    fact = seed_observed_market_fact(tmp_path)
    record = service.ingest_supply(
        provider,
        supply_result(
            provider,
            evidence.record_id,
            observed_market_fact_ids=[fact.record_id],
            observed_market_fact_versions=[fact.semantic_version],
            quantity_components=[
                ["circulating_supply", "86000000"],
                ["total_supply", "100000000.001"],
                ["fully_diluted_supply", "100000000"],
            ],
        ),
    )
    components = dict(record.quantity_components)
    assert components["total_supply"] == "100000000.001"
    assert components["fully_diluted_supply"] == "100000000"
    assert record.quality_state == "accepted"
    assert record.conflict_state == "none"


def test_supply_basis_flags_conflict_when_total_exceeds_fully_diluted_beyond_tolerance(tmp_path) -> None:
    """A total_supply that exceeds fully_diluted_supply by more than the
    provider precision/timing tolerance is a real data conflict. It must not
    be rejected or silently discarded — it is persisted with both raw values
    intact, and conflict_state is surfaced as "open" instead of the
    caller-supplied "none"."""
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    fact = seed_observed_market_fact(tmp_path)
    record = service.ingest_supply(
        provider,
        supply_result(
            provider,
            evidence.record_id,
            observed_market_fact_ids=[fact.record_id],
            observed_market_fact_versions=[fact.semantic_version],
            quantity_components=[
                ["circulating_supply", "86000000"],
                ["total_supply", "100100000"],
                ["fully_diluted_supply", "100000000"],
            ],
        ),
    )
    components = dict(record.quantity_components)
    assert components["total_supply"] == "100100000"
    assert components["fully_diluted_supply"] == "100000000"
    assert record.conflict_state == "open"


def test_supply_basis_initial_record_cannot_claim_resolved_for_unauthorized_conflict(tmp_path) -> None:
    """An initial record (no supersedes_record_id, i.e. no correction lineage) has no prior,
    authorized correction that could have legitimately resolved anything. A caller pre-labeling
    such a record conflict_state="resolved" must not bypass conflict surfacing for a material
    total>fully_diluted incoherence — this is forced to "open" regardless of the claimed state."""
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    fact = seed_observed_market_fact(tmp_path)
    record = service.ingest_supply(
        provider,
        supply_result(
            provider,
            evidence.record_id,
            observed_market_fact_ids=[fact.record_id],
            observed_market_fact_versions=[fact.semantic_version],
            quantity_components=[
                ["circulating_supply", "86000000"],
                ["total_supply", "100100000"],
                ["fully_diluted_supply", "100000000"],
            ],
            conflict_state="resolved",
        ),
    )
    assert record.supersedes_record_id is None
    assert record.conflict_state == "open"


def test_supply_basis_contract_round_trips_policy_and_fact_versions(tmp_path) -> None:
    service, repository, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    fact = seed_observed_market_fact(tmp_path)
    record = service.ingest_supply(
        provider,
        supply_result(
            provider,
            evidence.record_id,
            observed_market_fact_ids=[fact.record_id],
            observed_market_fact_versions=[fact.semantic_version],
        ),
    )
    restored = repository.supply(record.record_id)
    assert restored == record
    assert record.supply_policy_version == "1.0.0"
    assert record.observed_market_fact_ids == (fact.record_id,)
    assert record.observed_market_fact_versions == ("observed-market-fact-v2",)
    assert dict(record.quantity_components)["fully_diluted_supply"] == "115000000"


def test_supply_basis_rejects_nonexistent_observed_market_fact(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="does not exist"):
        service.ingest_supply(
            provider,
            supply_result(
                provider,
                evidence.record_id,
                observed_market_fact_ids=["nonexistent-market-fact"],
                observed_market_fact_versions=["observed-market-fact-v2"],
            ),
        )


def test_supply_basis_rejects_observed_market_fact_version_mismatch(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    fact = seed_observed_market_fact(tmp_path)
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="version does not match"):
        service.ingest_supply(
            provider,
            supply_result(
                provider,
                evidence.record_id,
                observed_market_fact_ids=[fact.record_id],
                observed_market_fact_versions=["wrong-version"],
            ),
        )


def test_supply_basis_rejects_observed_market_fact_identity_mismatch(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    mismatched_repository = ObservedMarketFactRepository(tmp_path / "value-capture.sqlite")
    mismatched_service = ObservedMarketFactService(mismatched_repository, market_fact_registry())
    other_identity = MarketFactIdentity(
        entity_id="other-project",
        asset_id="other-token",
        representation_id="other-ethereum",
        chain="",
        contract_address="",
        provider_listing_id="other-listing",
    )
    request = MarketFactRequest(
        source_id="official-api3-market-facts",
        provider_id="official-api3-market-facts-provider",
        identity=other_identity,
        quote_currency="usd",
        requested_fact_types=("circulating_supply",),
        requested_at=NOW,
    )
    result = MarketFactAcquisitionResult(
        source_id="official-api3-market-facts",
        provider_id="official-api3-market-facts-provider",
        endpoint="https://example.org/market-facts/other-listing",
        parser_version="official-market-facts-v1",
        registry_fingerprint=market_fact_registry().require("official-api3-market-facts").fingerprint,
        provider_source_record_id="other-listing",
        provider_source_record_version="2026-07-20",
        request=request,
        status="success",
        acquired_at=NOW,
        known_at=NOW,
        raw_payload_hash="sha256:" + "b" * 64,
        facts=(
            NormalizedMarketFact(
                fact_type="circulating_supply",
                value="86000000",
                unit="native_units",
                quote_currency=None,
                effective_at=NOW,
                observed_at=NOW,
                confidence="0.9",
            ),
        ),
    )
    other_fact = mismatched_service.ingest(request, result, recorded_at=NOW)[0]
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="identity does not match"):
        service.ingest_supply(
            provider,
            supply_result(
                provider,
                evidence.record_id,
                observed_market_fact_ids=[other_fact.record_id],
                observed_market_fact_versions=[other_fact.semantic_version],
            ),
        )


def test_supply_basis_rejects_future_known_observed_market_fact(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    fact = seed_observed_market_fact(tmp_path, effective_at=NOW, known_at=NOW + timedelta(days=1))
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="future-known market fact"):
        service.ingest_supply(
            provider,
            supply_result(
                provider,
                evidence.record_id,
                observed_market_fact_ids=[fact.record_id],
                observed_market_fact_versions=[fact.semantic_version],
            ),
        )


def test_supply_basis_rejects_future_effective_observed_market_fact(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    fact = seed_observed_market_fact(
        tmp_path,
        effective_at=NOW + timedelta(days=1),
        known_at=NOW + timedelta(days=1),
    )
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="future-effective market fact"):
        service.ingest_supply(
            provider,
            supply_result(
                provider,
                evidence.record_id,
                acquired_at=NOW + timedelta(days=2),
                observed_market_fact_ids=[fact.record_id],
                observed_market_fact_versions=[fact.semantic_version],
            ),
        )


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


def test_logical_history_reads_are_stable_for_all_record_families(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    corrected_evidence = service.ingest_evidence(
        provider,
        evidence_result(
            provider,
            acquired_at=NOW + timedelta(days=1),
            acquisition_id="evidence-2",
            supersedes_record_id=evidence.record_id,
            correction_reason="Corrected source claim",
            extracted_claim="Corrected attributable protocol fees",
        ),
    )
    fact = seed_observed_market_fact(tmp_path)
    supply = service.ingest_supply(
        provider,
        supply_result(
            provider,
            corrected_evidence.record_id,
            acquired_at=NOW + timedelta(days=2),
            observed_market_fact_ids=[fact.record_id],
            observed_market_fact_versions=[fact.semantic_version],
        ),
    )
    corrected_supply = service.ingest_supply(
        provider,
        supply_result(
            provider,
            corrected_evidence.record_id,
            acquired_at=NOW + timedelta(days=3),
            acquisition_id="supply-2",
            supersedes_record_id=supply.record_id,
            correction_reason="Corrected official supply",
            quantity="87000000",
            quantity_components=[
                ["circulating_supply", "87000000"],
                ["total_supply", "100000000"],
                ["fully_diluted_supply", "115000000"],
            ],
            observed_market_fact_ids=[fact.record_id],
            observed_market_fact_versions=[fact.semantic_version],
        ),
    )
    rule = service.ingest_rule(
        provider,
        rule_result(
            provider,
            corrected_evidence.record_id,
            acquired_at=NOW + timedelta(days=2),
        ),
    )
    corrected_rule = service.ingest_rule(
        provider,
        rule_result(
            provider,
            corrected_evidence.record_id,
            acquired_at=NOW + timedelta(days=3),
            acquisition_id="rule-2",
            supersedes_record_id=rule.record_id,
            correction_reason="Corrected value pathway",
            source_economic_flow="Corrected protocol fees",
        ),
    )

    assert service.evidence_history(evidence.logical_id) == (evidence, corrected_evidence)
    assert service.supply_history(supply.logical_id) == (supply, corrected_supply)
    assert service.rule_history(rule.logical_id) == (rule, corrected_rule)
    assert service.evidence_history("0" * 64) == ()


def test_logical_history_rejects_blank_identity(tmp_path) -> None:
    service, _, _ = setup(tmp_path)
    with pytest.raises(ValueError, match="logical_id"):
        service.evidence_history(" ")


def test_logical_history_uses_authoritative_payload_when_metadata_is_missing(tmp_path) -> None:
    service, _, provider = setup(tmp_path / "source")
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    imported_repository = SupplyAndValueCaptureRepository(tmp_path / "imported.sqlite")
    engine = create_sqlite_engine(imported_repository.path)
    session = SessionFactory(engine).create()
    try:
        RepositoryFactory(session).snapshots().save(replace(record_snapshot(evidence), metadata={}))
        session.commit()
    finally:
        session.close()
        engine.dispose()

    assert imported_repository.evidence(evidence.record_id) == evidence
    assert imported_repository.evidence_history(evidence.logical_id) == (evidence,)


def test_value_capture_rule_contract_round_trips_policy_and_limitations(tmp_path) -> None:
    service, repository, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    record = service.ingest_rule(provider, rule_result(provider, evidence.record_id))
    restored = repository.rule(record.record_id)
    assert restored == record
    assert record.mechanism_policy_version == "1.0.0"
    assert record.evidence_record_versions == ("1.0.0",)
    assert len(record.limitations) == 2


def test_strict_known_rule_excludes_expired_applicability_period(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    record = service.ingest_rule(provider, rule_result(provider, evidence.record_id))

    assert (
        service.strict_known_rule(
            entity_id=record.identity.entity_id,
            economic_claim_id=record.identity.economic_claim_id,
            representation_id=record.identity.representation_id,
            rule_type=record.rule_type,
            effective_as_of=record.applicability_end,
            known_by=record.known_at,
        )
        == record
    )
    assert (
        service.strict_known_rule(
            entity_id=record.identity.entity_id,
            economic_claim_id=record.identity.economic_claim_id,
            representation_id=record.identity.representation_id,
            rule_type=record.rule_type,
            effective_as_of=record.applicability_end + timedelta(microseconds=1),
            known_by=record.known_at,
        )
        is None
    )


def test_value_capture_rule_contract_rejects_invalid_period_and_rate(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    with pytest.raises(ValueError, match="applicability_start"):
        service.ingest_rule(
            provider,
            rule_result(
                provider,
                evidence.record_id,
                applicability_start=NOW + timedelta(days=2),
                applicability_end=NOW + timedelta(days=1),
            ),
        )
    with pytest.raises(ValueError, match="rate_or_proportion"):
        service.ingest_rule(
            provider,
            rule_result(
                provider,
                evidence.record_id,
                acquisition_id="invalid-rule-rate",
                rate_or_proportion="1.1",
            ),
        )


def test_value_capture_rule_model_rejects_blank_evidence_versions(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    record = service.ingest_rule(provider, rule_result(provider, evidence.record_id))
    with pytest.raises(ValueError, match="evidence_record_versions"):
        replace(record, evidence_record_versions=("",))


def test_value_capture_rule_rejects_mismatched_evidence_version(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="evidence version"):
        service.ingest_rule(
            provider,
            rule_result(
                provider,
                evidence.record_id,
                evidence_record_versions=["2.0.0"],
            ),
        )


def test_snapshot_rejects_future_effective_evidence(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(
        provider,
        evidence_result(
            provider,
            effective_at=NOW + timedelta(days=1),
            acquired_at=NOW + timedelta(days=1),
        ),
    )
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="future-effective"):
        service.ingest_rule(
            provider,
            rule_result(
                provider,
                evidence.record_id,
                acquired_at=NOW + timedelta(days=2),
            ),
        )


def test_value_capture_rule_rejects_duplicate_evidence_references(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    with pytest.raises(ValueError, match="evidence_record_ids must be unique"):
        service.ingest_rule(
            provider,
            rule_result(
                provider,
                evidence.record_id,
                evidence_record_ids=[evidence.record_id, evidence.record_id],
                evidence_record_versions=["1.0.0", "1.0.0"],
            ),
        )


def test_value_capture_rule_contract_rejects_null_policy_before_coercion(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="mechanism_policy_id"):
        service.ingest_rule(
            provider,
            rule_result(provider, evidence.record_id, mechanism_policy_id=None),
        )


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


def test_divergent_evidence_submissions_are_flagged_and_queryable_as_unresolved_conflicts(
    tmp_path,
) -> None:
    service, repository, provider = setup(tmp_path)
    first = service.ingest_evidence(provider, evidence_result(provider, acquisition_id="evidence-conflict-1"))
    assert first.conflict_state == "none"

    second = service.ingest_evidence(
        provider,
        evidence_result(
            provider,
            acquisition_id="evidence-conflict-2",
            acquired_at=NOW + timedelta(minutes=5),
            extracted_claim="Protocol fees are distributed under a materially different disclosed rule.",
        ),
    )

    assert second.conflict_state == "open"
    assert [item.record_id for item in service.unresolved_evidence_conflicts()] == [second.record_id]
    assert service.unresolved_supply_conflicts() == ()
    assert service.unresolved_rule_conflicts() == ()


def test_matching_duplicate_evidence_is_not_flagged_as_conflict(tmp_path) -> None:
    service, repository, provider = setup(tmp_path)
    service.ingest_evidence(provider, evidence_result(provider, acquisition_id="evidence-agree-1"))
    second = service.ingest_evidence(
        provider,
        evidence_result(
            provider,
            acquisition_id="evidence-agree-2",
            acquired_at=NOW + timedelta(minutes=5),
        ),
    )

    assert second.conflict_state == "none"
    assert service.unresolved_evidence_conflicts() == ()


def test_correction_diverging_from_its_own_predecessor_is_not_flagged_as_conflict(tmp_path) -> None:
    service, repository, provider = setup(tmp_path)
    original = service.ingest_evidence(provider, evidence_result(provider, acquisition_id="evidence-correction-1"))

    corrected = service.ingest_evidence(
        provider,
        evidence_result(
            provider,
            acquisition_id="evidence-correction-2",
            acquired_at=NOW + timedelta(minutes=5),
            extracted_claim="Protocol fees are distributed under the corrected disclosed rule.",
            supersedes_record_id=original.record_id,
            correction_reason="Corrected the disclosed distribution rule.",
        ),
    )

    assert corrected.conflict_state == "none"
    assert service.unresolved_evidence_conflicts() == ()


def test_divergent_supply_snapshots_are_flagged_and_queryable_as_unresolved_conflicts(tmp_path) -> None:
    service, repository, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider, acquisition_id="evidence-supply-conflict"))
    fact = seed_observed_market_fact(tmp_path)
    service.ingest_supply(
        provider,
        supply_result(
            provider,
            evidence.record_id,
            acquisition_id="supply-conflict-1",
            observed_market_fact_ids=[fact.record_id],
            observed_market_fact_versions=[fact.semantic_version],
        ),
    )
    second = service.ingest_supply(
        provider,
        supply_result(
            provider,
            evidence.record_id,
            acquisition_id="supply-conflict-2",
            acquired_at=NOW + timedelta(minutes=6),
            quantity="77000000",
            quantity_components=[
                ["circulating_supply", "77000000"],
                ["total_supply", "100000000"],
                ["fully_diluted_supply", "115000000"],
                ["locked_supply", "10000000"],
                ["treasury_held_supply", "2000000"],
            ],
            observed_market_fact_ids=[fact.record_id],
            observed_market_fact_versions=[fact.semantic_version],
        ),
    )

    assert second.conflict_state == "open"
    assert [item.record_id for item in service.unresolved_supply_conflicts()] == [second.record_id]


def test_strict_known_evidence_selects_correction_by_cutoff(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    original = service.ingest_evidence(provider, evidence_result(provider))
    corrected = service.ingest_evidence(
        provider,
        evidence_result(
            provider,
            acquired_at=NOW + timedelta(days=2),
            acquisition_id="evidence-correction-strict-known",
            supersedes_record_id=original.record_id,
            correction_reason="Corrected the disclosed claim.",
            extracted_claim="Corrected attributable protocol fees.",
        ),
    )

    historical = service.strict_known_evidence(
        entity_id=original.identity.entity_id,
        economic_claim_id=original.identity.economic_claim_id,
        representation_id=original.identity.representation_id,
        evidence_type=original.evidence_type,
        effective_as_of=NOW + timedelta(days=3),
        known_by=NOW + timedelta(days=1),
    )
    current = service.strict_known_evidence(
        entity_id=original.identity.entity_id,
        economic_claim_id=original.identity.economic_claim_id,
        representation_id=original.identity.representation_id,
        evidence_type=original.evidence_type,
        effective_as_of=NOW + timedelta(days=3),
        known_by=NOW + timedelta(days=3),
    )

    assert historical == original
    assert current == corrected


def test_strict_known_evidence_excludes_future_known_correction(tmp_path) -> None:
    service, _, provider = setup(tmp_path)
    original = service.ingest_evidence(provider, evidence_result(provider))

    assert (
        service.strict_known_evidence(
            entity_id=original.identity.entity_id,
            economic_claim_id=original.identity.economic_claim_id,
            representation_id=original.identity.representation_id,
            evidence_type=original.evidence_type,
            effective_as_of=original.effective_at,
            known_by=original.known_at,
        )
        == original
    )
    assert (
        service.strict_known_evidence(
            entity_id=original.identity.entity_id,
            economic_claim_id=original.identity.economic_claim_id,
            representation_id=original.identity.representation_id,
            evidence_type=original.evidence_type,
            effective_as_of=original.effective_at - timedelta(microseconds=1),
            known_by=original.known_at,
        )
        is None
    )


def test_exact_duplicate_evidence_submission_is_idempotent(tmp_path) -> None:
    service, repository, provider = setup(tmp_path)
    result = evidence_result(provider)

    first = service.ingest_evidence(provider, result)
    second = service.ingest_evidence(provider, result)

    assert first == second
    assert first.record_id == second.record_id
    assert repository.count("fundamental_evidence_records") == 1
    assert repository.count("value_capture_acquisition_receipts") == 1
    assert repository.evidence_history(first.logical_id) == (first,)


def test_divergent_duplicate_acquisition_id_is_rejected(tmp_path) -> None:
    service, repository, provider = setup(tmp_path)
    service.ingest_evidence(provider, evidence_result(provider))

    divergent = evidence_result(
        provider,
        extracted_claim="A materially different disclosed claim reusing the same acquisition id.",
    )
    with pytest.raises(ValueCaptureIntegrityError):
        service.ingest_evidence(provider, divergent)

    assert repository.count("fundamental_evidence_records") == 1
    assert repository.count("value_capture_acquisition_receipts") == 1


def test_overlapping_evidence_excludes_known_successor_predecessor(tmp_path) -> None:
    """Real SQLite-backed repository: overlapping_evidence() -- the assembly
    authority's candidate-universe query -- must exclude a superseded predecessor
    once its successor is itself strict-known at the requested cutoff, mirroring
    _strict_known()'s established supersession-selection convention."""
    service, repository, provider = setup(tmp_path)
    original = service.ingest_evidence(provider, evidence_result(provider))
    corrected = service.ingest_evidence(
        provider,
        evidence_result(
            provider,
            acquired_at=NOW + timedelta(days=2),
            acquisition_id="evidence-correction-overlapping",
            supersedes_record_id=original.record_id,
            correction_reason="Corrected the disclosed claim.",
            extracted_claim="Corrected attributable protocol fees.",
        ),
    )

    after_correction_known = service.overlapping_evidence(
        entity_id=original.identity.entity_id,
        economic_claim_id=original.identity.economic_claim_id,
        accounting_window_start=original.accounting_period_start,
        accounting_window_end=original.accounting_period_end,
        known_by=NOW + timedelta(days=3),
    )
    assert [record.record_id for record in after_correction_known] == [corrected.record_id]


def test_overlapping_evidence_preserves_predecessor_before_successor_was_known(tmp_path) -> None:
    """A predecessor must remain visible at any cutoff strictly before its
    successor became known -- the correction must never be retroactively hidden
    from a replay cutoff that predates it."""
    service, repository, provider = setup(tmp_path)
    original = service.ingest_evidence(provider, evidence_result(provider))
    service.ingest_evidence(
        provider,
        evidence_result(
            provider,
            acquired_at=NOW + timedelta(days=2),
            acquisition_id="evidence-correction-overlapping-early-cutoff",
            supersedes_record_id=original.record_id,
            correction_reason="Corrected the disclosed claim.",
            extracted_claim="Corrected attributable protocol fees.",
        ),
    )

    before_correction_known = service.overlapping_evidence(
        entity_id=original.identity.entity_id,
        economic_claim_id=original.identity.economic_claim_id,
        accounting_window_start=original.accounting_period_start,
        accounting_window_end=original.accounting_period_end,
        known_by=NOW + timedelta(days=1),
    )
    assert [record.record_id for record in before_correction_known] == [original.record_id]


# -------------------------------------------------------------------------------------
# Regression tests: out-of-order correction rejection (Issue #126)
#
# Proves that insert_record's enforcement — non-advancing recorded_at, non-advancing
# known_at, and logical_id preservation — is actually exercised and rejected for
# FundamentalEvidenceRecord, SupplyBasisSnapshot, and ValueCaptureRuleSnapshot.
# -------------------------------------------------------------------------------------


def _inject_snapshot_with_payload_override(
    repository: SupplyAndValueCaptureRepository,
    original_id: str,
    new_id: str,
    **payload_overrides: object,
) -> None:
    """Load an existing snapshot, copy it under a new id, and override named payload fields.

    Used to construct synthetic predecessors whose recorded_at/known_at/logical_id differ
    from values the service would normally produce, enabling tests of the exact branches
    inside insert_record that the service API cannot reach through ordinary ingestion.
    """
    engine = create_sqlite_engine(repository.path)
    session = SessionFactory(engine).create()
    try:
        snapshots = RepositoryFactory(session).snapshots()
        original = snapshots.load(original_id)
        assert original is not None
        payload = {**original.payload, **payload_overrides, "record_id": new_id}
        metadata = dict(original.metadata)
        if "known_at" in payload_overrides:
            metadata["known_at"] = payload_overrides["known_at"]
        snapshots.save(replace(original, id=new_id, record_ids=(new_id,), payload=payload, metadata=metadata))
        session.commit()
    finally:
        session.close()
        engine.dispose()


# --- FundamentalEvidenceRecord -------------------------------------------------------


def test_evidence_correction_non_advancing_recorded_at_is_rejected(tmp_path) -> None:
    """A correction whose recorded_at does not strictly follow the predecessor's is rejected."""
    service, _, provider = setup(tmp_path)
    original = service.ingest_evidence(provider, evidence_result(provider))
    with pytest.raises(ValueCaptureIntegrityError, match="recorded_at must follow predecessor"):
        service.ingest_evidence(
            provider,
            evidence_result(
                provider,
                # Same acquired_at as predecessor → recorded_at == predecessor.recorded_at → rejected.
                acquisition_id="ev-non-adv-rec",
                supersedes_record_id=original.record_id,
                correction_reason="Attempted non-advancing recorded_at correction",
            ),
        )


def test_evidence_correction_non_advancing_known_at_is_rejected(tmp_path) -> None:
    """A correction whose known_at does not strictly follow the predecessor's is rejected.

    Because the service always sets recorded_at == known_at == acquired_at, testing
    this branch in isolation requires a synthetic predecessor whose known_at lies further
    in the future than its recorded_at. The correction's acquired_at is chosen to advance
    past the predecessor's recorded_at (clearing the first check) while remaining below
    the predecessor's known_at (triggering the second check).
    """
    service, repository, provider = setup(tmp_path)
    original = service.ingest_evidence(provider, evidence_result(provider))
    future_known = (NOW + timedelta(days=10)).astimezone(UTC).isoformat()
    _inject_snapshot_with_payload_override(
        repository,
        original.record_id,
        "synth-ev-future-known",
        known_at=future_known,
        acquisition_id="synth-ev-future-known-acq",
    )
    with pytest.raises(ValueCaptureIntegrityError, match="known_at must follow predecessor"):
        service.ingest_evidence(
            provider,
            evidence_result(
                provider,
                acquired_at=NOW + timedelta(days=5),
                acquisition_id="ev-non-adv-known",
                supersedes_record_id="synth-ev-future-known",
                correction_reason="Attempted non-advancing known_at correction",
            ),
        )


def test_evidence_correction_logical_id_mismatch_is_rejected(tmp_path) -> None:
    """A correction that would change the logical_id relative to its predecessor is rejected."""
    service, repository, provider = setup(tmp_path)
    original = service.ingest_evidence(provider, evidence_result(provider))
    _inject_snapshot_with_payload_override(
        repository,
        original.record_id,
        "synth-ev-wrong-logid",
        logical_id="deliberately-wrong-logical-id",
        acquisition_id="synth-ev-wrong-logid-acq",
    )
    with pytest.raises(ValueCaptureIntegrityError, match="correction must preserve logical_id"):
        service.ingest_evidence(
            provider,
            evidence_result(
                provider,
                acquired_at=NOW + timedelta(days=1),
                acquisition_id="ev-logid-mismatch",
                supersedes_record_id="synth-ev-wrong-logid",
                correction_reason="Attempted logical_id-changing correction",
            ),
        )


def test_evidence_valid_advancing_correction_is_accepted(tmp_path) -> None:
    """A correction with strictly advancing recorded_at and known_at is accepted."""
    service, _, provider = setup(tmp_path)
    original = service.ingest_evidence(provider, evidence_result(provider))
    corrected = service.ingest_evidence(
        provider,
        evidence_result(
            provider,
            acquired_at=NOW + timedelta(days=1),
            acquisition_id="ev-valid-correction",
            supersedes_record_id=original.record_id,
            correction_reason="Corrected disclosed claim",
            extracted_claim="Corrected attributable protocol fees.",
        ),
    )
    assert corrected.logical_id == original.logical_id
    assert corrected.supersedes_record_id == original.record_id
    assert corrected.recorded_at > original.recorded_at
    assert corrected.known_at > original.known_at


# --- SupplyBasisSnapshot -------------------------------------------------------------


def test_supply_correction_non_advancing_recorded_at_is_rejected(tmp_path) -> None:
    """A supply correction whose recorded_at does not strictly follow the predecessor's is rejected."""
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    fact = seed_observed_market_fact(tmp_path)
    original = service.ingest_supply(
        provider,
        supply_result(
            provider,
            evidence.record_id,
            observed_market_fact_ids=[fact.record_id],
            observed_market_fact_versions=[fact.semantic_version],
        ),
    )
    with pytest.raises(ValueCaptureIntegrityError, match="recorded_at must follow predecessor"):
        service.ingest_supply(
            provider,
            supply_result(
                provider,
                evidence.record_id,
                # Same acquired_at as predecessor → recorded_at == predecessor.recorded_at → rejected.
                acquisition_id="sup-non-adv-rec",
                observed_market_fact_ids=[fact.record_id],
                observed_market_fact_versions=[fact.semantic_version],
                supersedes_record_id=original.record_id,
                correction_reason="Attempted non-advancing recorded_at correction",
            ),
        )


def test_supply_correction_non_advancing_known_at_is_rejected(tmp_path) -> None:
    """A supply correction whose known_at does not strictly follow the predecessor's is rejected."""
    service, repository, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    fact = seed_observed_market_fact(tmp_path)
    original = service.ingest_supply(
        provider,
        supply_result(
            provider,
            evidence.record_id,
            observed_market_fact_ids=[fact.record_id],
            observed_market_fact_versions=[fact.semantic_version],
        ),
    )
    future_known = (NOW + timedelta(days=10)).astimezone(UTC).isoformat()
    _inject_snapshot_with_payload_override(
        repository,
        original.record_id,
        "synth-sup-future-known",
        known_at=future_known,
        acquisition_id="synth-sup-future-known-acq",
    )
    with pytest.raises(ValueCaptureIntegrityError, match="known_at must follow predecessor"):
        service.ingest_supply(
            provider,
            supply_result(
                provider,
                evidence.record_id,
                acquired_at=NOW + timedelta(days=5),
                acquisition_id="sup-non-adv-known",
                observed_market_fact_ids=[fact.record_id],
                observed_market_fact_versions=[fact.semantic_version],
                supersedes_record_id="synth-sup-future-known",
                correction_reason="Attempted non-advancing known_at correction",
            ),
        )


def test_supply_correction_logical_id_mismatch_is_rejected(tmp_path) -> None:
    """A supply correction that would change the logical_id relative to its predecessor is rejected."""
    service, repository, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    fact = seed_observed_market_fact(tmp_path)
    original = service.ingest_supply(
        provider,
        supply_result(
            provider,
            evidence.record_id,
            observed_market_fact_ids=[fact.record_id],
            observed_market_fact_versions=[fact.semantic_version],
        ),
    )
    _inject_snapshot_with_payload_override(
        repository,
        original.record_id,
        "synth-sup-wrong-logid",
        logical_id="deliberately-wrong-logical-id",
        acquisition_id="synth-sup-wrong-logid-acq",
    )
    with pytest.raises(ValueCaptureIntegrityError, match="correction must preserve logical_id"):
        service.ingest_supply(
            provider,
            supply_result(
                provider,
                evidence.record_id,
                acquired_at=NOW + timedelta(days=1),
                acquisition_id="sup-logid-mismatch",
                observed_market_fact_ids=[fact.record_id],
                observed_market_fact_versions=[fact.semantic_version],
                supersedes_record_id="synth-sup-wrong-logid",
                correction_reason="Attempted logical_id-changing correction",
            ),
        )


def test_supply_valid_advancing_correction_is_accepted(tmp_path) -> None:
    """A supply correction with strictly advancing recorded_at and known_at is accepted."""
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    fact = seed_observed_market_fact(tmp_path)
    original = service.ingest_supply(
        provider,
        supply_result(
            provider,
            evidence.record_id,
            observed_market_fact_ids=[fact.record_id],
            observed_market_fact_versions=[fact.semantic_version],
        ),
    )
    corrected = service.ingest_supply(
        provider,
        supply_result(
            provider,
            evidence.record_id,
            acquired_at=NOW + timedelta(days=1),
            acquisition_id="sup-valid-correction",
            observed_market_fact_ids=[fact.record_id],
            observed_market_fact_versions=[fact.semantic_version],
            supersedes_record_id=original.record_id,
            correction_reason="Corrected official supply figure",
            quantity="87000000",
            quantity_components=[
                ["circulating_supply", "87000000"],
                ["total_supply", "100000000"],
                ["fully_diluted_supply", "115000000"],
                ["locked_supply", "10000000"],
                ["treasury_held_supply", "2000000"],
            ],
        ),
    )
    assert corrected.logical_id == original.logical_id
    assert corrected.supersedes_record_id == original.record_id
    assert corrected.recorded_at > original.recorded_at
    assert corrected.known_at > original.known_at


# --- ValueCaptureRuleSnapshot --------------------------------------------------------


def test_rule_correction_non_advancing_recorded_at_is_rejected(tmp_path) -> None:
    """A rule correction whose recorded_at does not strictly follow the predecessor's is rejected."""
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    original = service.ingest_rule(provider, rule_result(provider, evidence.record_id))
    with pytest.raises(ValueCaptureIntegrityError, match="recorded_at must follow predecessor"):
        service.ingest_rule(
            provider,
            rule_result(
                provider,
                evidence.record_id,
                # Same acquired_at as predecessor → recorded_at == predecessor.recorded_at → rejected.
                acquisition_id="rule-non-adv-rec",
                supersedes_record_id=original.record_id,
                correction_reason="Attempted non-advancing recorded_at correction",
            ),
        )


def test_rule_correction_non_advancing_known_at_is_rejected(tmp_path) -> None:
    """A rule correction whose known_at does not strictly follow the predecessor's is rejected."""
    service, repository, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    original = service.ingest_rule(provider, rule_result(provider, evidence.record_id))
    future_known = (NOW + timedelta(days=10)).astimezone(UTC).isoformat()
    _inject_snapshot_with_payload_override(
        repository,
        original.record_id,
        "synth-rule-future-known",
        known_at=future_known,
        acquisition_id="synth-rule-future-known-acq",
    )
    with pytest.raises(ValueCaptureIntegrityError, match="known_at must follow predecessor"):
        service.ingest_rule(
            provider,
            rule_result(
                provider,
                evidence.record_id,
                acquired_at=NOW + timedelta(days=5),
                acquisition_id="rule-non-adv-known",
                supersedes_record_id="synth-rule-future-known",
                correction_reason="Attempted non-advancing known_at correction",
            ),
        )


def test_rule_correction_logical_id_mismatch_is_rejected(tmp_path) -> None:
    """A rule correction that would change the logical_id relative to its predecessor is rejected."""
    service, repository, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    original = service.ingest_rule(provider, rule_result(provider, evidence.record_id))
    _inject_snapshot_with_payload_override(
        repository,
        original.record_id,
        "synth-rule-wrong-logid",
        logical_id="deliberately-wrong-logical-id",
        acquisition_id="synth-rule-wrong-logid-acq",
    )
    with pytest.raises(ValueCaptureIntegrityError, match="correction must preserve logical_id"):
        service.ingest_rule(
            provider,
            rule_result(
                provider,
                evidence.record_id,
                acquired_at=NOW + timedelta(days=1),
                acquisition_id="rule-logid-mismatch",
                supersedes_record_id="synth-rule-wrong-logid",
                correction_reason="Attempted logical_id-changing correction",
            ),
        )


def test_rule_valid_advancing_correction_is_accepted(tmp_path) -> None:
    """A rule correction with strictly advancing recorded_at and known_at is accepted."""
    service, _, provider = setup(tmp_path)
    evidence = service.ingest_evidence(provider, evidence_result(provider))
    original = service.ingest_rule(provider, rule_result(provider, evidence.record_id))
    corrected = service.ingest_rule(
        provider,
        rule_result(
            provider,
            evidence.record_id,
            acquired_at=NOW + timedelta(days=1),
            acquisition_id="rule-valid-correction",
            supersedes_record_id=original.record_id,
            correction_reason="Corrected value capture pathway",
            source_economic_flow="Corrected protocol fee scope",
        ),
    )
    assert corrected.logical_id == original.logical_id
    assert corrected.supersedes_record_id == original.record_id
    assert corrected.recorded_at > original.recorded_at
    assert corrected.known_at > original.known_at
