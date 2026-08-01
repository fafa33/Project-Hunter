from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.comparative_valuation import (
    CanonicalComparativeValuationAuthorityError,
    CanonicalComparativeValuationIntegrityError,
    CanonicalComparativeValuationRepository,
    CanonicalComparativeValuationService,
    ComparativeMetricObservationRecord,
    ComparativeValuationAssessmentRecord,
    PeerCandidate,
    PeerEligibilityDecisionRecord,
    PeerUniversePolicyRecord,
    PeerUniverseSnapshot,
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
from hunter.value_capture.providers import RegisteredValueCaptureProvider, ValueCaptureVerificationKeyRegistry
from hunter.value_capture.registry import ValueCaptureSourceConfig, ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository
from hunter.value_capture.service import SupplyAndValueCaptureService

NOW = datetime(2026, 1, 1, tzinfo=UTC)

# The native evidence chain is ingested at NOW+1min (evidence) and NOW+2min
# (supply/rule), so strict-known reads must use a recorded/known time strictly after
# the last acquisition while keeping effective_at=NOW so the 365-day accounting window
# aligns with the valuation horizon (ADR 0026).
RECORDED = NOW + timedelta(minutes=5)

SIGNING_KEY = b"comparative-valuation-test-key-00001"
SIGNING_KEY_ID = "comparative-valuation-test-key-v1"
ENDPOINT = "https://example.org/tokenomics/cmp"

# Target and peer economic-entity identities. Contract addresses are unique per entity so
# that every identity is a distinct canonical economic entity (ADR 0026 Peer Eligibility).
ENTITY_CONTRACTS = {
    "target": "0x1111111111111111111111111111111111111111",
    "peer-a": "0x2222222222222222222222222222222222222222",
    "peer-b": "0x3333333333333333333333333333333333333333",
    "peer-c": "0x4444444444444444444444444444444444444444",
    "peer-d": "0x5555555555555555555555555555555555555555",
}


def identity(entity: str) -> EconomicClaimIdentity:
    return EconomicClaimIdentity(
        entity_id=f"entity:cmp-{entity}",
        economic_claim_id=f"economic-claim:cmp-{entity}",
        asset_id=f"asset:cmp-{entity}",
        representation_id=f"representation:cmp-{entity}-ethereum",
        token_id=f"cmp-{entity}",
        chain="ethereum",
        contract_address=ENTITY_CONTRACTS[entity],
    )


def market_fact_identity(entity: str) -> MarketFactIdentity:
    return MarketFactIdentity(
        entity_id=f"entity:cmp-{entity}",
        asset_id=f"asset:cmp-{entity}",
        representation_id=f"representation:cmp-{entity}-ethereum",
        chain="ethereum",
        contract_address=ENTITY_CONTRACTS[entity],
        provider_listing_id=f"cmp-{entity}-listing",
    )


def value_capture_source() -> ValueCaptureSourceConfig:
    return ValueCaptureSourceConfig(
        source_id="official-cmp-tokenomics",
        authority_tier="official",
        source_type="official_disclosure",
        allowed_hosts=("example.org",),
        endpoint_patterns=("https://example.org/tokenomics/",),
        parser_version="official-cmp-tokenomics-v1",
        capabilities=(
            "evidence:official_disclosure",
            "supply:fully_diluted_supply",
            "rule:fee_distribution",
        ),
        enabled=True,
    )


def market_fact_registry() -> MarketFactSourceRegistry:
    bindings = [
        {
            "entity_id": f"entity:cmp-{entity}",
            "asset_id": f"asset:cmp-{entity}",
            "representation_id": f"representation:cmp-{entity}-ethereum",
            "chain": "ethereum",
            "contract_address": ENTITY_CONTRACTS[entity],
            "provider_listing_id": f"cmp-{entity}-listing",
        }
        for entity in ENTITY_CONTRACTS
    ]
    return MarketFactSourceRegistry.from_mapping(
        {
            "sources": [
                {
                    "source_id": "official-cmp-market-facts",
                    "provider_id": "official-cmp-market-facts-provider",
                    "endpoint_template": "https://example.org/market-facts/{listing_id}",
                    "allowed_hosts": ["example.org"],
                    "parser_version": "official-cmp-market-facts-v1",
                    "enabled": True,
                    "capabilities": ["fully_diluted_valuation"],
                    "quote_currencies": ["usd"],
                    "units": {"fully_diluted_valuation": "usd"},
                    "supported_entity_scope": ["canonical_asset_representation"],
                    "identity_bindings": bindings,
                    "freshness_seconds": 3600,
                    "observation_confidence": "0.9",
                    "historical_support": "current-only",
                    "limitations": "test fixture fully diluted valuation only",
                }
            ]
        }
    )


def seed_market_fact(db_path: Path, entity: str, value: str) -> ObservedMarketFactRecord:
    registry = market_fact_registry()
    repository = ObservedMarketFactRepository(db_path)
    service = ObservedMarketFactService(repository, registry)
    request = MarketFactRequest(
        source_id="official-cmp-market-facts",
        provider_id="official-cmp-market-facts-provider",
        identity=market_fact_identity(entity),
        quote_currency="usd",
        requested_fact_types=("fully_diluted_valuation",),
        requested_at=NOW,
    )
    fact = NormalizedMarketFact(
        fact_type="fully_diluted_valuation",
        value=value,
        unit="usd",
        quote_currency="usd",
        effective_at=NOW,
        observed_at=NOW,
        confidence="0.9",
    )
    result = MarketFactAcquisitionResult(
        source_id="official-cmp-market-facts",
        provider_id="official-cmp-market-facts-provider",
        endpoint=f"https://example.org/market-facts/cmp-{entity}-listing",
        parser_version="official-cmp-market-facts-v1",
        registry_fingerprint=registry.require("official-cmp-market-facts").fingerprint,
        provider_source_record_id=f"cmp-{entity}-listing",
        provider_source_record_version="2026-01-01",
        request=request,
        status="success",
        acquired_at=NOW,
        known_at=NOW,
        raw_payload_hash="sha256:" + "a" * 64,
        facts=(fact,),
    )
    records = service.ingest(request, result, recorded_at=NOW)
    return records[0]


def evidence_result(
    provider: RegisteredValueCaptureProvider,
    entity: str,
    *,
    amount: str = "1000000",
    acquisition_id: str = "evidence-1",
    **extra: object,
):
    payload = {
        "evidence_type": "official_disclosure",
        "source_reference": f"official-cmp-{entity}-disclosure",
        "extracted_claim": "Protocol fees are distributed under the documented rule.",
        "amount": amount,
        "unit": "usd",
        "accounting_period_start": NOW - timedelta(days=365),
        "accounting_period_end": NOW,
        "attribution_rule_id": "cmp-fee-distribution-v1",
        "source_methodology": "official-accrual-disclosure-v1",
        "source_record_id": f"official-cmp-{entity}-disclosure",
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
        acquired_at=NOW + timedelta(minutes=1),
        identity=identity(entity),
        payload=payload,
    )


def supply_result(
    provider: RegisteredValueCaptureProvider,
    entity: str,
    evidence_id: str,
    market_fact: ObservedMarketFactRecord,
    *,
    diluted: str = "115000000",
    acquisition_id: str = "supply-1",
    **extra: object,
):
    payload = {
        "supply_basis_type": "fully_diluted_supply",
        "quantity": diluted,
        "unit": "native_units",
        "denominator_meaning": "Fully diluted units for the canonical representation.",
        "supply_policy_id": "cmp-token-supply-v1",
        "supply_policy_version": "1.0.0",
        "quantity_components": [
            ["circulating_supply", "86000000"],
            ["total_supply", "100000000"],
            ["fully_diluted_supply", diluted],
        ],
        "observed_market_fact_ids": [market_fact.record_id],
        "observed_market_fact_versions": [market_fact.semantic_version],
        "source_record_id": f"official-cmp-{entity}-supply",
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
        capability="supply:fully_diluted_supply",
        endpoint=ENDPOINT,
        acquisition_id=acquisition_id,
        acquired_at=NOW + timedelta(minutes=2),
        identity=identity(entity),
        payload=payload,
    )


def rule_result(
    provider: RegisteredValueCaptureProvider,
    entity: str,
    evidence_id: str,
    *,
    acquisition_id: str = "rule-1",
    **extra: object,
):
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
        "mechanism_policy_id": "cmp-fee-distribution-v1",
        "mechanism_policy_version": "1.0.0",
        "dilution_treatment": "Distribution entitlement is measured after documented dilution.",
        "claim_seniority": "Pro rata eligible-holder claim after protocol liabilities.",
        "applicability_start": NOW,
        "applicability_end": NOW + timedelta(days=365),
        "limitations": ["No entitlement outside the enacted applicability period."],
        "evidence_record_versions": ["1.0.0"],
        "source_record_id": f"official-cmp-{entity}-fee-rule",
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
        acquired_at=NOW + timedelta(minutes=2),
        identity=identity(entity),
        payload=payload,
    )


def candidate(entity: str) -> PeerCandidate:
    return PeerCandidate(
        identity=identity(entity),
        source_record_id=f"candidate-source-{entity}",
        source_record_version="1.0.0",
        lifecycle_state="active",
        sector_classification="defi",
        value_capture_mechanism_type="fee_distribution",
        revenue_accounting_meaning="protocol_fees",
        native_evidence_type="official_disclosure",
        quote_currency="usd",
        supply_basis="fully_diluted_supply",
    )


def policy_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "policy_id": "cmp-peer-policy-v1",
        "supported_entity_class": "canonical_asset_representation",
        "entity_class_criteria_id": "adr-0026-first-entity-class-v1",
        "entity_class_criteria_version": "1.0.0",
        "taxonomy": "cmp-sector-taxonomy",
        "taxonomy_version": "1.0.0",
        "required_lifecycle_state": "active",
        "required_sector_classification": "defi",
        "required_value_capture_mechanism_types": ("fee_distribution",),
        "required_revenue_accounting_meaning": "protocol_fees",
        "required_native_evidence_types": ("official_disclosure",),
        "required_quote_currency": "usd",
        "freshness_limit_days": 30,
        "observation_window_days": 365,
        "maximum_candidate_universe_size": 20,
        "effective_at": NOW,
        "recorded_at": NOW,
        "known_at": NOW,
    }
    base.update(overrides)
    return base


class Fixture:
    """Seeds one canonical economic entity (target + peers) with the complete native
    evidence chain -- fully-diluted observed market value, value-capture flow evidence,
    fully-diluted supply basis, and value-capture rule -- then exposes the comparative
    valuation service for the Issue #159 verification scenarios."""

    def __init__(self, tmp_path: Path, *, peer_entities: tuple[str, ...] = ("peer-a", "peer-b", "peer-c")) -> None:
        self.db_path = tmp_path / "comparative-valuation.sqlite"
        self.verification_keys = ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY})
        self.vc_repository = SupplyAndValueCaptureRepository(self.db_path)
        self.vc_service = SupplyAndValueCaptureService(
            registry=ValueCaptureSourceRegistry((value_capture_source(),)),
            repository=self.vc_repository,
            verification_keys=self.verification_keys,
        )
        self.provider = RegisteredValueCaptureProvider(
            value_capture_source(), signing_key_id=SIGNING_KEY_ID, signing_key=SIGNING_KEY
        )

        self.fdvs: dict[str, str] = {}
        self.evidence: dict[str, object] = {}
        self.supply: dict[str, object] = {}
        self.rule: dict[str, object] = {}
        for index, entity in enumerate(("target",) + peer_entities, start=1):
            market_fact = seed_market_fact(self.db_path, entity, str(12_000_000 + index * 1_000_000))
            self.fdvs[entity] = market_fact.record_id
            evidence = self.vc_service.ingest_evidence(
                self.provider, evidence_result(self.provider, entity, acquisition_id=f"evidence-{entity}")
            )
            supply = self.vc_service.ingest_supply(
                self.provider,
                supply_result(
                    self.provider,
                    entity,
                    evidence.record_id,
                    market_fact,
                    acquisition_id=f"supply-{entity}",
                ),
            )
            rule = self.vc_service.ingest_rule(
                self.provider, rule_result(self.provider, entity, evidence.record_id, acquisition_id=f"rule-{entity}")
            )
            self.evidence[entity] = evidence
            self.supply[entity] = supply
            self.rule[entity] = rule

        self.repository = CanonicalComparativeValuationRepository(self.db_path)
        self.service = CanonicalComparativeValuationService(
            repository=self.repository,
            value_capture_repository=self.vc_repository,
            market_fact_repository=ObservedMarketFactRepository(self.db_path),
            application_root=tmp_path,
        )

    def persist_policy(self, **overrides: object) -> PeerUniversePolicyRecord:
        return self.service.persist_peer_policy(**policy_payload(**overrides))

    def persist_universe(
        self,
        *,
        policy: PeerUniversePolicyRecord,
        entities: tuple[str, ...] | None = None,
    ) -> PeerUniverseSnapshot:
        peer_entities = tuple(
            entity
            for entity in (entities if entities is not None else ("peer-a", "peer-b", "peer-c"))
            if entity != "target"
        )
        return self.service.persist_peer_universe(
            identity=identity("target"),
            policy_record_id=policy.record_id,
            cutoff=NOW,
            candidates=tuple(candidate(entity) for entity in peer_entities),
            recorded_at=RECORDED,
            known_at=RECORDED,
        )

    def persist_decision(self, *, universe: PeerUniverseSnapshot, entity: str) -> PeerEligibilityDecisionRecord:
        return self.service.persist_eligibility_decision(
            target_identity=identity("target"),
            candidate_identity=identity(entity),
            policy_record_id=universe.policy_record_id,
            universe_snapshot_id=universe.record_id,
            effective_at=NOW,
            recorded_at=RECORDED,
            known_at=RECORDED,
        )

    def persist_observation(self, *, universe: PeerUniverseSnapshot, entity: str) -> ComparativeMetricObservationRecord:
        return self.service.persist_metric_observation(
            identity=identity(entity),
            policy_record_id=universe.policy_record_id,
            universe_snapshot_id=universe.record_id,
            evidence_type="official_disclosure",
            rule_type="fee_distribution",
            effective_at=NOW,
            recorded_at=RECORDED,
            known_at=RECORDED,
        )

    def build_complete(
        self, *, universe: PeerUniverseSnapshot
    ) -> tuple[tuple[PeerEligibilityDecisionRecord, ...], tuple[ComparativeMetricObservationRecord, ...]]:
        decisions = tuple(
            self.persist_decision(universe=universe, entity=entity) for entity in ("peer-a", "peer-b", "peer-c")
        )
        observations = tuple(
            self.persist_observation(universe=universe, entity=entity)
            for entity in ("target", "peer-a", "peer-b", "peer-c")
        )
        return decisions, observations

    def assess_complete(self, *, universe: PeerUniverseSnapshot) -> ComparativeValuationAssessmentRecord:
        self.build_complete(universe=universe)
        return self.service.assess(
            identity=identity("target"),
            policy_record_id=universe.policy_record_id,
            universe_snapshot_id=universe.record_id,
            evidence_type="official_disclosure",
            rule_type="fee_distribution",
            effective_at=NOW,
            recorded_at=RECORDED,
            known_at=RECORDED,
        )


# ---------------------------------------------------------------- valid evaluation


def test_complete_evaluation_persists_raw_median_and_residual(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    assessment = fixture.assess_complete(universe=universe)

    assert assessment.identity == identity("target")
    assert assessment.universe_snapshot_id == universe.record_id
    assert assessment.policy_record_id == policy.record_id
    assert len(assessment.included_decision_record_ids) == 3
    assert len(assessment.peer_observation_record_ids) == 3
    # Complete ordered peer distribution with equal weighting; the unweighted median is
    # the middle ordered value for an odd cohort.
    distribution = [float(value) for value in assessment.peer_multiple_distribution]
    assert distribution == sorted(distribution)
    assert len(distribution) == 3
    expected_median = sorted(distribution)[1]
    assert abs(float(assessment.peer_median_multiple or "0") - expected_median) < 1e-9
    # Raw signed residual = ln(median / target). AVAILABLE is never emitted without a
    # calibrated normalization; the raw values are preserved.
    assert assessment.availability_state == "UNAVAILABLE_UNCALIBRATED_NORMALIZATION"
    assert assessment.normalization_status == "unavailable"
    assert assessment.normalized_value is None
    assert assessment.target_multiple is not None
    assert assessment.raw_log_residual is not None
    assert assessment.correlation_group == "valuation-mispricing"
    # A positive residual means the target is cheaper than the peer reference.
    if float(assessment.target_multiple or "1") < float(assessment.peer_median_multiple or "0"):
        assert float(assessment.raw_log_residual or "0") > 0


def test_even_cohort_median_is_mean_of_two_central_values(tmp_path) -> None:
    fixture = Fixture(tmp_path, peer_entities=("peer-a", "peer-b", "peer-c", "peer-d"))
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy, entities=("peer-a", "peer-b", "peer-c", "peer-d"))
    for entity in ("peer-a", "peer-b", "peer-c", "peer-d"):
        fixture.persist_decision(universe=universe, entity=entity)
    for entity in ("target", "peer-a", "peer-b", "peer-c", "peer-d"):
        fixture.persist_observation(universe=universe, entity=entity)
    assessment = fixture.service.assess(
        identity=identity("target"),
        policy_record_id=universe.policy_record_id,
        universe_snapshot_id=universe.record_id,
        evidence_type="official_disclosure",
        rule_type="fee_distribution",
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    distribution = sorted(float(value) for value in assessment.peer_multiple_distribution)
    expected_median = (distribution[1] + distribution[2]) / 2
    assert abs(float(assessment.peer_median_multiple or "0") - expected_median) < 1e-9


# ------------------------------------------------------ incomplete decision coverage


def test_incomplete_decision_coverage_is_unavailable(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    # Only two of the three candidates receive an eligibility decision.
    fixture.persist_decision(universe=universe, entity="peer-a")
    fixture.persist_decision(universe=universe, entity="peer-b")
    assessment = fixture.service.assess(
        identity=identity("target"),
        policy_record_id=universe.policy_record_id,
        universe_snapshot_id=universe.record_id,
        evidence_type="official_disclosure",
        rule_type="fee_distribution",
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_INSUFFICIENT_ELIGIBLE_PEERS"
    assert assessment.peer_median_multiple is None
    assert assessment.raw_log_residual is None


def test_indeterminate_decision_fails_coverage_gate(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    # peer-d is intentionally NOT seeded with native evidence, so its eligibility
    # decision is indeterminate: missing evidence is not a pass (ADR 0026).
    universe = fixture.persist_universe(policy=policy, entities=("peer-a", "peer-b", "peer-c", "peer-d"))
    for entity in ("peer-a", "peer-b", "peer-c", "peer-d"):
        decision = fixture.persist_decision(universe=universe, entity=entity)
        assert (decision.decision == "indeterminate") == (entity == "peer-d")
    assessment = fixture.service.assess(
        identity=identity("target"),
        policy_record_id=universe.policy_record_id,
        universe_snapshot_id=universe.record_id,
        evidence_type="official_disclosure",
        rule_type="fee_distribution",
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_INSUFFICIENT_ELIGIBLE_PEERS"


def test_fewer_than_minimum_eligible_peers_is_unavailable(tmp_path) -> None:
    fixture = Fixture(tmp_path, peer_entities=("peer-a", "peer-b"))
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy, entities=("peer-a", "peer-b"))
    for entity in ("peer-a", "peer-b"):
        fixture.persist_decision(universe=universe, entity=entity)
    for entity in ("target", "peer-a", "peer-b"):
        fixture.persist_observation(universe=universe, entity=entity)
    assessment = fixture.service.assess(
        identity=identity("target"),
        policy_record_id=universe.policy_record_id,
        universe_snapshot_id=universe.record_id,
        evidence_type="official_disclosure",
        rule_type="fee_distribution",
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_INSUFFICIENT_ELIGIBLE_PEERS"
    assert assessment.raw_log_residual is None


def test_empty_candidate_universe_is_unavailable(tmp_path) -> None:
    fixture = Fixture(tmp_path, peer_entities=())
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy, entities=())
    assessment = fixture.service.assess(
        identity=identity("target"),
        policy_record_id=universe.policy_record_id,
        universe_snapshot_id=universe.record_id,
        evidence_type="official_disclosure",
        rule_type="fee_distribution",
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_NO_CANDIDATES"


def test_missing_target_observation_is_unavailable(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    for entity in ("peer-a", "peer-b", "peer-c"):
        fixture.persist_decision(universe=universe, entity=entity)
    for entity in ("peer-a", "peer-b", "peer-c"):
        fixture.persist_observation(universe=universe, entity=entity)
    assessment = fixture.service.assess(
        identity=identity("target"),
        policy_record_id=universe.policy_record_id,
        universe_snapshot_id=universe.record_id,
        evidence_type="official_disclosure",
        rule_type="fee_distribution",
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_INCOMPATIBLE_COORDINATES"


# ------------------------------------------------------ peer-policy version binding


def test_universe_binds_policy_version_and_rejects_mismatch(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    assert universe.policy_record_id == policy.record_id
    assert universe.policy_record_version == policy.semantic_version

    # A second, distinct peer policy exists; a decision that references it while
    # pointing at a universe snapshot bound to the first policy must be rejected as a
    # version mismatch, not as a missing policy.
    other_policy = fixture.persist_policy(policy_id="cmp-peer-policy-v2")
    assert other_policy.record_id != policy.record_id

    with pytest.raises(CanonicalComparativeValuationAuthorityError, match="does not bind the referenced policy"):
        fixture.service.persist_eligibility_decision(
            target_identity=identity("target"),
            candidate_identity=identity("peer-a"),
            policy_record_id=other_policy.record_id,
            universe_snapshot_id=universe.record_id,
            effective_at=NOW,
            recorded_at=NOW,
            known_at=NOW,
        )


def test_candidate_not_in_universe_is_rejected(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    with pytest.raises(CanonicalComparativeValuationAuthorityError, match="not a member"):
        fixture.service.persist_eligibility_decision(
            target_identity=identity("target"),
            candidate_identity=identity("peer-d"),
            policy_record_id=universe.policy_record_id,
            universe_snapshot_id=universe.record_id,
            effective_at=NOW,
            recorded_at=NOW,
            known_at=NOW,
        )


def test_policy_not_strict_known_at_cutoff_is_rejected(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy(
        effective_at=NOW, recorded_at=NOW + timedelta(days=5), known_at=NOW + timedelta(days=5)
    )
    with pytest.raises(CanonicalComparativeValuationAuthorityError, match="not strict-known"):
        fixture.service.persist_eligibility_decision(
            target_identity=identity("target"),
            candidate_identity=identity("peer-a"),
            policy_record_id=policy.record_id,
            universe_snapshot_id="irrelevant",
            effective_at=NOW,
            recorded_at=NOW,
            known_at=NOW,
        )


# ------------------------------------------------------------- point-in-time eligibility


def test_point_in_time_peer_eligibility_is_persisted(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    decision = fixture.persist_decision(universe=universe, entity="peer-a")
    assert decision.universe_snapshot_id == universe.record_id
    assert decision.target_identity == identity("target")
    assert decision.candidate_identity == identity("peer-a")
    assert decision.decision == "included"
    assert decision.dimension_decisions
    assert decision.evidence_record_id
    assert decision.market_fact_record_id
    assert decision.rule_record_id
    assert decision.supply_record_id
    assert decision.confidence_component
    loaded = fixture.service.get_eligibility_decision(decision.record_id)
    assert loaded == decision


# ------------------------------------------------------- equal weighting and median


def test_equal_weighting_keeps_every_eligible_observation(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    _, observations = fixture.build_complete(universe=universe)
    peers = [item for item in observations if item.identity.representation_id != identity("target").representation_id]
    assert len(peers) == 3
    # Every eligible peer observation contributes to the distribution without trimming.
    assessment = fixture.service.assess(
        identity=identity("target"),
        policy_record_id=universe.policy_record_id,
        universe_snapshot_id=universe.record_id,
        evidence_type="official_disclosure",
        rule_type="fee_distribution",
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert len(assessment.peer_multiple_distribution) == 3


# ------------------------------------------------------------- insert-identical idempotency


def test_insert_identical_reproduces_same_record_ids(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    first = fixture.persist_policy()
    second = fixture.persist_policy()
    assert first.record_id == second.record_id
    assert first.content_hash == second.content_hash

    universe_first = fixture.persist_universe(policy=first)
    universe_second = fixture.persist_universe(policy=first)
    assert universe_first.record_id == universe_second.record_id

    decision_first = fixture.persist_decision(universe=universe_first, entity="peer-a")
    decision_second = fixture.persist_decision(universe=universe_first, entity="peer-a")
    assert decision_first.record_id == decision_second.record_id


def test_divergent_duplicate_is_rejected(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    fixture.persist_policy()
    with pytest.raises(CanonicalComparativeValuationIntegrityError, match="root record already exists"):
        fixture.persist_policy(required_sector_classification="layer1")


# ------------------------------------------------------------- correction lineage


def test_correction_and_supersession_lineage(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    original = fixture.persist_decision(universe=universe, entity="peer-a")
    correction = fixture.service.persist_eligibility_decision(
        target_identity=identity("target"),
        candidate_identity=identity("peer-a"),
        policy_record_id=universe.policy_record_id,
        universe_snapshot_id=universe.record_id,
        effective_at=NOW,
        recorded_at=NOW + timedelta(minutes=10),
        known_at=NOW + timedelta(minutes=10),
        supersedes_record_id=original.record_id,
        correction_reason="corrected eligibility evidence reference",
    )
    assert correction.supersedes_record_id == original.record_id
    assert correction.correction_reason
    assert correction.logical_id == original.logical_id
    history = fixture.service.decision_history(original.logical_id)
    assert [item.record_id for item in history] == [original.record_id, correction.record_id]


def test_branching_correction_is_prohibited(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    original = fixture.persist_decision(universe=universe, entity="peer-a")
    fixture.service.persist_eligibility_decision(
        target_identity=identity("target"),
        candidate_identity=identity("peer-a"),
        policy_record_id=universe.policy_record_id,
        universe_snapshot_id=universe.record_id,
        effective_at=NOW,
        recorded_at=NOW + timedelta(minutes=10),
        known_at=NOW + timedelta(minutes=10),
        supersedes_record_id=original.record_id,
        correction_reason="first correction",
    )
    with pytest.raises(CanonicalComparativeValuationIntegrityError, match="branching correction lineage"):
        fixture.service.persist_eligibility_decision(
            target_identity=identity("target"),
            candidate_identity=identity("peer-a"),
            policy_record_id=universe.policy_record_id,
            universe_snapshot_id=universe.record_id,
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=20),
            known_at=NOW + timedelta(minutes=20),
            supersedes_record_id=original.record_id,
            correction_reason="second correction",
        )


def test_correction_requires_later_chronology(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    original = fixture.persist_decision(universe=universe, entity="peer-a")
    with pytest.raises(CanonicalComparativeValuationIntegrityError, match="recorded_at must follow"):
        fixture.service.persist_eligibility_decision(
            target_identity=identity("target"),
            candidate_identity=identity("peer-a"),
            policy_record_id=universe.policy_record_id,
            universe_snapshot_id=universe.record_id,
            effective_at=NOW,
            recorded_at=NOW,
            known_at=NOW,
            supersedes_record_id=original.record_id,
            correction_reason="invalid chronology",
        )


# ------------------------------------------------------------- strict-known replay


def test_strict_known_replay_excludes_future_known_records(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    assessment = fixture.assess_complete(universe=universe)
    logical_id = assessment.logical_id
    # The assessment was recorded/known at NOW; replay at an earlier cutoff must not see it.
    earlier = NOW - timedelta(days=1)
    assert (
        fixture.service.strict_known_assessment(effective_as_of=earlier, known_by=earlier, logical_id=logical_id)
        is None
    )
    later = NOW + timedelta(days=1)
    replay = fixture.service.strict_known_assessment(effective_as_of=later, known_by=later, logical_id=logical_id)
    assert replay is not None
    assert replay.record_id == assessment.record_id


def test_strict_known_replay_reproduces_identical_assessment(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    assessment = fixture.assess_complete(universe=universe)
    # The assessment is recorded/known at RECORDED, so strict-known replay at the same
    # cutoff reproduces the identical persisted record; replay before RECORDED must not
    # see it (covered by the future-known exclusion test).
    replay = fixture.service.strict_known_assessment(
        effective_as_of=RECORDED, known_by=RECORDED, logical_id=assessment.logical_id
    )
    assert replay is not None
    assert replay.record_id == assessment.record_id
    assert replay.peer_multiple_distribution == assessment.peer_multiple_distribution
    assert replay.peer_median_multiple == assessment.peer_median_multiple
    assert replay.raw_log_residual == assessment.raw_log_residual


# ------------------------------------------------------------- deterministic ordering


def test_deterministic_ordering_of_decisions_and_observations(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    # Persist decisions in a shuffled insertion order; reads must be deterministic.
    for entity in ("peer-c", "peer-a", "peer-b"):
        fixture.persist_decision(universe=universe, entity=entity)
    assert len(universe.candidates) == 3
    repository_decisions = fixture.repository.decisions_for_universe(universe.record_id)
    keys = [
        (item.candidate_identity.entity_id, item.candidate_identity.representation_id) for item in repository_decisions
    ]
    assert keys == sorted(keys)


# ------------------------------------------------------------- explicit missingness/confidence


def test_unavailable_assessment_persists_explicit_missingness(tmp_path) -> None:
    fixture = Fixture(tmp_path, peer_entities=("peer-a", "peer-b"))
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy, entities=("peer-a", "peer-b"))
    for entity in ("peer-a", "peer-b"):
        fixture.persist_decision(universe=universe, entity=entity)
    for entity in ("target", "peer-a", "peer-b"):
        fixture.persist_observation(universe=universe, entity=entity)
    assessment = fixture.service.assess(
        identity=identity("target"),
        policy_record_id=universe.policy_record_id,
        universe_snapshot_id=universe.record_id,
        evidence_type="official_disclosure",
        rule_type="fee_distribution",
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    assert assessment.availability_state == "UNAVAILABLE_INSUFFICIENT_ELIGIBLE_PEERS"
    assert assessment.confidence == "0"
    assert assessment.normalization_status == "unavailable"
    assert assessment.normalized_value is None


def test_confidence_decomposition_and_weakest_component_rule(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    assessment = fixture.assess_complete(universe=universe)
    components = [
        float(assessment.confidence_universe_coverage),
        float(assessment.confidence_eligibility_evidence),
        float(assessment.confidence_metric_evidence),
        float(assessment.confidence_coordinate_compatibility),
        float(assessment.confidence_peer_set_cardinality),
        float(assessment.confidence_methodology_calibration),
    ]
    assert all(0 <= component <= 1 for component in components)
    # No calibrated normalization has been accepted, so methodology-calibration
    # confidence is zero and overall confidence cannot exceed the weakest component.
    assert assessment.confidence_methodology_calibration == "0"
    assert float(assessment.confidence) <= min(components)
    assert float(assessment.confidence) == 0


# ------------------------------------------------------------- unresolved conflicts


def test_unresolved_conflict_queries_are_available(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    policy = fixture.persist_policy()
    universe = fixture.persist_universe(policy=policy)
    fixture.assess_complete(universe=universe)
    assert fixture.service.unresolved_policy_conflicts() == ()
    assert fixture.service.unresolved_universe_conflicts() == ()
    assert fixture.service.unresolved_decision_conflicts() == ()
    assert fixture.service.unresolved_observation_conflicts() == ()
    assert fixture.service.unresolved_assessment_conflicts() == ()


# ------------------------------------------------------------- prohibited composition


def test_prohibited_cross_authority_composition_is_absent(tmp_path) -> None:
    fixture = Fixture(tmp_path)
    # ADR 0026 Prohibited Comparisons: the service must not expose Mispricing, Asymmetry,
    # Opportunity, ranking, or recommendation capabilities.
    for attribute in (
        "mispricing",
        "asymmetry",
        "opportunity",
        "rank",
        "recommend",
        "score",
        "portfolio",
    ):
        assert not hasattr(fixture.service, attribute)
    assert not hasattr(fixture.service, "normalize")
