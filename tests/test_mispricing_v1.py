from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
from hunter.mispricing import (
    MISPRICING_SCHEMA_VERSION,
    CanonicalMispricingAuthorityError,
    CanonicalMispricingIntegrityError,
    CanonicalMispricingRepository,
    CanonicalMispricingService,
    MispricingAssessmentRecord,
    MispricingMethodologySnapshot,
)
from hunter.mispricing.models import SUPPORTED_FORMULA_VERSION
from hunter.valuation.repository import CanonicalValuationRepository
from hunter.valuation.service import (
    CANONICAL_DISCOUNT_RATE_POLICY_ID,
    CANONICAL_DISCOUNT_RATE_POLICY_VERSION,
    CANONICAL_SENSITIVITY_POLICY_ID,
    CANONICAL_SENSITIVITY_POLICY_VERSION,
    CANONICAL_SUPPLY_BASIS_SELECTION_RULE,
    CanonicalValuationService,
)
from hunter.valuation_methodology.repository import ValuationMethodologyRepository
from hunter.valuation_methodology.service import CanonicalValuationMethodologyAuthority
from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.providers import RegisteredValueCaptureProvider, ValueCaptureVerificationKeyRegistry
from hunter.value_capture.registry import ValueCaptureSourceConfig, ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository
from hunter.value_capture.service import SupplyAndValueCaptureService

NOW = datetime(2026, 1, 1, tzinfo=UTC)

# The native evidence chain is ingested at NOW+1min (evidence) and NOW+2min (supply/rule),
# the fair-value estimate is recorded/known at NOW+10min, and the spot-price observation is
# ingested at NOW+11min, so strict-known reads for the mispricing assessment must use a
# recorded/known time strictly after the last acquisition while keeping effective_at=NOW so
# the fair-value estimate and the observed spot price are temporally compatible (ADR 0021
# 7-day observation tolerance).
RECORDED = NOW + timedelta(minutes=15)

SIGNING_KEY = b"canonical-mispricing-test-key-00001"
SIGNING_KEY_ID = "canonical-mispricing-test-key-v1"
ENDPOINT = "https://example.org/tokenomics/mispricing"

ENTITY_ID = "entity:cmp-mispricing"
ASSET_ID = "asset:cmp-mispricing"
REPRESENTATION_ID = "representation:cmp-mispricing-ethereum"
CONTRACT = "0x6666666666666666666666666666666666666666"
LISTING = "cmp-mispricing-listing"


def identity() -> EconomicClaimIdentity:
    return EconomicClaimIdentity(
        entity_id=ENTITY_ID,
        economic_claim_id="economic-claim:cmp-mispricing",
        asset_id=ASSET_ID,
        representation_id=REPRESENTATION_ID,
        token_id="cmp-mispricing",
        chain="ethereum",
        contract_address=CONTRACT,
    )


def other_identity() -> EconomicClaimIdentity:
    return EconomicClaimIdentity(
        entity_id="entity:cmp-other",
        economic_claim_id="economic-claim:cmp-other",
        asset_id="asset:cmp-other",
        representation_id="representation:cmp-other-ethereum",
        token_id="cmp-other",
        chain="ethereum",
        contract_address="0x7777777777777777777777777777777777777777",
    )


def market_fact_identity() -> MarketFactIdentity:
    return MarketFactIdentity(
        entity_id=ENTITY_ID,
        asset_id=ASSET_ID,
        representation_id=REPRESENTATION_ID,
        chain="ethereum",
        contract_address=CONTRACT,
        provider_listing_id=LISTING,
    )


def value_capture_source() -> ValueCaptureSourceConfig:
    return ValueCaptureSourceConfig(
        source_id="official-cmp-mispricing-tokenomics",
        authority_tier="official",
        source_type="official_disclosure",
        allowed_hosts=("example.org",),
        endpoint_patterns=("https://example.org/tokenomics/",),
        parser_version="official-cmp-mispricing-tokenomics-v1",
        capabilities=(
            "evidence:official_disclosure",
            "supply:circulating_supply",
            "rule:fee_distribution",
        ),
        enabled=True,
    )


def market_fact_registry() -> MarketFactSourceRegistry:
    # One source authorizes both observed-fact capabilities: the circulating supply fact
    # referenced by the fair-value chain's SupplyBasisSnapshot and the separate spot price
    # consumed by the Canonical Mispricing authority (ADR 0021: providers own observed
    # facts such as spot price; they never declare mispricing).
    return MarketFactSourceRegistry.from_mapping(
        {
            "sources": [
                {
                    "source_id": "official-cmp-mispricing-market-facts",
                    "provider_id": "official-cmp-mispricing-market-facts-provider",
                    "endpoint_template": "https://example.org/market-facts/{listing_id}",
                    "allowed_hosts": ["example.org"],
                    "parser_version": "official-cmp-mispricing-market-facts-v1",
                    "enabled": True,
                    "capabilities": ["circulating_supply", "spot_price"],
                    "quote_currencies": ["usd"],
                    "units": {
                        "circulating_supply": "native_units",
                        "spot_price": "usd",
                    },
                    "supported_entity_scope": ["canonical_asset_representation"],
                    "identity_bindings": [
                        {
                            "entity_id": ENTITY_ID,
                            "asset_id": ASSET_ID,
                            "representation_id": REPRESENTATION_ID,
                            "chain": "ethereum",
                            "contract_address": CONTRACT,
                            "provider_listing_id": LISTING,
                        }
                    ],
                    "freshness_seconds": 3600,
                    "observation_confidence": "0.9",
                    "historical_support": "current-only",
                    "limitations": "test fixture observed market facts only; never mispricing",
                }
            ]
        }
    )


def seed_circulating_supply_fact(db_path: Path) -> ObservedMarketFactRecord:
    return _seed_fact(db_path, fact_type="circulating_supply", value="86000000", unit="native_units", effective_at=NOW)


def seed_spot_price_fact(
    db_path: Path, *, value: str = "0.01", effective_at: datetime = NOW
) -> ObservedMarketFactRecord:
    return _seed_fact(db_path, fact_type="spot_price", value=value, unit="usd", effective_at=effective_at)


def _seed_fact(
    db_path: Path, *, fact_type: str, value: str, unit: str, effective_at: datetime
) -> ObservedMarketFactRecord:
    repository = ObservedMarketFactRepository(db_path)
    registry = market_fact_registry()
    service = ObservedMarketFactService(repository, registry)
    request = MarketFactRequest(
        source_id="official-cmp-mispricing-market-facts",
        provider_id="official-cmp-mispricing-market-facts-provider",
        identity=market_fact_identity(),
        quote_currency="usd",
        requested_fact_types=(fact_type,),
        requested_at=effective_at,
    )
    fact = NormalizedMarketFact(
        fact_type=fact_type,  # type: ignore[arg-type]
        value=value,
        unit=unit,
        quote_currency="usd" if fact_type == "spot_price" else None,
        effective_at=effective_at,
        observed_at=effective_at,
        confidence="0.9",
    )
    result = MarketFactAcquisitionResult(
        source_id="official-cmp-mispricing-market-facts",
        provider_id="official-cmp-mispricing-market-facts-provider",
        endpoint="https://example.org/market-facts/cmp-mispricing-listing",
        parser_version="official-cmp-mispricing-market-facts-v1",
        registry_fingerprint=registry.require("official-cmp-mispricing-market-facts").fingerprint,
        provider_source_record_id=LISTING,
        provider_source_record_version="2026-01-01",
        request=request,
        status="success",
        acquired_at=effective_at,
        known_at=effective_at,
        raw_payload_hash="sha256:" + "a" * 64,
        facts=(fact,),
    )
    return service.ingest(request, result, recorded_at=effective_at)[0]


def evidence_result(provider, **extra: object):
    payload = {
        "evidence_type": "official_disclosure",
        "source_reference": "official-cmp-mispricing-disclosure",
        "extracted_claim": "Protocol fees are distributed under the documented rule.",
        "amount": "1000000",
        "unit": "usd",
        "accounting_period_start": NOW - timedelta(days=365),
        "accounting_period_end": NOW,
        "attribution_rule_id": "cmp-mispricing-fee-distribution-v1",
        "source_methodology": "official-accrual-disclosure-v1",
        "source_record_id": "official-cmp-mispricing-disclosure",
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
        acquisition_id="evidence-1",
        acquired_at=NOW + timedelta(minutes=1),
        identity=identity(),
        payload=payload,
    )


def supply_result(provider, evidence_id: str, market_fact: ObservedMarketFactRecord, **extra: object):
    payload = {
        "supply_basis_type": "circulating_supply",
        "quantity": "86000000",
        "unit": "native_units",
        "denominator_meaning": "Provider-observed circulating units for the canonical representation.",
        "supply_policy_id": "cmp-mispricing-token-supply-v1",
        "supply_policy_version": "1.0.0",
        "quantity_components": [
            ["circulating_supply", "86000000"],
            ["total_supply", "100000000"],
            ["fully_diluted_supply", "115000000"],
        ],
        "observed_market_fact_ids": [market_fact.record_id],
        "observed_market_fact_versions": [market_fact.semantic_version],
        "source_record_id": "official-cmp-mispricing-supply-disclosure",
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
        acquisition_id="supply-1",
        acquired_at=NOW + timedelta(minutes=2),
        identity=identity(),
        payload=payload,
    )


def rule_result(provider, evidence_id: str, **extra: object):
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
        "mechanism_policy_id": "cmp-mispricing-fee-distribution-v1",
        "mechanism_policy_version": "1.0.0",
        "dilution_treatment": "Distribution entitlement is measured after documented dilution.",
        "claim_seniority": "Pro rata eligible-holder claim after protocol liabilities.",
        "applicability_start": NOW,
        "applicability_end": NOW + timedelta(days=365),
        "limitations": ["No entitlement outside the enacted applicability period."],
        "evidence_record_versions": ["1.0.0"],
        "source_record_id": "official-cmp-mispricing-fee-rule",
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
        acquisition_id="rule-1",
        acquired_at=NOW + timedelta(minutes=2),
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
    """Seeds one canonical economic entity with the complete native evidence chain --
    observed market facts, value-capture flow evidence, supply basis, value-capture rule,
    valuation methodology, and a fair-value estimate -- then exposes the Canonical
    Mispricing authority for the Issue #160 verification scenarios."""

    def __init__(self, tmp_path: Path, *, spot_price_effective_at: datetime = NOW) -> None:
        self.db_path = tmp_path / "mispricing.sqlite"
        self.circulating_supply_fact = seed_circulating_supply_fact(self.db_path)
        self.spot_price_fact = seed_spot_price_fact(self.db_path, effective_at=spot_price_effective_at)

        verification_keys = ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY})
        self.vc_repository = SupplyAndValueCaptureRepository(self.db_path)
        self.vc_service = SupplyAndValueCaptureService(
            registry=ValueCaptureSourceRegistry((value_capture_source(),)),
            repository=self.vc_repository,
            verification_keys=verification_keys,
        )
        self.provider = RegisteredValueCaptureProvider(
            value_capture_source(), signing_key_id=SIGNING_KEY_ID, signing_key=SIGNING_KEY
        )
        self.evidence = self.vc_service.ingest_evidence(self.provider, evidence_result(self.provider))
        self.supply = self.vc_service.ingest_supply(
            self.provider,
            supply_result(self.provider, self.evidence.record_id, self.circulating_supply_fact),
        )
        self.rule = self.vc_service.ingest_rule(self.provider, rule_result(self.provider, self.evidence.record_id))

        self.vm_repository = ValuationMethodologyRepository(self.db_path)
        self.methodology_authority = CanonicalValuationMethodologyAuthority(
            repository=self.vm_repository, application_root=tmp_path
        )
        self.methodology = self.methodology_authority.persist_methodology(**methodology_payload())

        self.repository = CanonicalValuationRepository(self.db_path)
        self.valuation_service = CanonicalValuationService(
            repository=self.repository,
            methodology_authority=self.methodology_authority,
            value_capture_repository=self.vc_repository,
            market_fact_repository=ObservedMarketFactRepository(self.db_path),
            application_root=tmp_path,
        )
        self.estimate, self.valuation_assessment = self.valuation_service.estimate_fair_value(
            identity=identity(),
            evidence_type="official_disclosure",
            rule_type="fee_distribution",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=10),
            known_at=NOW + timedelta(minutes=10),
        )

        self.mispricing_repository = CanonicalMispricingRepository(self.db_path)
        self.service = CanonicalMispricingService(
            repository=self.mispricing_repository,
            valuation_repository=self.repository,
            market_fact_repository=ObservedMarketFactRepository(self.db_path),
            application_root=tmp_path,
        )
        self.mispricing_methodology = self.service.persist_mispricing_methodology(
            methodology_id="cmp-mispricing-methodology-v1",
            required_quote_currency="usd",
            entity_class_criteria_id="adr-0021-first-entity-class-v1",
            entity_class_criteria_version="1.0.0",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=12),
            known_at=NOW + timedelta(minutes=12),
        )

    def assess(self, **overrides: object) -> MispricingAssessmentRecord:
        kwargs: dict[str, object] = {
            "identity": identity(),
            "methodology_record_id": self.mispricing_methodology.record_id,
            "fair_value_logical_id": self.estimate.logical_id,
            "effective_at": NOW,
            "recorded_at": RECORDED,
            "known_at": RECORDED,
        }
        kwargs.update(overrides)
        return self.service.assess(**kwargs)


# 1. Fixed-parameter enforcement ---------------------------------------------------------


def test_mispricing_methodology_and_assessment_persist_with_fixed_parameters(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    methodology = fixture.mispricing_methodology
    assert methodology.schema_version == MISPRICING_SCHEMA_VERSION
    assert methodology.authorizing_adr_reference == "ADR-0021"
    # ADR 0021-fixed parameters are injected by the service; they are never caller-supplied
    # through the public API (mirrors hunter.valuation_methodology's fixed-parameter model).
    assert methodology.raw_formula == "(fair_value_p50 - observed_market_price) / observed_market_price"
    assert methodology.sign_convention == "positive-undervaluation"
    assert methodology.neutral_point == "0"
    assert methodology.raw_range == "[-1, +inf)"
    assert methodology.timestamp_tolerance_days == 7
    assert methodology.required_supply_basis == "fully_diluted_supply"
    assert methodology.market_observation_fact_type == "spot_price"
    assert methodology.correlation_group == "valuation-mispricing"
    assert methodology.normalization_policy_id is None
    assert methodology.calibration_policy_id is None

    assessment = fixture.assess()
    assert assessment.schema_version == MISPRICING_SCHEMA_VERSION
    assert assessment.correlation_group == "valuation-mispricing"
    assert assessment.neutral_point == "0"
    assert assessment.sign_convention == "positive-undervaluation"


def test_model_rejects_forged_fixed_parameters(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="raw_formula"):
        MispricingMethodologySnapshot(
            record_id="r",
            logical_id="l",
            schema_version=MISPRICING_SCHEMA_VERSION,
            semantic_version="1.0.0",
            methodology_id="m",
            formula_version=SUPPORTED_FORMULA_VERSION,
            raw_formula="some-other-formula",
            sign_convention="positive-undervaluation",
            neutral_point="0",
            raw_range="[-1, +inf)",
            timestamp_tolerance_days=7,
            required_quote_currency="usd",
            required_supply_basis="fully_diluted_supply",
            market_observation_fact_type="spot_price",
            supported_entity_class="canonical_asset_representation",
            entity_class_criteria_id="c",
            entity_class_criteria_version="1.0.0",
            correlation_group="valuation-mispricing",
            confidence_policy_id="p",
            confidence_policy_version="1.0.0",
            normalization_policy_id=None,
            calibration_policy_id=None,
            authorizing_adr_reference="ADR-0021",
            authorized_by="canonical-mispricing-authority-v1",
            effective_at=NOW,
            recorded_at=NOW,
            known_at=NOW,
            content_hash="h" * 64,
            quality_state="accepted",
            conflict_state="none",
        )


# 2. Deterministic raw arithmetic (no averaging, no fabricated evidence) ----------------


def test_assessment_preserves_raw_ratio_and_uncertainty_interval(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.assess()

    assert assessment.availability_state == "UNAVAILABLE_UNCALIBRATED_NORMALIZATION"
    assert assessment.normalization_status == "unavailable"
    assert assessment.normalized_value is None
    # The assessment references -- never re-counts -- the exact-version fair-value estimate
    # and the separate observed spot price (ADR 0021 anti-double-counting).
    assert assessment.fair_value_record_id == fixture.estimate.record_id
    assert assessment.fair_value_record_version == fixture.estimate.semantic_version
    assert assessment.market_fact_record_id == fixture.spot_price_fact.record_id
    assert assessment.market_fact_record_version == fixture.spot_price_fact.semantic_version
    assert assessment.methodology_record_id == fixture.mispricing_methodology.record_id
    assert assessment.methodology_record_version == fixture.mispricing_methodology.semantic_version

    price = Decimal(fixture.spot_price_fact.value)
    p10 = Decimal(fixture.estimate.fair_value_per_diluted_unit_p10)
    p50 = Decimal(fixture.estimate.fair_value_per_diluted_unit_p50)
    p90 = Decimal(fixture.estimate.fair_value_per_diluted_unit_p90)
    # Deterministic integrity: the raw signed ratio uses the fair-value p50 reference only
    # -- no averaging of the p10/p50/p90 distribution (ADR 0021 no-averaging requirement).
    assert assessment.raw_signed_ratio == ((p50 - price) / price).to_eng_string()
    # Proves the ratio is the p50 reference, not the mean of the p10/p50/p90 distribution.
    assert Decimal(assessment.raw_signed_ratio or "0") != ((p10 + p50 + p90) / 3 - price) / price
    assert assessment.uncertainty_interval_lower == ((p10 - price) / price).to_eng_string()
    assert assessment.uncertainty_interval_upper == ((p90 - price) / price).to_eng_string()
    assert (
        Decimal(assessment.uncertainty_interval_lower)
        < Decimal(assessment.raw_signed_ratio or "0")
        < Decimal(assessment.uncertainty_interval_upper)
    )


def test_confidence_decomposition_and_weakest_component_rule(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.assess()
    components = [
        Decimal(assessment.confidence_fair_value),
        Decimal(assessment.confidence_market_quality),
        Decimal(assessment.confidence_dispersion),
        Decimal(assessment.confidence_timestamp),
    ]
    assert all(0 <= component <= 1 for component in components)
    assert Decimal(assessment.confidence) <= min(components)
    # timestamp distance is 0 at this cutoff, so the timestamp factor is 1; the overall
    # confidence is capped by the weakest remaining component (ADR 0021 confidence rules).
    assert Decimal(assessment.confidence_timestamp) == 1
    assert Decimal(assessment.confidence) == min(components)


# 3. Insert-identical idempotency ---------------------------------------------------------


def test_insert_identical_reproduces_same_record_ids(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    first = fixture.service.persist_mispricing_methodology(
        methodology_id="cmp-mispricing-methodology-v1",
        required_quote_currency="usd",
        entity_class_criteria_id="adr-0021-first-entity-class-v1",
        entity_class_criteria_version="1.0.0",
        effective_at=NOW,
        recorded_at=NOW + timedelta(minutes=12),
        known_at=NOW + timedelta(minutes=12),
    )
    assert first.record_id == fixture.mispricing_methodology.record_id
    assert first.content_hash == fixture.mispricing_methodology.content_hash

    assessment_first = fixture.assess()
    assessment_second = fixture.assess()
    assert assessment_first.record_id == assessment_second.record_id
    assert assessment_first.content_hash == assessment_second.content_hash
    assert fixture.mispricing_repository.count("mispricing_assessments") == 1


# 4. Divergent duplicate rejection ---------------------------------------------------------


def test_divergent_duplicate_root_methodology_is_rejected(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    with pytest.raises(CanonicalMispricingIntegrityError, match="root record already exists"):
        fixture.service.persist_mispricing_methodology(
            methodology_id="cmp-mispricing-methodology-v1",
            required_quote_currency="usd",
            entity_class_criteria_id="adr-0021-first-entity-class-v1",
            entity_class_criteria_version="2.0.0",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=12),
            known_at=NOW + timedelta(minutes=12),
        )


def test_divergent_duplicate_root_assessment_is_rejected(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.assess()
    with pytest.raises(CanonicalMispricingIntegrityError, match="root record already exists"):
        fixture.assess(recorded_at=NOW + timedelta(minutes=16), known_at=NOW + timedelta(minutes=16))


# 5. Correction lineage ----------------------------------------------------------------------


def test_correction_and_supersession_lineage(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    original = fixture.assess()
    correction = fixture.assess(
        recorded_at=NOW + timedelta(minutes=16),
        known_at=NOW + timedelta(minutes=16),
        supersedes_record_id=original.record_id,
        correction_reason="corrected fair value reference",
    )
    assert correction.supersedes_record_id == original.record_id
    assert correction.correction_reason
    assert correction.logical_id == original.logical_id
    history = fixture.service.assessment_history(original.logical_id)
    assert [item.record_id for item in history] == [original.record_id, correction.record_id]
    # The predecessor is never mutated or removed.
    assert fixture.service.get_assessment(original.record_id) == original


def test_branching_correction_is_prohibited(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    original = fixture.assess()
    fixture.assess(
        recorded_at=NOW + timedelta(minutes=16),
        known_at=NOW + timedelta(minutes=16),
        supersedes_record_id=original.record_id,
        correction_reason="first correction",
    )
    with pytest.raises(CanonicalMispricingIntegrityError, match="branching correction lineage"):
        fixture.assess(
            recorded_at=NOW + timedelta(minutes=17),
            known_at=NOW + timedelta(minutes=17),
            supersedes_record_id=original.record_id,
            correction_reason="second correction",
        )


def test_correction_requires_later_chronology(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    original = fixture.assess()
    with pytest.raises(CanonicalMispricingIntegrityError, match="recorded_at must follow"):
        fixture.assess(
            recorded_at=RECORDED,
            known_at=RECORDED,
            supersedes_record_id=original.record_id,
            correction_reason="invalid chronology",
        )


# 6. Strict-known replay ----------------------------------------------------------------------


def test_strict_known_replay_excludes_future_known_records(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.assess()
    earlier = NOW + timedelta(minutes=14)
    assert (
        fixture.service.strict_known_assessment(effective_as_of=NOW, known_by=earlier, logical_id=assessment.logical_id)
        is None
    )
    later = NOW + timedelta(minutes=16)
    replay = fixture.service.strict_known_assessment(
        effective_as_of=NOW, known_by=later, logical_id=assessment.logical_id
    )
    assert replay is not None
    assert replay.record_id == assessment.record_id


def test_strict_known_replay_reproduces_identical_assessment(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.assess()
    replay = fixture.service.strict_known_assessment(
        effective_as_of=NOW, known_by=RECORDED, logical_id=assessment.logical_id
    )
    assert replay is not None
    assert replay.record_id == assessment.record_id
    assert replay.raw_signed_ratio == assessment.raw_signed_ratio
    assert replay.uncertainty_interval_lower == assessment.uncertainty_interval_lower
    assert replay.uncertainty_interval_upper == assessment.uncertainty_interval_upper
    assert replay.confidence == assessment.confidence


def test_strict_known_replay_selects_current_not_superseded(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    original = fixture.assess()
    correction = fixture.assess(
        recorded_at=NOW + timedelta(minutes=16),
        known_at=NOW + timedelta(minutes=16),
        supersedes_record_id=original.record_id,
        correction_reason="corrected fair value reference",
    )
    replay = fixture.service.strict_known_assessment(
        effective_as_of=NOW,
        known_by=NOW + timedelta(minutes=16),
        logical_id=original.logical_id,
    )
    assert replay is not None
    assert replay.record_id == correction.record_id
    assert replay.record_id != original.record_id


def test_strict_known_methodology_replay(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    methodology = fixture.mispricing_methodology
    earlier = NOW + timedelta(minutes=11)
    assert (
        fixture.service.strict_known_methodology(
            effective_as_of=NOW, known_by=earlier, logical_id=methodology.logical_id
        )
        is None
    )
    replay = fixture.service.strict_known_methodology(
        effective_as_of=NOW, known_by=RECORDED, logical_id=methodology.logical_id
    )
    assert replay is not None
    assert replay.record_id == methodology.record_id


def test_effective_time_cutoff_respects_fair_value_and_price(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.assess()
    assert (
        fixture.service.strict_known_assessment(
            effective_as_of=NOW - timedelta(days=1),
            known_by=RECORDED,
            logical_id=assessment.logical_id,
        )
        is None
    )


# 7. Explicit missingness ----------------------------------------------------------------------


def test_missing_fair_value_is_explicit_missingness(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.service.assess(
        identity=identity(),
        methodology_record_id=fixture.mispricing_methodology.record_id,
        fair_value_logical_id="missing-fair-value-logical-id",
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_NO_FAIR_VALUE"
    # Explicit missingness (ADR 0020 "Confidence is unavailable, not zero"): an unavailable
    # assessment must never encode unavailable as a zero confidence.
    assert assessment.confidence == ""
    assert assessment.confidence_fair_value == ""
    assert assessment.confidence_market_quality == ""
    assert assessment.confidence_dispersion == ""
    assert assessment.confidence_timestamp == ""
    assert assessment.normalization_status == "unavailable"
    assert assessment.normalized_value is None
    assert assessment.raw_signed_ratio is None


def test_unavailable_confidence_cannot_be_encoded_as_zero(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    unavailable = fixture.service.assess(
        identity=identity(),
        methodology_record_id=fixture.mispricing_methodology.record_id,
        fair_value_logical_id="missing-fair-value-logical-id",
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    with pytest.raises(ValueError, match="must be blank"):
        replace(unavailable, confidence="0")
    with pytest.raises(ValueError, match="must be blank"):
        replace(unavailable, confidence_fair_value="0")


# 8. Unavailable and unsupported methodology ---------------------------------------------------


def test_unavailable_methodology_remains_explicit(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.service.assess(
        identity=identity(),
        methodology_record_id="nonexistent-methodology-record",
        fair_value_logical_id=fixture.estimate.logical_id,
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_UNSUPPORTED_METHODOLOGY"
    assert assessment.confidence == ""
    assert assessment.normalization_status == "unavailable"
    assert assessment.normalized_value is None


def test_unsupported_methodology_remains_explicit_unavailable(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    unsupported = fixture.service.persist_mispricing_methodology(
        methodology_id="cmp-mispricing-methodology-unsupported",
        required_quote_currency="usd",
        entity_class_criteria_id="adr-0021-first-entity-class-v1",
        entity_class_criteria_version="1.0.0",
        effective_at=NOW,
        recorded_at=NOW + timedelta(minutes=12),
        known_at=NOW + timedelta(minutes=12),
        formula_version="mispricing-raw-ratio-v999",
    )
    assert unsupported.formula_version == "mispricing-raw-ratio-v999"
    assessment = fixture.service.assess(
        identity=identity(),
        methodology_record_id=unsupported.record_id,
        fair_value_logical_id=fixture.estimate.logical_id,
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    # Unsupported methodology stays explicitly unavailable (ADR 0020); it never falls back
    # to a different formula or a fabricated value.
    assert assessment.availability_state == "UNAVAILABLE_UNSUPPORTED_METHODOLOGY"
    assert assessment.raw_signed_ratio is None
    assert assessment.confidence == ""


# 9. Input compatibility gates -----------------------------------------------------------------


def test_identity_mismatch_is_explicit_unavailable(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.service.assess(
        identity=other_identity(),
        methodology_record_id=fixture.mispricing_methodology.record_id,
        fair_value_logical_id=fixture.estimate.logical_id,
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_IDENTITY_MISMATCH"
    assert assessment.confidence == ""


def test_timestamp_tolerance_rejects_incompatible_times(tmp_path: Path) -> None:
    # A spot price observed 10 days before the fair-value effective time violates the
    # fixed 7-day observation tolerance (ADR 0021 "market observation tolerance is fixed by
    # methodology").
    stale = NOW - timedelta(days=10)
    fixture = Fixture(tmp_path, spot_price_effective_at=stale)
    assessment = fixture.assess()
    assert assessment.availability_state == "UNAVAILABLE_INCOMPATIBLE_TIMES"
    assert assessment.confidence == ""


# 10. Deterministic ordering --------------------------------------------------------------------


def test_deterministic_ordering_of_history(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    original = fixture.assess()
    first_correction = fixture.assess(
        recorded_at=NOW + timedelta(minutes=16),
        known_at=NOW + timedelta(minutes=16),
        supersedes_record_id=original.record_id,
        correction_reason="first correction",
    )
    second_correction = fixture.assess(
        recorded_at=NOW + timedelta(minutes=17),
        known_at=NOW + timedelta(minutes=17),
        supersedes_record_id=first_correction.record_id,
        correction_reason="second correction",
    )
    history = fixture.service.assessment_history(original.logical_id)
    assert [item.record_id for item in history] == [
        original.record_id,
        first_correction.record_id,
        second_correction.record_id,
    ]
    # Repository reads are deterministic and identical to the service's mechanical reads.
    assert [item.record_id for item in fixture.mispricing_repository.assessment_history(original.logical_id)] == [
        item.record_id for item in history
    ]


# 11. ADR 0009 repository purity ---------------------------------------------------------------


def test_repository_has_no_replay_selection_or_write_methods(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    # The repository is a purely mechanical persistence adapter (ADR 0009): no write/apply
    # method and no replay selection of any kind.
    for name in (
        "strict_known_methodology",
        "strict_known_assessment",
        "apply",
        "write",
        "assess",
        "persist",
    ):
        assert not hasattr(fixture.mispricing_repository, name)
    # The service owns every strict-known replay read path.
    assert hasattr(fixture.service, "strict_known_methodology")
    assert hasattr(fixture.service, "strict_known_assessment")


# 12. Provider authority boundary --------------------------------------------------------------


def test_market_provider_observes_facts_and_never_mispricing(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fact = fixture.spot_price_fact
    # Providers own only observed facts; the spot price carries no mispricing artifact.
    assert fact.fact_type == "spot_price"
    assert not hasattr(fact, "mispricing")
    assert not hasattr(fact, "raw_signed_ratio")
    assert not hasattr(fact, "availability_state")
    # The assessment is the only mispricing artifact, produced exclusively by the service.
    fixture.assess()
    assert fixture.mispricing_repository.count("mispricing_assessments") == 1


def test_prohibited_cross_authority_composition_is_absent(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    # ADR 0021 authority matrix: the mispricing authority cannot calculate Asymmetry,
    # Opportunity, Market Validation scoring, weighting, recommendation, or ranking.
    for attribute in (
        "asymmetry",
        "opportunity",
        "rank",
        "recommend",
        "score",
        "portfolio",
        "normalize",
    ):
        assert not hasattr(fixture.service, attribute)


# 13. Authority enforcement --------------------------------------------------------------------


def test_unattested_construction_without_application_root_env_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HUNTER_APPLICATION_ROOT", raising=False)
    fixture = Fixture(tmp_path)
    with pytest.raises(CanonicalMispricingAuthorityError, match="HUNTER_APPLICATION_ROOT"):
        CanonicalMispricingService(
            repository=fixture.mispricing_repository,
            valuation_repository=fixture.repository,
            market_fact_repository=ObservedMarketFactRepository(fixture.db_path),
        )


def test_relative_application_root_is_rejected(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    with pytest.raises(CanonicalMispricingAuthorityError, match="absolute"):
        CanonicalMispricingService(
            repository=fixture.mispricing_repository,
            valuation_repository=fixture.repository,
            market_fact_repository=ObservedMarketFactRepository(fixture.db_path),
            application_root=Path("relative/root"),
        )
