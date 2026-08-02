from __future__ import annotations

import inspect
from datetime import timedelta

from test_asymmetry_v1 import NOW as ASYMMETRY_NOW
from test_asymmetry_v1 import RECORDED as ASYMMETRY_RECORDED
from test_asymmetry_v1 import Fixture as AsymmetryFixture
from test_comparative_valuation_v1 import NOW as COMPARATIVE_NOW
from test_comparative_valuation_v1 import RECORDED as COMPARATIVE_RECORDED
from test_comparative_valuation_v1 import Fixture as ComparativeFixture
from test_mispricing_v1 import NOW as MISPRICING_NOW
from test_mispricing_v1 import RECORDED as MISPRICING_RECORDED
from test_mispricing_v1 import Fixture as MispricingFixture
from test_mispricing_v1 import identity as mispricing_identity

from hunter.asymmetry.service import CanonicalAsymmetryService
from hunter.comparative_valuation.service import CanonicalComparativeValuationService
from hunter.mispricing.service import CanonicalMispricingService
from hunter.valuation.service import CanonicalValuationService


def test_complete_valuation_family_lineage_replay_and_isolation(tmp_path) -> None:
    mispricing = MispricingFixture(tmp_path / "valuation-mispricing")
    original_mispricing = mispricing.assess()

    assert original_mispricing.fair_value_record_id == mispricing.estimate.record_id
    assert original_mispricing.fair_value_record_version == mispricing.estimate.semantic_version
    assert original_mispricing.market_fact_record_id == mispricing.spot_price_fact.record_id
    assert original_mispricing.correlation_group == mispricing.valuation_assessment.correlation_group
    assert original_mispricing.correlation_group == "valuation-mispricing"

    correction_time = MISPRICING_RECORDED + timedelta(minutes=10)
    corrected_estimate, corrected_valuation = mispricing.valuation_service.estimate_fair_value(
        identity=mispricing_identity(),
        evidence_type="official_disclosure",
        rule_type="fee_distribution",
        effective_at=MISPRICING_NOW,
        recorded_at=correction_time,
        known_at=correction_time,
        supersedes_estimate_record_id=mispricing.estimate.record_id,
        supersedes_assessment_record_id=mispricing.valuation_assessment.record_id,
        correction_reason="cross-family replay correction",
    )
    corrected_mispricing = mispricing.service.assess(
        identity=mispricing_identity(),
        methodology_record_id=mispricing.mispricing_methodology.record_id,
        fair_value_logical_id=mispricing.estimate.logical_id,
        effective_at=MISPRICING_NOW,
        recorded_at=correction_time + timedelta(minutes=1),
        known_at=correction_time + timedelta(minutes=1),
        supersedes_record_id=original_mispricing.record_id,
        correction_reason="propagate exact fair-value correction",
    )

    assert corrected_estimate.logical_id == mispricing.estimate.logical_id
    assert corrected_valuation.logical_id == mispricing.valuation_assessment.logical_id
    assert corrected_mispricing.logical_id == original_mispricing.logical_id
    assert corrected_mispricing.fair_value_record_id == corrected_estimate.record_id
    assert (
        mispricing.service.strict_known_assessment(
            effective_as_of=MISPRICING_NOW, known_by=MISPRICING_RECORDED, logical_id=original_mispricing.logical_id
        )
        == original_mispricing
    )
    assert (
        mispricing.service.strict_known_assessment(
            effective_as_of=MISPRICING_NOW,
            known_by=correction_time + timedelta(minutes=1),
            logical_id=original_mispricing.logical_id,
        )
        == corrected_mispricing
    )

    comparative = ComparativeFixture(tmp_path / "comparative")
    policy = comparative.persist_policy()
    ordered = comparative.persist_universe(policy=policy, entities=("peer-a", "peer-b", "peer-c"))
    permuted = comparative.persist_universe(policy=policy, entities=("peer-c", "peer-a", "peer-b"))
    assert ordered.record_id == permuted.record_id
    assert ordered.candidates == permuted.candidates
    comparative_assessment = comparative.assess_complete(universe=ordered)
    replayed_comparative = comparative.service.strict_known_assessment(
        effective_as_of=COMPARATIVE_NOW,
        known_by=COMPARATIVE_RECORDED,
        logical_id=comparative_assessment.logical_id,
    )
    assert replayed_comparative == comparative_assessment
    assert comparative_assessment.correlation_group == "valuation-mispricing"
    assert comparative_assessment.target_observation_record_id
    assert tuple(comparative_assessment.peer_observation_record_ids) == tuple(
        sorted(
            comparative_assessment.peer_observation_record_ids,
            key=lambda record_id: comparative.repository.get_metric_observation(record_id).identity.entity_id,  # type: ignore[union-attr]
        )
    )

    asymmetry = AsymmetryFixture(tmp_path / "asymmetry")
    original_asymmetry = asymmetry.assess()
    corrected_payoff = asymmetry.service.persist_scenario_payoff(
        scenario_set_record_id=asymmetry.scenario_set.record_id,
        scenario_id="s-upside-strong",
        terminal_payoff="0.35",
        payoff_uncertainty="0.05",
        evidence_record_ids=("evidence:cmp-asymmetry:payoff-s-upside-strong-correction",),
        source_record_id="cmp-asymmetry-payoff-s-upside-strong",
        source_record_version="2026-01-02",
        effective_at=ASYMMETRY_NOW,
        recorded_at=ASYMMETRY_RECORDED + timedelta(minutes=10),
        known_at=ASYMMETRY_RECORDED + timedelta(minutes=10),
        supersedes_record_id=asymmetry.payoffs["s-upside-strong"].record_id,
        correction_reason="cross-family scenario payoff correction",
    )
    corrected_asymmetry = asymmetry.service.assess(
        methodology_record_id=asymmetry.methodology.record_id,
        scenario_set_logical_id=asymmetry.scenario_set.logical_id,
        identity=asymmetry.scenario_set.identity,
        effective_at=ASYMMETRY_NOW,
        recorded_at=ASYMMETRY_RECORDED + timedelta(minutes=11),
        known_at=ASYMMETRY_RECORDED + timedelta(minutes=11),
        supersedes_record_id=original_asymmetry.record_id,
        correction_reason="propagate exact payoff correction",
    )
    assert corrected_payoff.logical_id == asymmetry.payoffs["s-upside-strong"].logical_id
    assert corrected_asymmetry.logical_id == original_asymmetry.logical_id
    assert corrected_payoff.record_id in corrected_asymmetry.scenario_payoff_record_ids
    assert asymmetry.payoffs["s-upside-strong"].record_id not in corrected_asymmetry.scenario_payoff_record_ids
    assert original_asymmetry.correlation_group == corrected_asymmetry.correlation_group == "asymmetry-scenario"
    assert not hasattr(corrected_asymmetry, "fair_value_record_id")
    assert not hasattr(corrected_asymmetry, "mispricing_record_id")

    for service_type in (
        CanonicalValuationService,
        CanonicalComparativeValuationService,
        CanonicalMispricingService,
        CanonicalAsymmetryService,
    ):
        source = inspect.getsource(service_type).lower()
        assert "market_validation" not in source
        for prohibited in ("opportunity", "ranking", "portfolio"):
            assert not hasattr(service_type, prohibited)
