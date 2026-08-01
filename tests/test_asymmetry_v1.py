from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hunter.asymmetry import (
    ASYMMETRY_SCHEMA_VERSION,
    AsymmetryAssessmentRecord,
    CanonicalAsymmetryAuthorityError,
    CanonicalAsymmetryIntegrityError,
    CanonicalAsymmetryRepository,
    CanonicalAsymmetryService,
    ScenarioPayoffEstimateRecord,
    ScenarioProbabilityRecord,
    ScenarioSetSnapshot,
)
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
from hunter.value_capture.models import EconomicClaimIdentity

NOW = datetime(2026, 1, 1, tzinfo=UTC)

# The baseline spot price is ingested at NOW; the methodology is recorded/known at
# NOW+1min, the scenario set at NOW+2min, the probabilities at NOW+3min, and the payoff
# estimates at NOW+4min, so strict-known reads for the assessment must use a
# recorded/known time strictly after the last acquisition while keeping effective_at=NOW
# so the scenario set and baseline remain temporally compatible (ADR 0021 fixed horizon
# and strict-known policy).
RECORDED = NOW + timedelta(minutes=5)

ENTITY_ID = "entity:cmp-asymmetry"
ASSET_ID = "asset:cmp-asymmetry"
REPRESENTATION_ID = "representation:cmp-asymmetry-ethereum"
CONTRACT = "0x6666666666666666666666666666666666666666"
LISTING = "cmp-asymmetry-listing"

SCENARIO_IDS = ("s-upside-strong", "s-upside-mild", "s-downside-tail")
SCENARIO_TYPES = ("upside", "upside", "downside_tail")


def identity() -> EconomicClaimIdentity:
    return EconomicClaimIdentity(
        entity_id=ENTITY_ID,
        economic_claim_id="economic-claim:cmp-asymmetry",
        asset_id=ASSET_ID,
        representation_id=REPRESENTATION_ID,
        token_id="cmp-asymmetry",
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


def market_fact_registry() -> MarketFactSourceRegistry:
    # One source authorizes the observed-fact capability the Canonical Asymmetry
    # authority consumes as its strict-known baseline (ADR 0021: providers own observed
    # facts such as spot price; they never assign scenario probabilities, calculate
    # asymmetry, or normalize inputs).
    return MarketFactSourceRegistry.from_mapping(
        {
            "sources": [
                {
                    "source_id": "official-cmp-asymmetry-market-facts",
                    "provider_id": "official-cmp-asymmetry-market-facts-provider",
                    "endpoint_template": "https://example.org/market-facts/{listing_id}",
                    "allowed_hosts": ["example.org"],
                    "parser_version": "official-cmp-asymmetry-market-facts-v1",
                    "enabled": True,
                    "capabilities": ["spot_price"],
                    "quote_currencies": ["usd"],
                    "units": {"spot_price": "usd"},
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
                    "limitations": "test fixture observed market facts only; never asymmetry",
                }
            ]
        }
    )


def seed_spot_price_fact(db_path: Path, *, value: str = "0.01") -> ObservedMarketFactRecord:
    repository = ObservedMarketFactRepository(db_path)
    registry = market_fact_registry()
    service = ObservedMarketFactService(repository, registry)
    request = MarketFactRequest(
        source_id="official-cmp-asymmetry-market-facts",
        provider_id="official-cmp-asymmetry-market-facts-provider",
        identity=market_fact_identity(),
        quote_currency="usd",
        requested_fact_types=("spot_price",),
        requested_at=NOW,
    )
    fact = NormalizedMarketFact(
        fact_type="spot_price",  # type: ignore[arg-type]
        value=value,
        unit="usd",
        quote_currency="usd",
        effective_at=NOW,
        observed_at=NOW,
        confidence="0.9",
    )
    result = MarketFactAcquisitionResult(
        source_id="official-cmp-asymmetry-market-facts",
        provider_id="official-cmp-asymmetry-market-facts-provider",
        endpoint="https://example.org/market-facts/cmp-asymmetry-listing",
        parser_version="official-cmp-asymmetry-market-facts-v1",
        registry_fingerprint=registry.require("official-cmp-asymmetry-market-facts").fingerprint,
        provider_source_record_id=LISTING,
        provider_source_record_version="2026-01-01",
        request=request,
        status="success",
        acquired_at=NOW,
        known_at=NOW,
        raw_payload_hash="sha256:" + "a" * 64,
        facts=(fact,),
    )
    return service.ingest(request, result, recorded_at=NOW)[0]


def scenario_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "identity": identity(),
        "scenario_set_id": "cmp-asymmetry-scenario-set-v1",
        "scenario_ids": SCENARIO_IDS,
        "scenario_types": SCENARIO_TYPES,
        "quote_currency": "usd",
        "material_omitted_scenario_policy": "material omitted scenarios are declared before payoff evaluation",
        "uncertainty": "0.05",
        "evidence_record_ids": ("evidence:cmp-asymmetry:scenario-set-v1",),
        "source_versions": ("1.0.0",),
        "dependency_pairs": (),
        "effective_at": NOW,
        "recorded_at": NOW + timedelta(minutes=2),
        "known_at": NOW + timedelta(minutes=2),
    }
    base.update(overrides)
    return base


class Fixture:
    """Seeds one canonical economic entity with a provider-observed baseline spot price,
    the fixed asymmetry methodology, the predeclared scenario set, the declared
    probabilities, and the declared terminal payoff estimates, then exposes the Canonical
    Asymmetry authority for the verification scenarios."""

    def __init__(self, tmp_path: Path) -> None:
        self.db_path = tmp_path / "asymmetry.sqlite"
        self.spot_price_fact = seed_spot_price_fact(self.db_path)

        self.repository = CanonicalAsymmetryRepository(self.db_path)
        self.service = CanonicalAsymmetryService(
            repository=self.repository,
            market_fact_repository=ObservedMarketFactRepository(self.db_path),
            application_root=tmp_path,
        )
        self.methodology = self.service.persist_asymmetry_methodology(
            methodology_id="cmp-asymmetry-methodology-v1",
            required_quote_currency="usd",
            entity_class_criteria_id="adr-0021-first-entity-class-v1",
            entity_class_criteria_version="1.0.0",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=1),
            known_at=NOW + timedelta(minutes=1),
        )
        self.scenario_set = self.service.persist_scenario_set(
            **scenario_payload(methodology_record_id=self.methodology.record_id),
            baseline_market_fact_record_id=self.spot_price_fact.record_id,
        )
        self.probabilities = {
            "s-upside-strong": self.service.persist_scenario_probability(
                scenario_set_record_id=self.scenario_set.record_id,
                scenario_id="s-upside-strong",
                probability="0.25",
                probability_uncertainty="0.05",
                probability_authority="declared-pre-evaluation",
                probability_method="predeclared-scenario-probability-v1",
                effective_at=NOW,
                recorded_at=NOW + timedelta(minutes=3),
                known_at=NOW + timedelta(minutes=3),
            ),
            "s-upside-mild": self.service.persist_scenario_probability(
                scenario_set_record_id=self.scenario_set.record_id,
                scenario_id="s-upside-mild",
                probability="0.35",
                probability_uncertainty="0.05",
                probability_authority="declared-pre-evaluation",
                probability_method="predeclared-scenario-probability-v1",
                effective_at=NOW,
                recorded_at=NOW + timedelta(minutes=3),
                known_at=NOW + timedelta(minutes=3),
            ),
            "s-downside-tail": self.service.persist_scenario_probability(
                scenario_set_record_id=self.scenario_set.record_id,
                scenario_id="s-downside-tail",
                probability="0.40",
                probability_uncertainty="0.10",
                probability_authority="declared-pre-evaluation",
                probability_method="predeclared-scenario-probability-v1",
                effective_at=NOW,
                recorded_at=NOW + timedelta(minutes=3),
                known_at=NOW + timedelta(minutes=3),
            ),
        }
        self.payoffs = {
            "s-upside-strong": self.service.persist_scenario_payoff(
                scenario_set_record_id=self.scenario_set.record_id,
                scenario_id="s-upside-strong",
                terminal_payoff="0.30",
                payoff_uncertainty="0.05",
                evidence_record_ids=("evidence:cmp-asymmetry:payoff-s-upside-strong",),
                source_record_id="cmp-asymmetry-payoff-s-upside-strong",
                source_record_version="2026-01-01",
                effective_at=NOW,
                recorded_at=NOW + timedelta(minutes=4),
                known_at=NOW + timedelta(minutes=4),
            ),
            "s-upside-mild": self.service.persist_scenario_payoff(
                scenario_set_record_id=self.scenario_set.record_id,
                scenario_id="s-upside-mild",
                terminal_payoff="0.10",
                payoff_uncertainty="0.05",
                evidence_record_ids=("evidence:cmp-asymmetry:payoff-s-upside-mild",),
                source_record_id="cmp-asymmetry-payoff-s-upside-mild",
                source_record_version="2026-01-01",
                effective_at=NOW,
                recorded_at=NOW + timedelta(minutes=4),
                known_at=NOW + timedelta(minutes=4),
            ),
            "s-downside-tail": self.service.persist_scenario_payoff(
                scenario_set_record_id=self.scenario_set.record_id,
                scenario_id="s-downside-tail",
                terminal_payoff="-0.40",
                payoff_uncertainty="0.10",
                evidence_record_ids=("evidence:cmp-asymmetry:payoff-s-downside-tail",),
                source_record_id="cmp-asymmetry-payoff-s-downside-tail",
                source_record_version="2026-01-01",
                effective_at=NOW,
                recorded_at=NOW + timedelta(minutes=4),
                known_at=NOW + timedelta(minutes=4),
            ),
        }

    def assess(self, **overrides: object) -> AsymmetryAssessmentRecord:
        kwargs: dict[str, object] = {
            "identity": identity(),
            "methodology_record_id": self.methodology.record_id,
            "scenario_set_logical_id": self.scenario_set.logical_id,
            "effective_at": NOW,
            "recorded_at": RECORDED,
            "known_at": RECORDED,
        }
        kwargs.update(overrides)
        return self.service.assess(**kwargs)


# 1. Fixed-parameter enforcement ---------------------------------------------------------


def test_asymmetry_methodology_and_assessment_persist_with_fixed_parameters(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    methodology = fixture.methodology
    assert methodology.schema_version == ASYMMETRY_SCHEMA_VERSION
    assert methodology.authorizing_adr_reference == "ADR-0021"
    # ADR 0021-fixed parameters are injected by the service; they are never
    # caller-supplied through the public API.
    assert methodology.raw_formula == "expected_positive_payoff / expected_negative_payoff"
    assert methodology.sign_convention == "higher-more-favorable"
    assert methodology.neutral_ratio == "1"
    assert methodology.raw_range == "[0, +inf)"
    assert methodology.scenario_horizon_days == 365
    assert methodology.required_payoff_unit == "terminal_return"
    assert methodology.baseline_market_observation_fact_type == "spot_price"
    assert methodology.zero_downside_treatment == "insufficient-marker"
    assert methodology.correlation_group == "asymmetry-scenario"
    assert methodology.normalization_policy_id is None
    assert methodology.calibration_policy_id is None

    assessment = fixture.assess()
    assert assessment.schema_version == ASYMMETRY_SCHEMA_VERSION
    assert assessment.correlation_group == "asymmetry-scenario"
    assert assessment.neutral_ratio == "1"
    assert assessment.sign_convention == "higher-more-favorable"
    assert assessment.zero_downside_treatment == "insufficient-marker"


def test_model_rejects_forged_fixed_parameters(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    methodology = fixture.methodology
    with pytest.raises(ValueError, match="raw_formula"):
        replace(methodology, raw_formula="some-other-formula")
    with pytest.raises(ValueError, match="neutral_ratio"):
        replace(methodology, neutral_ratio="2")
    with pytest.raises(ValueError, match="zero_downside_treatment"):
        replace(methodology, zero_downside_treatment="finite-cap")
    with pytest.raises(ValueError, match="correlation_group"):
        replace(methodology, correlation_group="valuation-mispricing")


# 2. Deterministic raw payoff arithmetic (no averaging, no fabricated evidence) --------


def test_assessment_preserves_raw_ratio_and_expected_payoffs(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.assess()

    assert assessment.availability_state == "UNAVAILABLE_UNCALIBRATED_NORMALIZATION"
    assert assessment.normalization_status == "unavailable"
    assert assessment.normalized_value is None
    # The assessment references -- never re-counts -- the exact-version scenario set,
    # scenario probability and payoff records, and baseline market observation
    # (ADR 0021 anti-double-counting).
    assert assessment.scenario_set_record_id == fixture.scenario_set.record_id
    assert assessment.methodology_record_id == fixture.methodology.record_id
    assert assessment.baseline_market_fact_record_id == fixture.spot_price_fact.record_id
    assert tuple(assessment.scenario_probability_record_ids) == tuple(
        fixture.probabilities[scenario_id].record_id for scenario_id in SCENARIO_IDS
    )
    assert tuple(assessment.scenario_payoff_record_ids) == tuple(
        fixture.payoffs[scenario_id].record_id for scenario_id in SCENARIO_IDS
    )

    expected_positive = Decimal("0.25") * Decimal("0.30") + Decimal("0.35") * Decimal("0.10")
    expected_negative = Decimal("0.40") * Decimal("0.40")
    assert assessment.expected_positive_payoff == expected_positive.to_eng_string()
    assert assessment.expected_negative_payoff == expected_negative.to_eng_string()
    # Raw upside is the probability-weighted positive terminal return; raw downside is
    # the absolute probability-weighted negative terminal return; the raw asymmetry ratio
    # is expected_positive / expected_negative (ADR 0021). No averaging of payoffs.
    assert assessment.raw_asymmetry_ratio == (expected_positive / expected_negative).to_eng_string()
    assert Decimal(assessment.raw_asymmetry_ratio or "0") >= 0
    # Tail coverage is the probability mass of the declared downside_tail scenario.
    assert assessment.tail_coverage == "0.40"


def test_confidence_decomposition_and_weakest_component_rule(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.assess()
    components = [
        Decimal(assessment.confidence_scenario_coverage),
        Decimal(assessment.confidence_probability_quality),
        Decimal(assessment.confidence_payoff_uncertainty),
        Decimal(assessment.confidence_tail_coverage),
        Decimal(assessment.confidence_price_quality),
        Decimal(assessment.confidence_dependency),
    ]
    assert all(0 <= component <= 1 for component in components)
    assert Decimal(assessment.confidence) <= min(components)
    # Scenario coverage is complete and no dependency pair is declared, so the weakest
    # component is the tail coverage factor (0.40), which caps the overall confidence.
    assert Decimal(assessment.confidence) == min(components)


# 3. Insert-identical idempotency ---------------------------------------------------------


def test_insert_identical_reproduces_same_record_ids(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    first = fixture.service.persist_asymmetry_methodology(
        methodology_id="cmp-asymmetry-methodology-v1",
        required_quote_currency="usd",
        entity_class_criteria_id="adr-0021-first-entity-class-v1",
        entity_class_criteria_version="1.0.0",
        effective_at=NOW,
        recorded_at=NOW + timedelta(minutes=1),
        known_at=NOW + timedelta(minutes=1),
    )
    assert first.record_id == fixture.methodology.record_id
    assert first.content_hash == fixture.methodology.content_hash

    assessment_first = fixture.assess()
    assessment_second = fixture.assess()
    assert assessment_first.record_id == assessment_second.record_id
    assert assessment_first.content_hash == assessment_second.content_hash
    assert fixture.repository.count("asymmetry_assessments") == 1


# 4. Divergent duplicate rejection ---------------------------------------------------------


def test_divergent_duplicate_root_methodology_is_rejected(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    with pytest.raises(CanonicalAsymmetryIntegrityError, match="root record already exists"):
        fixture.service.persist_asymmetry_methodology(
            methodology_id="cmp-asymmetry-methodology-v1",
            required_quote_currency="usd",
            entity_class_criteria_id="adr-0021-first-entity-class-v1",
            entity_class_criteria_version="2.0.0",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=1),
            known_at=NOW + timedelta(minutes=1),
        )


def test_divergent_duplicate_root_assessment_is_rejected(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.assess()
    with pytest.raises(CanonicalAsymmetryIntegrityError, match="root record already exists"):
        fixture.assess(recorded_at=NOW + timedelta(minutes=6), known_at=NOW + timedelta(minutes=6))


def test_divergent_duplicate_root_scenario_set_is_rejected(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    with pytest.raises(CanonicalAsymmetryIntegrityError, match="root record already exists"):
        fixture.service.persist_scenario_set(
            **scenario_payload(methodology_record_id=fixture.methodology.record_id, uncertainty="0.10"),
            baseline_market_fact_record_id=fixture.spot_price_fact.record_id,
        )


# 5. Immutable records ---------------------------------------------------------------------


def test_records_are_immutable(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.assess()
    with pytest.raises(FrozenInstanceError):
        assessment.expected_positive_payoff = "9"  # type: ignore[misc]
    with pytest.raises(ValueError, match="normalization_status"):
        replace(assessment, normalization_status="available")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="raw asymmetry ratio"):
        replace(assessment, raw_asymmetry_ratio=None)


# 6. Correction lineage ---------------------------------------------------------------------


def test_correction_and_supersession_lineage(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    original = fixture.assess()
    correction = fixture.assess(
        recorded_at=NOW + timedelta(minutes=6),
        known_at=NOW + timedelta(minutes=6),
        supersedes_record_id=original.record_id,
        correction_reason="corrected scenario probability reference",
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
        recorded_at=NOW + timedelta(minutes=6),
        known_at=NOW + timedelta(minutes=6),
        supersedes_record_id=original.record_id,
        correction_reason="first correction",
    )
    with pytest.raises(CanonicalAsymmetryIntegrityError, match="branching correction lineage"):
        fixture.assess(
            recorded_at=NOW + timedelta(minutes=7),
            known_at=NOW + timedelta(minutes=7),
            supersedes_record_id=original.record_id,
            correction_reason="second correction",
        )


def test_correction_requires_later_chronology(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    original = fixture.assess()
    with pytest.raises(CanonicalAsymmetryIntegrityError, match="recorded_at must follow"):
        fixture.assess(
            recorded_at=RECORDED,
            known_at=RECORDED,
            supersedes_record_id=original.record_id,
            correction_reason="invalid chronology",
        )


# 7. Strict-known replay ---------------------------------------------------------------------


def test_strict_known_replay_excludes_future_known_records(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.assess()
    earlier = NOW + timedelta(minutes=4)
    assert (
        fixture.service.strict_known_assessment(effective_as_of=NOW, known_by=earlier, logical_id=assessment.logical_id)
        is None
    )
    later = NOW + timedelta(minutes=6)
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
    assert replay.raw_asymmetry_ratio == assessment.raw_asymmetry_ratio
    assert replay.expected_positive_payoff == assessment.expected_positive_payoff
    assert replay.expected_negative_payoff == assessment.expected_negative_payoff
    assert replay.confidence == assessment.confidence


def test_strict_known_replay_selects_current_not_superseded(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    original = fixture.assess()
    correction = fixture.assess(
        recorded_at=NOW + timedelta(minutes=6),
        known_at=NOW + timedelta(minutes=6),
        supersedes_record_id=original.record_id,
        correction_reason="corrected scenario probability reference",
    )
    replay = fixture.service.strict_known_assessment(
        effective_as_of=NOW,
        known_by=NOW + timedelta(minutes=6),
        logical_id=original.logical_id,
    )
    assert replay is not None
    assert replay.record_id == correction.record_id
    assert replay.record_id != original.record_id


def test_strict_known_replay_before_correction_selects_predecessor(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    original = fixture.assess()
    correction = fixture.assess(
        recorded_at=NOW + timedelta(minutes=6),
        known_at=NOW + timedelta(minutes=6),
        supersedes_record_id=original.record_id,
        correction_reason="corrected scenario probability reference",
    )
    # Replay before the correction's recorded time must select the original: a
    # correction available after a replay cutoff cannot change the earlier selection
    # (ADR 0021 Time, replay, provenance, and lifecycle).
    before = NOW + timedelta(minutes=5, seconds=59)
    replay = fixture.service.strict_known_assessment(
        effective_as_of=NOW,
        known_by=before,
        logical_id=original.logical_id,
    )
    assert replay is not None
    assert replay.record_id == original.record_id
    assert replay.record_id != correction.record_id


def test_strict_known_methodology_replay(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    methodology = fixture.methodology
    earlier = NOW + timedelta(seconds=30)
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


def test_strict_known_scenario_set_probability_and_payoff_replay(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    scenario_set = fixture.scenario_set
    earlier = NOW + timedelta(minutes=1)
    assert (
        fixture.service.strict_known_scenario_set(
            effective_as_of=NOW, known_by=earlier, logical_id=scenario_set.logical_id
        )
        is None
    )
    set_replay = fixture.service.strict_known_scenario_set(
        effective_as_of=NOW, known_by=RECORDED, logical_id=scenario_set.logical_id
    )
    assert set_replay is not None
    assert set_replay.record_id == scenario_set.record_id
    probability = fixture.probabilities["s-downside-tail"]
    probability_replay = fixture.service.strict_known_scenario_probability(
        effective_as_of=NOW, known_by=RECORDED, logical_id=probability.logical_id
    )
    assert probability_replay is not None
    assert probability_replay.record_id == probability.record_id
    payoff = fixture.payoffs["s-downside-tail"]
    payoff_replay = fixture.service.strict_known_scenario_payoff(
        effective_as_of=NOW, known_by=RECORDED, logical_id=payoff.logical_id
    )
    assert payoff_replay is not None
    assert payoff_replay.record_id == payoff.record_id


def test_effective_time_cutoff_respects_scenario_set_and_baseline(tmp_path: Path) -> None:
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


# 8. Explicit missingness ---------------------------------------------------------------------


def test_missing_scenario_set_is_explicit_missingness(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.service.assess(
        identity=identity(),
        methodology_record_id=fixture.methodology.record_id,
        scenario_set_logical_id="missing-scenario-set-logical-id",
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_NO_SCENARIO_SET"
    # Explicit missingness (ADR 0020 "Confidence is unavailable, not zero"): an
    # unavailable assessment must never encode unavailable as a zero confidence.
    assert assessment.confidence == ""
    assert assessment.confidence_scenario_coverage == ""
    assert assessment.confidence_probability_quality == ""
    assert assessment.confidence_payoff_uncertainty == ""
    assert assessment.confidence_tail_coverage == ""
    assert assessment.confidence_price_quality == ""
    assert assessment.confidence_dependency == ""
    assert assessment.normalization_status == "unavailable"
    assert assessment.normalized_value is None
    assert assessment.raw_asymmetry_ratio is None


def test_unavailable_confidence_cannot_be_encoded_as_zero(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    unavailable = fixture.service.assess(
        identity=identity(),
        methodology_record_id=fixture.methodology.record_id,
        scenario_set_logical_id="missing-scenario-set-logical-id",
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    with pytest.raises(ValueError, match="must be blank"):
        replace(unavailable, confidence="0")
    with pytest.raises(ValueError, match="must be blank"):
        replace(unavailable, confidence_tail_coverage="0")


def test_missing_probabilities_are_explicit_missingness(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    scenario_set = fixture.service.persist_scenario_set(
        **scenario_payload(
            identity=identity(),
            methodology_record_id=fixture.methodology.record_id,
            scenario_set_id="cmp-asymmetry-scenario-set-noprob",
        ),
        baseline_market_fact_record_id=fixture.spot_price_fact.record_id,
    )
    # No probability records are persisted for this scenario set.
    assessment = fixture.service.assess(
        identity=identity(),
        methodology_record_id=fixture.methodology.record_id,
        scenario_set_logical_id=scenario_set.logical_id,
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_NO_PROBABILITIES"
    assert assessment.confidence == ""


def test_missing_payoffs_are_explicit_missingness(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    scenario_set = fixture.service.persist_scenario_set(
        **scenario_payload(
            identity=identity(),
            methodology_record_id=fixture.methodology.record_id,
            scenario_set_id="cmp-asymmetry-scenario-set-nopayoff",
        ),
        baseline_market_fact_record_id=fixture.spot_price_fact.record_id,
    )
    for scenario_id, probability in (
        ("s-upside-strong", "0.25"),
        ("s-upside-mild", "0.35"),
        ("s-downside-tail", "0.40"),
    ):
        fixture.service.persist_scenario_probability(
            scenario_set_record_id=scenario_set.record_id,
            scenario_id=scenario_id,
            probability=probability,
            probability_uncertainty="0.05",
            probability_authority="declared-pre-evaluation",
            probability_method="predeclared-scenario-probability-v1",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=3),
            known_at=NOW + timedelta(minutes=3),
        )
    # No payoff records are persisted for this scenario set.
    assessment = fixture.service.assess(
        identity=identity(),
        methodology_record_id=fixture.methodology.record_id,
        scenario_set_logical_id=scenario_set.logical_id,
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_NO_PAYOFFS"
    assert assessment.confidence == ""


def test_incomplete_scenario_coverage_is_explicit_missingness(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    scenario_set = fixture.service.persist_scenario_set(
        **scenario_payload(
            identity=identity(),
            methodology_record_id=fixture.methodology.record_id,
            scenario_set_id="cmp-asymmetry-scenario-set-partial",
        ),
        baseline_market_fact_record_id=fixture.spot_price_fact.record_id,
    )
    # Probability and payoff are persisted for only two of the three declared scenarios.
    for scenario_id, probability in (("s-upside-strong", "0.25"), ("s-upside-mild", "0.35")):
        fixture.service.persist_scenario_probability(
            scenario_set_record_id=scenario_set.record_id,
            scenario_id=scenario_id,
            probability=probability,
            probability_uncertainty="0.05",
            probability_authority="declared-pre-evaluation",
            probability_method="predeclared-scenario-probability-v1",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=3),
            known_at=NOW + timedelta(minutes=3),
        )
    for scenario_id, payoff in (("s-upside-strong", "0.30"), ("s-upside-mild", "0.10")):
        fixture.service.persist_scenario_payoff(
            scenario_set_record_id=scenario_set.record_id,
            scenario_id=scenario_id,
            terminal_payoff=payoff,
            payoff_uncertainty="0.05",
            evidence_record_ids=(f"evidence:cmp-asymmetry:payoff-{scenario_id}",),
            source_record_id=f"cmp-asymmetry-payoff-{scenario_id}",
            source_record_version="2026-01-01",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=4),
            known_at=NOW + timedelta(minutes=4),
        )
    assessment = fixture.service.assess(
        identity=identity(),
        methodology_record_id=fixture.methodology.record_id,
        scenario_set_logical_id=scenario_set.logical_id,
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_INCOMPLETE_SCENARIO_COVERAGE"
    assert assessment.confidence == ""


# 9. Probability policy and material downside -----------------------------------------------


def test_probability_sum_policy_is_enforced(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    scenario_set = fixture.service.persist_scenario_set(
        **scenario_payload(
            identity=identity(),
            methodology_record_id=fixture.methodology.record_id,
            scenario_set_id="cmp-asymmetry-scenario-set-badsum",
        ),
        baseline_market_fact_record_id=fixture.spot_price_fact.record_id,
    )
    for scenario_id, probability in (
        ("s-upside-strong", "0.20"),
        ("s-upside-mild", "0.30"),
        ("s-downside-tail", "0.40"),
    ):
        fixture.service.persist_scenario_probability(
            scenario_set_record_id=scenario_set.record_id,
            scenario_id=scenario_id,
            probability=probability,
            probability_uncertainty="0.05",
            probability_authority="declared-pre-evaluation",
            probability_method="predeclared-scenario-probability-v1",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=3),
            known_at=NOW + timedelta(minutes=3),
        )
    for scenario_id, payoff in (("s-upside-strong", "0.30"), ("s-upside-mild", "0.10"), ("s-downside-tail", "-0.40")):
        fixture.service.persist_scenario_payoff(
            scenario_set_record_id=scenario_set.record_id,
            scenario_id=scenario_id,
            terminal_payoff=payoff,
            payoff_uncertainty="0.05",
            evidence_record_ids=(f"evidence:cmp-asymmetry:payoff-{scenario_id}",),
            source_record_id=f"cmp-asymmetry-payoff-{scenario_id}",
            source_record_version="2026-01-01",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=4),
            known_at=NOW + timedelta(minutes=4),
        )
    # The declared probabilities sum to 0.90, not 1: unsupported under the versioned
    # sum-to-one policy, so the input stays explicitly missing (ADR 0021).
    assessment = fixture.service.assess(
        identity=identity(),
        methodology_record_id=fixture.methodology.record_id,
        scenario_set_logical_id=scenario_set.logical_id,
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_UNSUPPORTED_PROBABILITIES"
    assert assessment.confidence == ""


def test_missing_material_downside_keeps_input_missing(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    scenario_set = fixture.service.persist_scenario_set(
        **scenario_payload(
            identity=identity(),
            methodology_record_id=fixture.methodology.record_id,
            scenario_set_id="cmp-asymmetry-scenario-set-nodownside",
        ),
        baseline_market_fact_record_id=fixture.spot_price_fact.record_id,
    )
    for scenario_id, probability in (
        ("s-upside-strong", "0.25"),
        ("s-upside-mild", "0.35"),
        ("s-downside-tail", "0.40"),
    ):
        fixture.service.persist_scenario_probability(
            scenario_set_record_id=scenario_set.record_id,
            scenario_id=scenario_id,
            probability=probability,
            probability_uncertainty="0.05",
            probability_authority="declared-pre-evaluation",
            probability_method="predeclared-scenario-probability-v1",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=3),
            known_at=NOW + timedelta(minutes=3),
        )
    # All payoffs are non-negative: material downside evidence is missing, so the input
    # stays explicitly missing rather than emitting an infinite ratio (ADR 0021
    # "Zero downside does not imply infinity").
    for scenario_id, payoff in (("s-upside-strong", "0.30"), ("s-upside-mild", "0.10"), ("s-downside-tail", "0.05")):
        fixture.service.persist_scenario_payoff(
            scenario_set_record_id=scenario_set.record_id,
            scenario_id=scenario_id,
            terminal_payoff=payoff,
            payoff_uncertainty="0.05",
            evidence_record_ids=(f"evidence:cmp-asymmetry:payoff-{scenario_id}",),
            source_record_id=f"cmp-asymmetry-payoff-{scenario_id}",
            source_record_version="2026-01-01",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=4),
            known_at=NOW + timedelta(minutes=4),
        )
    assessment = fixture.service.assess(
        identity=identity(),
        methodology_record_id=fixture.methodology.record_id,
        scenario_set_logical_id=scenario_set.logical_id,
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_MISSING_MATERIAL_DOWNSIDE"
    assert assessment.confidence == ""
    assert assessment.raw_asymmetry_ratio is None


# 10. Unavailable and unsupported methodology -------------------------------------------------


def test_unavailable_methodology_remains_explicit(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.service.assess(
        identity=identity(),
        methodology_record_id="nonexistent-methodology-record",
        scenario_set_logical_id=fixture.scenario_set.logical_id,
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
    unsupported = fixture.service.persist_asymmetry_methodology(
        methodology_id="cmp-asymmetry-methodology-unsupported",
        required_quote_currency="usd",
        entity_class_criteria_id="adr-0021-first-entity-class-v1",
        entity_class_criteria_version="1.0.0",
        effective_at=NOW,
        recorded_at=NOW + timedelta(minutes=1),
        known_at=NOW + timedelta(minutes=1),
        formula_version="asymmetry-raw-ratio-v999",
    )
    assert unsupported.formula_version == "asymmetry-raw-ratio-v999"
    assessment = fixture.service.assess(
        identity=identity(),
        methodology_record_id=unsupported.record_id,
        scenario_set_logical_id=fixture.scenario_set.logical_id,
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    # Unsupported methodology stays explicitly unavailable (ADR 0020); it never falls back
    # to a different formula or a fabricated value.
    assert assessment.availability_state == "UNAVAILABLE_UNSUPPORTED_METHODOLOGY"
    assert assessment.raw_asymmetry_ratio is None
    assert assessment.confidence == ""


# 11. Input compatibility gates -----------------------------------------------------------------


def test_identity_mismatch_is_explicit_unavailable(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    assessment = fixture.service.assess(
        identity=other_identity(),
        methodology_record_id=fixture.methodology.record_id,
        scenario_set_logical_id=fixture.scenario_set.logical_id,
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_IDENTITY_MISMATCH"
    assert assessment.confidence == ""


def test_unit_mismatch_is_explicit_unavailable(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    # A second methodology fixes a different required quote currency (eur). The predeclared
    # scenario set is denominated in usd, so applying the eur methodology keeps the input
    # missing under the unit/scope gate (ADR 0021 scope, unit, and compatibility).
    eur_methodology = fixture.service.persist_asymmetry_methodology(
        methodology_id="cmp-asymmetry-methodology-eur",
        required_quote_currency="eur",
        entity_class_criteria_id="adr-0021-first-entity-class-v1",
        entity_class_criteria_version="1.0.0",
        effective_at=NOW,
        recorded_at=NOW + timedelta(minutes=1),
        known_at=NOW + timedelta(minutes=1),
    )
    assessment = fixture.service.assess(
        identity=identity(),
        methodology_record_id=eur_methodology.record_id,
        scenario_set_logical_id=fixture.scenario_set.logical_id,
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_UNIT_MISMATCH"
    assert assessment.confidence == ""


# 12. Deterministic ordering --------------------------------------------------------------------


def test_deterministic_ordering_of_history(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    original = fixture.assess()
    first_correction = fixture.assess(
        recorded_at=NOW + timedelta(minutes=6),
        known_at=NOW + timedelta(minutes=6),
        supersedes_record_id=original.record_id,
        correction_reason="first correction",
    )
    second_correction = fixture.assess(
        recorded_at=NOW + timedelta(minutes=7),
        known_at=NOW + timedelta(minutes=7),
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
    assert [item.record_id for item in fixture.repository.assessment_history(original.logical_id)] == [
        item.record_id for item in history
    ]


def test_deterministic_ordering_of_scenario_probability_history(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    original = fixture.probabilities["s-upside-strong"]
    correction = fixture.service.persist_scenario_probability(
        scenario_set_record_id=fixture.scenario_set.record_id,
        scenario_id="s-upside-strong",
        probability="0.30",
        probability_uncertainty="0.05",
        probability_authority="declared-pre-evaluation",
        probability_method="predeclared-scenario-probability-v1",
        effective_at=NOW,
        recorded_at=NOW + timedelta(minutes=4),
        known_at=NOW + timedelta(minutes=4),
        supersedes_record_id=original.record_id,
        correction_reason="corrected probability",
    )
    history = fixture.service.scenario_probability_history(original.logical_id)
    assert [item.record_id for item in history] == [original.record_id, correction.record_id]


# 13. ADR 0009 repository purity ---------------------------------------------------------------


def test_repository_has_no_replay_selection_or_write_methods(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    # The repository is a purely mechanical persistence adapter (ADR 0009): no write/apply
    # method and no replay selection of any kind.
    for name in (
        "strict_known_methodology",
        "strict_known_scenario_set",
        "strict_known_scenario_probability",
        "strict_known_scenario_payoff",
        "strict_known_assessment",
        "apply",
        "write",
        "assess",
        "persist",
    ):
        assert not hasattr(fixture.repository, name)
    # The service owns every strict-known replay read path.
    assert hasattr(fixture.service, "strict_known_methodology")
    assert hasattr(fixture.service, "strict_known_scenario_set")
    assert hasattr(fixture.service, "strict_known_scenario_probability")
    assert hasattr(fixture.service, "strict_known_scenario_payoff")
    assert hasattr(fixture.service, "strict_known_assessment")


# 14. Provider authority boundary --------------------------------------------------------------


def test_market_provider_observes_facts_and_never_asymmetry(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fact = fixture.spot_price_fact
    # Providers own only observed facts; the spot price carries no asymmetry artifact.
    assert fact.fact_type == "spot_price"
    assert not hasattr(fact, "asymmetry")
    assert not hasattr(fact, "raw_asymmetry_ratio")
    assert not hasattr(fact, "expected_positive_payoff")
    # The assessment is the only asymmetry artifact, produced exclusively by the service.
    fixture.assess()
    assert fixture.repository.count("asymmetry_assessments") == 1


def test_scenario_records_are_hunter_owned_not_provider_owned(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    # Scenario sets, probabilities, and payoff estimates are produced only by the
    # canonical service; no provider registry or acquisition path can create them
    # (ADR 0021: providers never assign scenario probabilities or calculate asymmetry).
    assert isinstance(fixture.scenario_set, ScenarioSetSnapshot)
    assert isinstance(fixture.probabilities["s-downside-tail"], ScenarioProbabilityRecord)
    assert isinstance(fixture.payoffs["s-downside-tail"], ScenarioPayoffEstimateRecord)
    assert fixture.probabilities["s-downside-tail"].probability_authority == "declared-pre-evaluation"
    assert fixture.repository.count("asymmetry_scenario_probabilities") == 3
    assert fixture.repository.count("asymmetry_scenario_payoffs") == 3


def test_prohibited_cross_authority_composition_is_absent(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    # ADR 0021 authority matrix: the asymmetry authority cannot calculate Mispricing,
    # Opportunity, Market Validation scoring, weighting, recommendation, or ranking.
    for attribute in (
        "mispricing",
        "opportunity",
        "rank",
        "recommend",
        "score",
        "portfolio",
        "normalize",
    ):
        assert not hasattr(fixture.service, attribute)


def test_no_fair_value_delta_is_consumed_as_scenario_payoff(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    # ADR 0021 anti-double-counting: the same fair-value delta cannot count as both
    # mispricing upside and scenario upside. The scenario set and payoff estimates carry
    # no fair-value reference field, and the assessment binds only scenario probability
    # and payoff record ids plus the observed baseline fact.
    assessment = fixture.assess()
    assert not hasattr(fixture.scenario_set, "fair_value_record_id")
    assert not hasattr(fixture.payoffs["s-upside-strong"], "fair_value_record_id")
    assert not hasattr(assessment, "fair_value_record_id")
    assert assessment.scenario_probability_record_ids
    assert assessment.scenario_payoff_record_ids


# 15. Authority enforcement --------------------------------------------------------------------


def test_unattested_construction_without_application_root_env_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = Fixture(tmp_path)
    monkeypatch.delenv("HUNTER_APPLICATION_ROOT", raising=False)
    with pytest.raises(CanonicalAsymmetryAuthorityError, match="HUNTER_APPLICATION_ROOT"):
        CanonicalAsymmetryService(
            repository=fixture.repository,
            market_fact_repository=ObservedMarketFactRepository(fixture.db_path),
        )


def test_relative_application_root_is_rejected(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    with pytest.raises(CanonicalAsymmetryAuthorityError, match="absolute"):
        CanonicalAsymmetryService(
            repository=fixture.repository,
            market_fact_repository=ObservedMarketFactRepository(fixture.db_path),
            application_root=Path("relative/root"),
        )
