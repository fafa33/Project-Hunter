from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from hunter.valuation.models import FairValueEstimateRecord, ValuationAssessmentRecord
from hunter.valuation.repository import CanonicalValuationIntegrityError, CanonicalValuationRepository
from hunter.valuation.service import (
    CANONICAL_DISCOUNT_RATE_POLICY_ID,
    CANONICAL_DISCOUNT_RATE_POLICY_VERSION,
    CANONICAL_SENSITIVITY_POLICY_ID,
    CANONICAL_SENSITIVITY_POLICY_VERSION,
    CANONICAL_SUPPLY_BASIS_SELECTION_RULE,
    CanonicalValuationAuthorityError,
    CanonicalValuationService,
)
from hunter.valuation_methodology.repository import ValuationMethodologyRepository
from hunter.valuation_methodology.service import CanonicalValuationMethodologyAuthority
from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.providers import RegisteredValueCaptureProvider, ValueCaptureVerificationKeyRegistry
from hunter.value_capture.registry import ValueCaptureSourceConfig, ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository
from hunter.value_capture.service import SupplyAndValueCaptureAuthorityError, SupplyAndValueCaptureService

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SIGNING_KEY = b"canonical-valuation-test-key-00001"
SIGNING_KEY_ID = "canonical-valuation-test-key-v1"
ENDPOINT = "https://example.org/tokenomics/api3"


def identity() -> EconomicClaimIdentity:
    return EconomicClaimIdentity(
        entity_id="entity:canonical-test",
        economic_claim_id="economic-claim:canonical-test",
        asset_id="asset:canonical-test",
        representation_id="representation:canonical-test-ethereum",
        token_id="canonical-test",
        chain="ethereum",
        contract_address="0x0b38210ea11411557c13457d4da7dc6ea731b88a",
    )


def source() -> ValueCaptureSourceConfig:
    return ValueCaptureSourceConfig(
        source_id="official-canonical-test-tokenomics",
        authority_tier="official",
        source_type="official_disclosure",
        allowed_hosts=("example.org",),
        endpoint_patterns=("https://example.org/tokenomics/",),
        parser_version="official-tokenomics-v1",
        capabilities=(
            "evidence:official_disclosure",
            "supply:circulating_supply",
            "rule:fee_distribution",
        ),
        enabled=True,
    )


def market_fact_registry() -> MarketFactSourceRegistry:
    return MarketFactSourceRegistry.from_mapping(
        {
            "sources": [
                {
                    "source_id": "official-canonical-test-market-facts",
                    "provider_id": "official-canonical-test-market-facts-provider",
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
                            "entity_id": "entity:canonical-test",
                            "asset_id": "asset:canonical-test",
                            "representation_id": "representation:canonical-test-ethereum",
                            "chain": "ethereum",
                            "contract_address": "0x0b38210ea11411557c13457d4da7dc6ea731b88a",
                            "provider_listing_id": "canonical-test-listing",
                        }
                    ],
                    "freshness_seconds": 3600,
                    "observation_confidence": "0.9",
                    "historical_support": "current-only",
                    "limitations": "test fixture observed market fact only",
                }
            ]
        }
    )


def seed_observed_market_fact(db_path: Path) -> ObservedMarketFactRecord:
    repository = ObservedMarketFactRepository(db_path)
    service = ObservedMarketFactService(repository, market_fact_registry())
    request = MarketFactRequest(
        source_id="official-canonical-test-market-facts",
        provider_id="official-canonical-test-market-facts-provider",
        identity=MarketFactIdentity(
            entity_id="entity:canonical-test",
            asset_id="asset:canonical-test",
            representation_id="representation:canonical-test-ethereum",
            chain="ethereum",
            contract_address="0x0b38210ea11411557c13457d4da7dc6ea731b88a",
            provider_listing_id="canonical-test-listing",
        ),
        quote_currency="usd",
        requested_fact_types=("circulating_supply",),
        requested_at=NOW,
    )
    fact = NormalizedMarketFact(
        fact_type="circulating_supply",
        value="86000000",
        unit="native_units",
        quote_currency=None,
        effective_at=NOW,
        observed_at=NOW,
        confidence="0.9",
    )
    result = MarketFactAcquisitionResult(
        source_id="official-canonical-test-market-facts",
        provider_id="official-canonical-test-market-facts-provider",
        endpoint="https://example.org/market-facts/canonical-test-listing",
        parser_version="official-market-facts-v1",
        registry_fingerprint=market_fact_registry().require("official-canonical-test-market-facts").fingerprint,
        provider_source_record_id="canonical-test-listing",
        provider_source_record_version="2026-01-01",
        request=request,
        status="success",
        acquired_at=NOW,
        known_at=NOW,
        raw_payload_hash="sha256:" + "a" * 64,
        facts=(fact,),
    )
    return service.ingest(request, result, recorded_at=NOW)[0]


def evidence_result(provider, *, acquired_at=NOW + timedelta(minutes=1), acquisition_id="evidence-1", **extra):
    payload = {
        "evidence_type": "official_disclosure",
        "source_reference": "official-tokenomics-page",
        "extracted_claim": "Protocol fees are distributed under the documented rule.",
        "amount": "1000000",
        "unit": "usd",
        "accounting_period_start": NOW - timedelta(days=365),
        "accounting_period_end": NOW,
        "attribution_rule_id": "canonical-fee-distribution-v1",
        "source_methodology": "official-accrual-disclosure-v1",
        "source_record_id": "official-tokenomics-page",
        "source_record_version": "2026-01-01",
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


def supply_result(
    provider, evidence_id, market_fact, *, acquired_at=NOW + timedelta(minutes=2), acquisition_id="supply-1", **extra
):
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
        ],
        "observed_market_fact_ids": [market_fact.record_id],
        "observed_market_fact_versions": [market_fact.semantic_version],
        "source_record_id": "official-canonical-test-supply-disclosure",
        "source_record_version": "2026-01-01",
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
        "rate_or_proportion": "1",
        "governance_or_contract_authority": "Official enacted tokenomics rule",
        "mechanism_policy_id": "canonical-fee-distribution-v1",
        "mechanism_policy_version": "1.0.0",
        "dilution_treatment": "Distribution entitlement is measured after documented dilution.",
        "claim_seniority": "Pro rata eligible-holder claim after protocol liabilities.",
        "applicability_start": NOW,
        "applicability_end": NOW + timedelta(days=365),
        "limitations": ["No entitlement outside the enacted applicability period."],
        "evidence_record_versions": ["1.0.0"],
        "source_record_id": "official-canonical-test-fee-rule",
        "source_record_version": "2026-01-01",
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


def methodology_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "entity_class_criteria_id": "adr-0022-first-entity-class-v1",
        "entity_class_criteria_version": "1.0.0",
        "currency": "usd",
        "discount_rate_policy_id": CANONICAL_DISCOUNT_RATE_POLICY_ID,
        "discount_rate_policy_version": CANONICAL_DISCOUNT_RATE_POLICY_VERSION,
        "sensitivity_policy_id": CANONICAL_SENSITIVITY_POLICY_ID,
        "sensitivity_policy_version": CANONICAL_SENSITIVITY_POLICY_VERSION,
        "supply_basis_selection_rule": CANONICAL_SUPPLY_BASIS_SELECTION_RULE,
        "effective_at": NOW,
        "recorded_at": NOW,
        "known_at": NOW,
    }
    base.update(overrides)
    return base


class Fixture:
    def __init__(self, tmp_path: Path, *, methodology_overrides: dict[str, object] | None = None) -> None:
        self.db_path = tmp_path / "canonical-valuation.sqlite"
        self.market_fact = seed_observed_market_fact(self.db_path)

        verification_keys = ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY})
        self.vc_repository = SupplyAndValueCaptureRepository(self.db_path)
        self.vc_service = SupplyAndValueCaptureService(
            registry=ValueCaptureSourceRegistry((source(),)),
            repository=self.vc_repository,
            verification_keys=verification_keys,
        )
        self.provider = RegisteredValueCaptureProvider(source(), signing_key_id=SIGNING_KEY_ID, signing_key=SIGNING_KEY)

        self.evidence = self.vc_service.ingest_evidence(self.provider, evidence_result(self.provider))
        self.supply = self.vc_service.ingest_supply(
            self.provider, supply_result(self.provider, self.evidence.record_id, self.market_fact)
        )
        self.rule = self.vc_service.ingest_rule(self.provider, rule_result(self.provider, self.evidence.record_id))

        self.vm_repository = ValuationMethodologyRepository(self.db_path)
        self.methodology_authority = CanonicalValuationMethodologyAuthority(
            repository=self.vm_repository, application_root=tmp_path
        )
        self.methodology = self.methodology_authority.persist_methodology(
            **methodology_payload(**(methodology_overrides or {}))
        )

        self.repository = CanonicalValuationRepository(self.db_path)
        self.service = CanonicalValuationService(
            repository=self.repository,
            methodology_authority=self.methodology_authority,
            value_capture_repository=self.vc_repository,
            market_fact_repository=ObservedMarketFactRepository(self.db_path),
            application_root=tmp_path,
        )

    def estimate(self, **overrides: object):
        kwargs: dict[str, object] = {
            "identity": identity(),
            "evidence_type": "official_disclosure",
            "rule_type": "fee_distribution",
            "effective_at": NOW,
            "recorded_at": NOW + timedelta(minutes=10),
            "known_at": NOW + timedelta(minutes=10),
        }
        kwargs.update(overrides)
        return self.service.estimate_fair_value(**kwargs)


def setup(tmp_path: Path, *, methodology_overrides: dict[str, object] | None = None) -> Fixture:
    return Fixture(tmp_path, methodology_overrides=methodology_overrides)


# 1. Fair value persistence -----------------------------------------------------------


def test_fair_value_estimate_persists_with_valuation_assessment(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, assessment = fixture.estimate()

    assert fixture.repository.get_fair_value_estimate(estimate.record_id) == estimate
    assert fixture.repository.get_valuation_assessment(assessment.record_id) == assessment
    assert assessment.fair_value_estimate_record_id == estimate.record_id
    assert assessment.fair_value_estimate_record_version == estimate.semantic_version
    assert assessment.normalization_status == "unavailable"
    assert assessment.normalized_value is None
    assert assessment.correlation_group == "valuation-mispricing"
    assert fixture.repository.migration_ids()[0].startswith("generic-sql-")


def test_fair_value_estimate_matches_expected_dcf_arithmetic(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, _ = fixture.estimate()

    # flow=1_000_000, rate=0.15 => pv=869565.2173913...; supply=115_000_000 (fully diluted)
    assert estimate.discount_rate_applied == "0.15"
    assert estimate.total_diluted_value_p50.startswith("869565.217391")
    p50 = float(estimate.fair_value_per_diluted_unit_p50)
    p10 = float(estimate.fair_value_per_diluted_unit_p10)
    p90 = float(estimate.fair_value_per_diluted_unit_p90)
    assert p10 < p50 < p90
    assert p10 == pytest.approx(p50 * 0.9, rel=1e-9)
    assert p90 == pytest.approx(p50 * 1.1, rel=1e-9)
    assert estimate.confidence_base == "0.9"
    assert float(estimate.confidence) == pytest.approx(0.9 * 0.8 * 1.0, rel=1e-9)


# 2. Deterministic valuation ------------------------------------------------------------


def test_deterministic_valuation_produces_identical_output_for_identical_evidence(tmp_path: Path) -> None:
    fixture_a = setup(tmp_path / "a")
    fixture_b = setup(tmp_path / "b")

    estimate_a, assessment_a = fixture_a.estimate()
    estimate_b, assessment_b = fixture_b.estimate()

    assert estimate_a.record_id == estimate_b.record_id
    assert estimate_a == estimate_b
    assert assessment_a.record_id == assessment_b.record_id
    assert assessment_a == assessment_b


# 3. Historical replay across a correction boundary --------------------------------------


def test_historical_replay_selects_correct_version_across_correction(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    original_estimate, original_assessment = fixture.estimate()

    correction_known = NOW + timedelta(days=10)
    corrected_estimate, corrected_assessment = fixture.estimate(
        recorded_at=correction_known,
        known_at=correction_known,
        supersedes_estimate_record_id=original_estimate.record_id,
        supersedes_assessment_record_id=original_assessment.record_id,
        correction_reason="Independent recomputation using the same accepted evidence",
    )

    historical = fixture.service.strict_known_fair_value_estimate(
        effective_as_of=NOW, known_by=NOW + timedelta(minutes=10), logical_id=original_estimate.logical_id
    )
    current = fixture.service.strict_known_fair_value_estimate(
        effective_as_of=NOW, known_by=correction_known, logical_id=original_estimate.logical_id
    )
    assert historical == original_estimate
    assert current == corrected_estimate

    historical_assessment = fixture.service.strict_known_valuation_assessment(
        effective_as_of=NOW, known_by=NOW + timedelta(minutes=10), logical_id=original_assessment.logical_id
    )
    current_assessment = fixture.service.strict_known_valuation_assessment(
        effective_as_of=NOW, known_by=correction_known, logical_id=original_assessment.logical_id
    )
    assert historical_assessment == original_assessment
    assert current_assessment == corrected_assessment


# 4. Known-time replay -------------------------------------------------------------------


def test_known_time_replay_excludes_future_known_correction(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    original_estimate, original_assessment = fixture.estimate()
    correction_known = NOW + timedelta(days=5)
    fixture.estimate(
        recorded_at=correction_known,
        known_at=correction_known,
        supersedes_estimate_record_id=original_estimate.record_id,
        supersedes_assessment_record_id=original_assessment.record_id,
        correction_reason="Later correction",
    )

    assert (
        fixture.service.strict_known_fair_value_estimate(
            effective_as_of=NOW, known_by=correction_known - timedelta(days=1), logical_id=original_estimate.logical_id
        )
        == original_estimate
    )


# 5. Effective-time replay ----------------------------------------------------------------


def test_effective_time_replay_respects_effective_at_cutoff(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, _ = fixture.estimate()

    assert (
        fixture.service.strict_known_fair_value_estimate(
            effective_as_of=NOW - timedelta(days=1),
            known_by=NOW + timedelta(minutes=10),
            logical_id=estimate.logical_id,
        )
        is None
    )
    assert (
        fixture.service.strict_known_fair_value_estimate(
            effective_as_of=NOW, known_by=NOW + timedelta(minutes=10), logical_id=estimate.logical_id
        )
        == estimate
    )


# 6. Correction ordering ------------------------------------------------------------------


def test_correction_recorded_at_not_advancing_predecessor_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    original_estimate, original_assessment = fixture.estimate()
    with pytest.raises(CanonicalValuationIntegrityError, match="recorded_at must follow predecessor"):
        fixture.estimate(
            recorded_at=NOW + timedelta(minutes=10),
            known_at=NOW + timedelta(minutes=20),
            supersedes_estimate_record_id=original_estimate.record_id,
            supersedes_assessment_record_id=original_assessment.record_id,
            correction_reason="Attempted non-advancing correction",
        )


def test_correction_known_at_not_advancing_predecessor_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    original_estimate, original_assessment = fixture.estimate(
        recorded_at=NOW + timedelta(minutes=10),
        known_at=NOW + timedelta(minutes=30),
    )
    with pytest.raises(CanonicalValuationIntegrityError, match="known_at must follow predecessor"):
        fixture.estimate(
            recorded_at=NOW + timedelta(minutes=20),
            known_at=NOW + timedelta(minutes=25),
            supersedes_estimate_record_id=original_estimate.record_id,
            supersedes_assessment_record_id=original_assessment.record_id,
            correction_reason="Attempted non-advancing known_at",
        )


# 7. Anti-branching -------------------------------------------------------------------------


def test_branching_correction_lineage_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    original_estimate, original_assessment = fixture.estimate()
    fixture.estimate(
        recorded_at=NOW + timedelta(days=1),
        known_at=NOW + timedelta(days=1),
        supersedes_estimate_record_id=original_estimate.record_id,
        supersedes_assessment_record_id=original_assessment.record_id,
        correction_reason="First correction",
    )
    with pytest.raises(CanonicalValuationIntegrityError, match="branching"):
        fixture.estimate(
            recorded_at=NOW + timedelta(days=2),
            known_at=NOW + timedelta(days=2),
            supersedes_estimate_record_id=original_estimate.record_id,
            supersedes_assessment_record_id=original_assessment.record_id,
            correction_reason="Competing correction",
        )


# 8. Duplicate rejection ----------------------------------------------------------------------


def test_second_root_estimate_for_same_logical_id_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    fixture.estimate()
    with pytest.raises(CanonicalValuationIntegrityError, match="root record already exists"):
        fixture.estimate(recorded_at=NOW + timedelta(minutes=20), known_at=NOW + timedelta(minutes=20))


# 9. Idempotency --------------------------------------------------------------------------------


def test_retrying_identical_estimate_is_idempotent(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    first_estimate, first_assessment = fixture.estimate()
    second_estimate, second_assessment = fixture.estimate()

    assert first_estimate == second_estimate
    assert first_estimate.record_id == second_estimate.record_id
    assert first_assessment == second_assessment
    assert fixture.repository.count("fair_value_estimate_records") == 1
    assert fixture.repository.count("valuation_assessment_records") == 1


# 10. Append-only / record lineage ---------------------------------------------------------------


def test_append_only_lineage_preserves_predecessor(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    original_estimate, original_assessment = fixture.estimate()
    corrected_estimate, corrected_assessment = fixture.estimate(
        recorded_at=NOW + timedelta(days=1),
        known_at=NOW + timedelta(days=1),
        supersedes_estimate_record_id=original_estimate.record_id,
        supersedes_assessment_record_id=original_assessment.record_id,
        correction_reason="Independent recomputation",
    )

    assert fixture.service.fair_value_history(original_estimate.logical_id) == (
        original_estimate,
        corrected_estimate,
    )
    assert fixture.service.valuation_assessment_history(original_assessment.logical_id) == (
        original_assessment,
        corrected_assessment,
    )
    # The predecessor is never mutated or removed.
    assert fixture.repository.get_fair_value_estimate(original_estimate.record_id) == original_estimate


# 11. Missing-evidence rejection (fail closed, one per required input) ---------------------------


def test_missing_methodology_fails_closed(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    with pytest.raises(CanonicalValuationAuthorityError, match="ValuationMethodologySnapshot"):
        fixture.estimate(effective_at=NOW - timedelta(days=1000))


def test_missing_supply_fails_closed(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    with pytest.raises(CanonicalValuationAuthorityError, match="SupplyBasisSnapshot"):
        fixture.estimate(
            identity=EconomicClaimIdentity(
                entity_id="entity:unregistered",
                economic_claim_id="economic-claim:unregistered",
                asset_id="asset:unregistered",
                representation_id="representation:unregistered",
                token_id="unregistered",
            )
        )


def test_missing_evidence_fails_closed(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    with pytest.raises(CanonicalValuationAuthorityError, match="FundamentalEvidenceRecord"):
        fixture.estimate(evidence_type="audited_financial_disclosure")


def test_missing_rule_fails_closed(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    with pytest.raises(CanonicalValuationAuthorityError, match="ValueCaptureRuleSnapshot"):
        fixture.estimate(rule_type="revenue_distribution")


# 12. Unsupported evidence / out-of-scope rejection -----------------------------------------------


def test_out_of_scope_rule_type_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    # Persist a rule of an explicitly out-of-scope type for the same evidence/logical target.
    fixture.vc_service.ingest_rule(
        fixture.provider,
        rule_result(
            fixture.provider,
            fixture.evidence.record_id,
            acquisition_id="rule-out-of-scope",
            rule_type="no_direct_value_capture",
            mechanism_policy_id="canonical-no-direct-value-capture-v1",
        ),
    )
    with pytest.raises(CanonicalValuationAuthorityError, match="out of scope"):
        fixture.estimate(rule_type="no_direct_value_capture")


def test_attribution_mismatch_between_evidence_and_rule_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    mismatched_evidence = fixture.vc_service.ingest_evidence(
        fixture.provider,
        evidence_result(
            fixture.provider,
            acquisition_id="evidence-mismatched",
            acquired_at=NOW + timedelta(minutes=6),
            source_reference="official-tokenomics-page-mismatched",
            attribution_rule_id="some-unrelated-rule-id",
        ),
    )
    fixture.vc_service.ingest_supply(
        fixture.provider,
        supply_result(
            fixture.provider,
            mismatched_evidence.record_id,
            fixture.market_fact,
            acquisition_id="supply-mismatched",
            acquired_at=NOW + timedelta(minutes=6),
        ),
    )
    with pytest.raises(CanonicalValuationAuthorityError, match="Scope condition 4"):
        fixture.service.estimate_fair_value(
            identity=identity(),
            evidence_type="official_disclosure",
            rule_type="fee_distribution",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=10),
            known_at=NOW + timedelta(minutes=10),
        )


def test_evidence_outside_horizon_lookback_window_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    stale_evidence = fixture.vc_service.ingest_evidence(
        fixture.provider,
        evidence_result(
            fixture.provider,
            acquisition_id="evidence-stale",
            acquired_at=NOW + timedelta(minutes=5),
            source_reference="official-tokenomics-page-stale",
            accounting_period_start=NOW - timedelta(days=400),
            accounting_period_end=NOW - timedelta(days=370),
        ),
    )
    assert stale_evidence.conflict_state == "none"
    with pytest.raises(CanonicalValuationAuthorityError, match="lookback window"):
        fixture.estimate()


def test_currency_mismatch_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    mismatched_evidence = fixture.vc_service.ingest_evidence(
        fixture.provider,
        evidence_result(
            fixture.provider,
            acquisition_id="evidence-eur",
            acquired_at=NOW + timedelta(minutes=7),
            source_reference="official-tokenomics-page-eur",
            unit="eur",
        ),
    )
    fixture.vc_service.ingest_supply(
        fixture.provider,
        supply_result(
            fixture.provider,
            mismatched_evidence.record_id,
            fixture.market_fact,
            acquisition_id="supply-eur",
            acquired_at=NOW + timedelta(minutes=7),
        ),
    )
    fixture.vc_service.ingest_rule(
        fixture.provider,
        rule_result(
            fixture.provider,
            mismatched_evidence.record_id,
            acquisition_id="rule-eur",
            acquired_at=NOW + timedelta(minutes=7),
        ),
    )
    with pytest.raises(CanonicalValuationAuthorityError, match="currency conversion is not authorized"):
        fixture.estimate()


def test_negative_flow_amount_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    negative_evidence = fixture.vc_service.ingest_evidence(
        fixture.provider,
        evidence_result(
            fixture.provider,
            acquisition_id="evidence-negative",
            acquired_at=NOW + timedelta(minutes=8),
            source_reference="official-tokenomics-page-negative",
            amount="-500",
        ),
    )
    fixture.vc_service.ingest_supply(
        fixture.provider,
        supply_result(
            fixture.provider,
            negative_evidence.record_id,
            fixture.market_fact,
            acquisition_id="supply-negative",
            acquired_at=NOW + timedelta(minutes=8),
        ),
    )
    fixture.vc_service.ingest_rule(
        fixture.provider,
        rule_result(
            fixture.provider,
            negative_evidence.record_id,
            acquisition_id="rule-negative",
            acquired_at=NOW + timedelta(minutes=8),
        ),
    )
    with pytest.raises(CanonicalValuationAuthorityError, match="non-negative"):
        fixture.estimate()


def test_unsupported_discount_rate_policy_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path, methodology_overrides={"discount_rate_policy_id": "unregistered-policy"})
    assert fixture.methodology.discount_rate_policy_id == "unregistered-policy"
    with pytest.raises(CanonicalValuationAuthorityError, match="discount-rate policy"):
        fixture.estimate()


# 13. Conflict rejection --------------------------------------------------------------------------


def test_conflicted_supply_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "canonical-valuation.sqlite"
    market_fact = seed_observed_market_fact(db_path)

    verification_keys = ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY})
    vc_repository = SupplyAndValueCaptureRepository(db_path)
    vc_service = SupplyAndValueCaptureService(
        registry=ValueCaptureSourceRegistry((source(),)),
        repository=vc_repository,
        verification_keys=verification_keys,
    )
    provider = RegisteredValueCaptureProvider(source(), signing_key_id=SIGNING_KEY_ID, signing_key=SIGNING_KEY)

    evidence = vc_service.ingest_evidence(provider, evidence_result(provider))
    conflicted_supply = vc_service.ingest_supply(
        provider,
        supply_result(provider, evidence.record_id, market_fact, conflict_state="open"),
    )
    assert conflicted_supply.conflict_state == "open"
    vc_service.ingest_rule(provider, rule_result(provider, evidence.record_id))

    vm_repository = ValuationMethodologyRepository(db_path)
    methodology_authority = CanonicalValuationMethodologyAuthority(repository=vm_repository, application_root=tmp_path)
    methodology_authority.persist_methodology(**methodology_payload())

    service = CanonicalValuationService(
        repository=CanonicalValuationRepository(db_path),
        methodology_authority=methodology_authority,
        value_capture_repository=vc_repository,
        market_fact_repository=ObservedMarketFactRepository(db_path),
        application_root=tmp_path,
    )
    with pytest.raises(CanonicalValuationAuthorityError, match="SupplyBasisSnapshot"):
        service.estimate_fair_value(
            identity=identity(),
            evidence_type="official_disclosure",
            rule_type="fee_distribution",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=10),
            known_at=NOW + timedelta(minutes=10),
        )


# 14. Authority enforcement -----------------------------------------------------------------------


def test_unattested_construction_without_application_root_env_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HUNTER_APPLICATION_ROOT", raising=False)
    fixture = setup(tmp_path)
    with pytest.raises(CanonicalValuationAuthorityError, match="HUNTER_APPLICATION_ROOT"):
        CanonicalValuationService(
            repository=fixture.repository,
            methodology_authority=fixture.methodology_authority,
            value_capture_repository=fixture.vc_repository,
            market_fact_repository=ObservedMarketFactRepository(fixture.db_path),
        )


def test_relative_application_root_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    with pytest.raises(CanonicalValuationAuthorityError, match="absolute"):
        CanonicalValuationService(
            repository=fixture.repository,
            methodology_authority=fixture.methodology_authority,
            value_capture_repository=fixture.vc_repository,
            market_fact_repository=ObservedMarketFactRepository(fixture.db_path),
            application_root=Path("relative/root"),
        )


# 15. Post-merge review fix: input chronology bound to the estimate's own recorded_at ------------


def test_input_recorded_after_estimate_recorded_at_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    future_known_evidence = fixture.vc_service.ingest_evidence(
        fixture.provider,
        evidence_result(
            fixture.provider,
            acquisition_id="evidence-future-known",
            acquired_at=NOW + timedelta(minutes=20),
            source_reference="official-tokenomics-page-future-known",
        ),
    )
    assert future_known_evidence.recorded_at == NOW + timedelta(minutes=20)
    with pytest.raises(CanonicalValuationAuthorityError, match="cannot support this estimate"):
        fixture.estimate(
            recorded_at=NOW + timedelta(minutes=10),
            known_at=NOW + timedelta(minutes=30),
        )


# 16. Post-merge review fix: rate_or_proportion attribution --------------------------------------


def test_rate_or_proportion_scales_attributable_value(tmp_path: Path) -> None:
    db_path = tmp_path / "canonical-valuation.sqlite"
    market_fact = seed_observed_market_fact(db_path)

    verification_keys = ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY})
    vc_repository = SupplyAndValueCaptureRepository(db_path)
    vc_service = SupplyAndValueCaptureService(
        registry=ValueCaptureSourceRegistry((source(),)),
        repository=vc_repository,
        verification_keys=verification_keys,
    )
    provider = RegisteredValueCaptureProvider(source(), signing_key_id=SIGNING_KEY_ID, signing_key=SIGNING_KEY)

    evidence = vc_service.ingest_evidence(provider, evidence_result(provider))
    vc_service.ingest_supply(provider, supply_result(provider, evidence.record_id, market_fact))
    rule = vc_service.ingest_rule(provider, rule_result(provider, evidence.record_id, rate_or_proportion="0.1"))
    assert rule.rate_or_proportion == "0.1"

    vm_repository = ValuationMethodologyRepository(db_path)
    methodology_authority = CanonicalValuationMethodologyAuthority(repository=vm_repository, application_root=tmp_path)
    methodology_authority.persist_methodology(**methodology_payload())

    service = CanonicalValuationService(
        repository=CanonicalValuationRepository(db_path),
        methodology_authority=methodology_authority,
        value_capture_repository=vc_repository,
        market_fact_repository=ObservedMarketFactRepository(db_path),
        application_root=tmp_path,
    )
    estimate, _ = service.estimate_fair_value(
        identity=identity(),
        evidence_type="official_disclosure",
        rule_type="fee_distribution",
        effective_at=NOW,
        recorded_at=NOW + timedelta(minutes=10),
        known_at=NOW + timedelta(minutes=10),
    )
    # flow = 1_000_000 * 0.1 = 100_000; rate=0.15 => pv=86956.521739...
    assert estimate.discount_rate_applied == "0.15"
    assert estimate.total_diluted_value_p50.startswith("86956.521739")


def test_missing_rate_or_proportion_fails_closed(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    no_rate_evidence = fixture.vc_service.ingest_evidence(
        fixture.provider,
        evidence_result(
            fixture.provider,
            acquisition_id="evidence-no-rate",
            acquired_at=NOW + timedelta(minutes=3),
            source_reference="official-tokenomics-page-no-rate",
            attribution_rule_id="canonical-revenue-distribution-v1",
        ),
    )
    fixture.vc_service.ingest_rule(
        fixture.provider,
        rule_result(
            fixture.provider,
            no_rate_evidence.record_id,
            acquisition_id="rule-no-rate",
            acquired_at=NOW + timedelta(minutes=5),
            rule_type="revenue_distribution",
            mechanism_policy_id="canonical-revenue-distribution-v1",
            rate_or_proportion=None,
        ),
    )
    with pytest.raises(CanonicalValuationAuthorityError, match="rate_or_proportion"):
        fixture.service.estimate_fair_value(
            identity=identity(),
            evidence_type="official_disclosure",
            rule_type="revenue_distribution",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=10),
            known_at=NOW + timedelta(minutes=10),
        )


# 17. Post-merge review fix: accounting-period length must match the horizon ---------------------


def test_evidence_accounting_period_length_not_matching_horizon_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    short_period_evidence = fixture.vc_service.ingest_evidence(
        fixture.provider,
        evidence_result(
            fixture.provider,
            acquisition_id="evidence-short-period",
            acquired_at=NOW + timedelta(minutes=4),
            source_reference="official-tokenomics-page-short-period",
            accounting_period_start=NOW - timedelta(days=30),
            accounting_period_end=NOW,
        ),
    )
    assert short_period_evidence.conflict_state == "none"
    with pytest.raises(CanonicalValuationAuthorityError, match="horizon"):
        fixture.estimate()


# 18. Prohibited methodologies: price-derived market facts cannot influence output ----
#
# ADR 0022 "Prohibited methodologies": "Any methodology whose output is derived, directly
# or indirectly, from ObservedMarketFactRecord.spot_price, market_capitalization, or
# fully_diluted_valuation -- this is circular (using price to value price) and directly
# contradicts ADR 0020's prohibition on market-cap/completeness-derived valuation." Milestone
# 3's arithmetic (service.py's estimate_fair_value) never reads a referenced market fact's
# `value`/`fact_type` -- market facts are only strict-known/version/quality checked, never
# dereferenced into the discounted-value-capture-flow computation. The tests below prove this
# by construction: attaching a `spot_price`/`market_capitalization`/`fully_diluted_valuation`
# market fact to the strict-known SupplyBasisSnapshot, with values that differ by many orders
# of magnitude between two otherwise-identical runs, produces byte-identical fair-value output.


def _price_market_fact_registry() -> MarketFactSourceRegistry:
    return MarketFactSourceRegistry.from_mapping(
        {
            "sources": [
                {
                    "source_id": "official-canonical-test-price-facts",
                    "provider_id": "official-canonical-test-price-facts-provider",
                    "endpoint_template": "https://example.org/market-facts/{listing_id}",
                    "allowed_hosts": ["example.org"],
                    "parser_version": "official-market-facts-v1",
                    "enabled": True,
                    "capabilities": ["spot_price", "market_capitalization", "fully_diluted_valuation"],
                    "quote_currencies": ["usd"],
                    "units": {
                        "spot_price": "usd",
                        "market_capitalization": "usd",
                        "fully_diluted_valuation": "usd",
                    },
                    "supported_entity_scope": ["canonical_asset_representation"],
                    "identity_bindings": [
                        {
                            "entity_id": "entity:canonical-test",
                            "asset_id": "asset:canonical-test",
                            "representation_id": "representation:canonical-test-ethereum",
                            "chain": "ethereum",
                            "contract_address": "0x0b38210ea11411557c13457d4da7dc6ea731b88a",
                            "provider_listing_id": "canonical-test-listing",
                        }
                    ],
                    "freshness_seconds": 3600,
                    "observation_confidence": "0.9",
                    "historical_support": "current-only",
                    "limitations": "test fixture price-type market fact only, never consumed by valuation arithmetic",
                }
            ]
        }
    )


def _seed_price_market_fact(
    db_path: Path, *, fact_type: str, value: str, acquired_at: datetime
) -> ObservedMarketFactRecord:
    repository = ObservedMarketFactRepository(db_path)
    registry = _price_market_fact_registry()
    service = ObservedMarketFactService(repository, registry)
    request = MarketFactRequest(
        source_id="official-canonical-test-price-facts",
        provider_id="official-canonical-test-price-facts-provider",
        identity=MarketFactIdentity(
            entity_id="entity:canonical-test",
            asset_id="asset:canonical-test",
            representation_id="representation:canonical-test-ethereum",
            chain="ethereum",
            contract_address="0x0b38210ea11411557c13457d4da7dc6ea731b88a",
            provider_listing_id="canonical-test-listing",
        ),
        quote_currency="usd",
        requested_fact_types=(fact_type,),
        requested_at=acquired_at,
    )
    fact = NormalizedMarketFact(
        fact_type=fact_type,
        value=value,
        unit="usd",
        quote_currency="usd",
        effective_at=acquired_at,
        observed_at=acquired_at,
        confidence="0.9",
    )
    result = MarketFactAcquisitionResult(
        source_id="official-canonical-test-price-facts",
        provider_id="official-canonical-test-price-facts-provider",
        endpoint="https://example.org/market-facts/canonical-test-listing",
        parser_version="official-market-facts-v1",
        registry_fingerprint=registry.require("official-canonical-test-price-facts").fingerprint,
        provider_source_record_id="canonical-test-listing",
        provider_source_record_version="2026-01-01",
        request=request,
        status="success",
        acquired_at=acquired_at,
        known_at=acquired_at,
        raw_payload_hash="sha256:" + "c" * 64,
        facts=(fact,),
    )
    return service.ingest(request, result, recorded_at=acquired_at)[0]


def _estimate_with_price_reference(
    tmp_path: Path, *, fact_type: str, price_value: str
) -> tuple[FairValueEstimateRecord, ValuationAssessmentRecord]:
    """Build one full, isolated canonical-valuation fixture whose strict-known
    SupplyBasisSnapshot additionally references one `fact_type`-typed
    ObservedMarketFactRecord carrying `price_value`, via a normal append-only correction.
    Everything else is identical to `setup()`."""
    fixture = setup(tmp_path)
    price_fact = _seed_price_market_fact(
        fixture.db_path,
        fact_type=fact_type,
        value=price_value,
        acquired_at=NOW - timedelta(minutes=5),
    )
    fixture.vc_service.ingest_supply(
        fixture.provider,
        supply_result(
            fixture.provider,
            fixture.evidence.record_id,
            fixture.market_fact,
            acquisition_id="supply-2-price-reference",
            acquired_at=NOW + timedelta(minutes=4),
            observed_market_fact_ids=[fixture.market_fact.record_id, price_fact.record_id],
            observed_market_fact_versions=[fixture.market_fact.semantic_version, price_fact.semantic_version],
            supersedes_record_id=fixture.supply.record_id,
            correction_reason="attach a price-type market fact reference for prohibited-methodology testing",
        ),
    )
    return fixture.estimate()


@pytest.mark.parametrize("fact_type", ["spot_price", "market_capitalization", "fully_diluted_valuation"])
def test_prohibited_market_price_data_cannot_influence_fair_value_output(tmp_path: Path, fact_type: str) -> None:
    low_estimate, low_assessment = _estimate_with_price_reference(
        tmp_path / "low", fact_type=fact_type, price_value="1"
    )
    high_estimate, high_assessment = _estimate_with_price_reference(
        tmp_path / "high", fact_type=fact_type, price_value="999999999999"
    )

    assert low_estimate.fair_value_per_diluted_unit_p10 == high_estimate.fair_value_per_diluted_unit_p10
    assert low_estimate.fair_value_per_diluted_unit_p50 == high_estimate.fair_value_per_diluted_unit_p50
    assert low_estimate.fair_value_per_diluted_unit_p90 == high_estimate.fair_value_per_diluted_unit_p90
    assert low_estimate.total_diluted_value_p10 == high_estimate.total_diluted_value_p10
    assert low_estimate.total_diluted_value_p50 == high_estimate.total_diluted_value_p50
    assert low_estimate.total_diluted_value_p90 == high_estimate.total_diluted_value_p90
    assert low_estimate.model_dispersion_ratio == high_estimate.model_dispersion_ratio
    assert low_estimate.confidence == high_estimate.confidence
    assert low_assessment.confidence == high_assessment.confidence


def test_prohibited_price_fact_is_still_strict_known_validated_not_merely_ignored(tmp_path: Path) -> None:
    """The invariance proved above must not be achieved by silently skipping strict-known
    validation of the referenced price fact. Making the referenced spot_price fact
    conflicted must still fail the estimate closed, exactly as for any other referenced
    market fact (ADR 0022 Missingness) -- the price value is never used, but the record
    carrying it is still held to the same authority standard as every other input."""
    fixture = setup(tmp_path)
    same_effective_at = NOW - timedelta(minutes=5)
    _seed_price_market_fact(
        fixture.db_path,
        fact_type="spot_price",
        value="123",
        acquired_at=same_effective_at,
    )
    conflicting_price_fact = _seed_price_market_fact(
        fixture.db_path,
        fact_type="spot_price",
        value="999",
        acquired_at=same_effective_at,
    )
    assert conflicting_price_fact.conflict_state == "open"
    # hunter.value_capture's own ingest-time gate (SupplyAndValueCaptureService.
    # _require_observed_market_facts) rejects the conflicted reference before a
    # SupplyBasisSnapshot referencing it can even be persisted -- a stronger, earlier
    # defense-in-depth boundary than CanonicalValuationService's own equivalent check,
    # matching the pattern already established for value_capture/market_facts leakage
    # (see hunter.historical.valuation_calibration's leakage tests).
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="non-authoritative market fact"):
        fixture.vc_service.ingest_supply(
            fixture.provider,
            supply_result(
                fixture.provider,
                fixture.evidence.record_id,
                fixture.market_fact,
                acquisition_id="supply-2-conflicted-price-reference",
                acquired_at=NOW + timedelta(minutes=4),
                observed_market_fact_ids=[fixture.market_fact.record_id, conflicting_price_fact.record_id],
                observed_market_fact_versions=[
                    fixture.market_fact.semantic_version,
                    conflicting_price_fact.semantic_version,
                ],
                supersedes_record_id=fixture.supply.record_id,
                correction_reason="attach a conflicted price-type market fact reference",
            ),
        )
