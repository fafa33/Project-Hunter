"""ADR 0022 Milestone 4: leakage detection and calibration-harness replay tests.

This suite proves -- rather than asserts -- that `CanonicalValuationService` (Milestone
3, hardened by PR #124) and the new `ValuationCalibrationHarness`
(`hunter.historical.valuation_calibration`) are free of temporal leakage across every
input family ADR 0022 authorizes, and that historical replay through the harness is
deterministic and reproducible.

Fixture helpers are imported from `test_valuation_v1` rather than duplicated, so the two
suites never drift out of sync on what a valid evidence/supply/rule/methodology payload
looks like.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from test_valuation_v1 import (
    NOW,
    SIGNING_KEY,
    SIGNING_KEY_ID,
    evidence_result,
    identity,
    market_fact_registry,
    methodology_payload,
    rule_result,
    seed_observed_market_fact,
    setup,
    source,
    supply_result,
)

from hunter.historical.cutoff import timestamp_valid_at
from hunter.historical.valuation_calibration import (
    ValuationCalibrationCase,
    ValuationCalibrationHarness,
    ValuationCalibrationLeakageError,
    ValuationCalibrationReconstructionError,
    _cross_check_lineage,
    _verify_no_leakage,
    content_hash,
)
from hunter.market_facts.models import (
    MarketFactAcquisitionResult,
    MarketFactIdentity,
    MarketFactRequest,
    NormalizedMarketFact,
)
from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.market_facts.service import ObservedMarketFactService
from hunter.valuation.repository import CanonicalValuationRepository
from hunter.valuation.service import CanonicalValuationAuthorityError, CanonicalValuationService
from hunter.valuation_methodology.repository import ValuationMethodologyIntegrityError, ValuationMethodologyRepository
from hunter.valuation_methodology.service import CanonicalValuationMethodologyAuthority
from hunter.value_capture.providers import RegisteredValueCaptureProvider, ValueCaptureVerificationKeyRegistry
from hunter.value_capture.registry import ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository
from hunter.value_capture.service import SupplyAndValueCaptureAuthorityError, SupplyAndValueCaptureService

CASE_ID = "canonical-test-case-1"


def harness_for(fixture) -> ValuationCalibrationHarness:
    return ValuationCalibrationHarness(fixture.service)


class ManualService:
    """A from-scratch construction mirroring `test_valuation_v1.Fixture`, but without the
    fixture's own default evidence/supply/rule ingestion -- used where a test needs sole
    control over exactly one input family (e.g. the *only* rule for a category is
    conflicted, with no valid fallback record of the same category already present)."""

    def __init__(self, tmp_path: Path) -> None:
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
        self.vm_repository = ValuationMethodologyRepository(self.db_path)
        self.methodology_authority = CanonicalValuationMethodologyAuthority(
            repository=self.vm_repository, application_root=tmp_path
        )
        self.methodology = self.methodology_authority.persist_methodology(**methodology_payload())
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


def case_for(
    fixture, logical_id: str, *, effective_as_of: datetime = NOW, known_by: datetime
) -> ValuationCalibrationCase:
    return ValuationCalibrationCase(
        case_id=CASE_ID,
        identity=identity(),
        evidence_type="official_disclosure",
        rule_type="fee_distribution",
        logical_id=logical_id,
        effective_as_of=effective_as_of,
        known_by=known_by,
    )


# 1. Temporal leakage -------------------------------------------------------------------
# One test per input family named in the task: evidence, corrections, methodology
# versions, supply basis, value-capture rules, market facts. Each proves the future
# version is deterministically rejected -- never silently skipped, averaged, or repaired.


def test_no_future_evidence_leaks_into_estimate(tmp_path: Path) -> None:
    # A future-recorded/known input is strict-known *eligible* whenever it falls at or
    # before the query's known_by cutoff -- here the estimate's own declared known_at
    # (NOW+30min) -- and, being the most recent, is exactly the one strict-known
    # selection would otherwise pick. This is the precise gap PR #124 closed: the input's
    # own recorded_at (NOW+20min) still exceeds the estimate's separately-declared
    # recorded_at (NOW+10min), so it must fail closed rather than silently be selected.
    fixture = setup(tmp_path)
    future_evidence = fixture.vc_service.ingest_evidence(
        fixture.provider,
        evidence_result(
            fixture.provider,
            acquisition_id="evidence-future",
            acquired_at=NOW + timedelta(minutes=20),
            source_reference="official-tokenomics-page-future",
        ),
    )
    assert future_evidence.recorded_at == NOW + timedelta(minutes=20)
    with pytest.raises(CanonicalValuationAuthorityError, match="cannot support this estimate"):
        fixture.estimate(recorded_at=NOW + timedelta(minutes=10), known_at=NOW + timedelta(minutes=30))


def test_no_future_supply_basis_leaks_into_estimate(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    fixture.vc_service.ingest_supply(
        fixture.provider,
        supply_result(
            fixture.provider,
            fixture.evidence.record_id,
            fixture.market_fact,
            acquisition_id="supply-future",
            acquired_at=NOW + timedelta(minutes=20),
        ),
    )
    with pytest.raises(CanonicalValuationAuthorityError, match="cannot support this estimate"):
        fixture.estimate(recorded_at=NOW + timedelta(minutes=10), known_at=NOW + timedelta(minutes=30))


def test_no_future_value_capture_rule_leaks_into_estimate(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    fixture.vc_service.ingest_rule(
        fixture.provider,
        rule_result(
            fixture.provider,
            fixture.evidence.record_id,
            acquisition_id="rule-future",
            acquired_at=NOW + timedelta(minutes=20),
        ),
    )
    with pytest.raises(CanonicalValuationAuthorityError, match="cannot support this estimate"):
        fixture.estimate(recorded_at=NOW + timedelta(minutes=10), known_at=NOW + timedelta(minutes=30))


def test_no_future_methodology_version_leaks_into_estimate(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    corrected_known = NOW + timedelta(minutes=20)
    fixture.methodology_authority.persist_methodology(
        **methodology_payload(
            recorded_at=corrected_known,
            known_at=corrected_known,
            supersedes_record_id=fixture.methodology.record_id,
            correction_reason="Later methodology correction",
        )
    )
    with pytest.raises(CanonicalValuationAuthorityError, match="cannot support this estimate"):
        fixture.estimate(recorded_at=NOW + timedelta(minutes=10), known_at=NOW + timedelta(minutes=30))


def test_no_future_market_fact_leaks_into_estimate(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    # value_capture's own ingest-time gate (`_require_observed_market_facts`) refuses to
    # let a SupplyBasisSnapshot ever reference a market fact that is future-known
    # relative to the snapshot itself -- the real, reachable enforcement boundary for
    # this leakage class; a SupplyBasisSnapshot referencing a future market fact simply
    # cannot be persisted. (`estimate_fair_value` also independently re-validates every
    # referenced market fact's chronology against the estimate's own cutoff, as
    # documented defense in depth, but that branch is structurally unreachable through
    # the normal ingestion pipeline given this earlier gate.)
    repository = ObservedMarketFactRepository(fixture.db_path)
    service = ObservedMarketFactService(repository, market_fact_registry())
    future_known = NOW + timedelta(days=1)
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
        requested_at=future_known,
    )
    fact = NormalizedMarketFact(
        fact_type="circulating_supply",
        value="87000000",
        unit="native_units",
        quote_currency=None,
        effective_at=future_known,
        observed_at=future_known,
        confidence="0.9",
    )
    result = MarketFactAcquisitionResult(
        source_id="official-canonical-test-market-facts",
        provider_id="official-canonical-test-market-facts-provider",
        endpoint="https://example.org/market-facts/canonical-test-listing",
        parser_version="official-market-facts-v1",
        registry_fingerprint=market_fact_registry().require("official-canonical-test-market-facts").fingerprint,
        provider_source_record_id="canonical-test-listing",
        provider_source_record_version="2026-01-02",
        request=request,
        status="success",
        acquired_at=future_known,
        known_at=future_known,
        raw_payload_hash="sha256:" + "b" * 64,
        facts=(fact,),
    )
    future_fact = service.ingest(request, result, recorded_at=future_known)[0]
    with pytest.raises(SupplyAndValueCaptureAuthorityError, match="future-effective market fact"):
        fixture.vc_service.ingest_supply(
            fixture.provider,
            supply_result(
                fixture.provider,
                fixture.evidence.record_id,
                future_fact,
                acquisition_id="supply-future-fact",
            ),
        )


def test_no_future_correction_of_evidence_leaks_into_earlier_replay(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, _ = fixture.estimate()
    corrected = fixture.vc_service.ingest_evidence(
        fixture.provider,
        evidence_result(
            fixture.provider,
            acquisition_id="evidence-correction",
            acquired_at=NOW + timedelta(days=5),
            source_reference="official-tokenomics-page",
            amount="9999999",
            supersedes_record_id=fixture.evidence.record_id,
            correction_reason="Later restated disclosure",
        ),
    )
    assert corrected.recorded_at == NOW + timedelta(days=5)

    harness = harness_for(fixture)
    replay = harness.replay(case_for(fixture, estimate.logical_id, known_by=NOW + timedelta(minutes=10)))
    assert replay.estimate == estimate
    assert replay.reconstructed_lineage is not None
    assert replay.reconstructed_lineage.evidence_record_id == fixture.evidence.record_id
    assert replay.reconstructed_lineage.evidence_record_id != corrected.record_id


# 2. Historical replay validation --------------------------------------------------------


def test_replay_consistency_across_repeated_calls_is_identical(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, _ = fixture.estimate()
    harness = harness_for(fixture)
    case = case_for(fixture, estimate.logical_id, known_by=NOW + timedelta(minutes=10))

    first = harness.replay(case)
    second = harness.replay(case)
    assert first == second


def test_methodology_version_correctness_across_methodology_correction(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, _ = fixture.estimate()
    original_methodology_id = fixture.methodology.record_id

    corrected_known = NOW + timedelta(days=2)
    fixture.methodology_authority.persist_methodology(
        **methodology_payload(
            recorded_at=corrected_known,
            known_at=corrected_known,
            supersedes_record_id=fixture.methodology.record_id,
            correction_reason="Later methodology correction",
        )
    )

    harness = harness_for(fixture)
    historical_case = case_for(fixture, estimate.logical_id, known_by=NOW + timedelta(minutes=10))
    replay = harness.replay(historical_case)
    assert replay.reconstructed_lineage is not None
    assert replay.reconstructed_lineage.methodology_record_id == original_methodology_id


def test_effective_time_correctness_before_estimate_is_effective(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, _ = fixture.estimate()
    harness = harness_for(fixture)
    case = case_for(
        fixture,
        estimate.logical_id,
        effective_as_of=NOW - timedelta(days=1),
        known_by=NOW + timedelta(minutes=10),
    )
    replay = harness.replay(case)
    assert replay.estimate is None
    assert replay.assessment is None
    assert replay.reconstructed_lineage is None


def test_recorded_time_correctness_before_estimate_is_known(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    fixture.estimate()
    harness = harness_for(fixture)
    case = case_for(fixture, "does-not-matter-since-unknown-at-this-cutoff", known_by=NOW - timedelta(minutes=1))
    replay = harness.replay(case)
    assert replay.estimate is None


def test_correction_chronology_selects_correct_version_at_each_cutoff(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    original_estimate, original_assessment = fixture.estimate()
    correction_known = NOW + timedelta(days=10)
    corrected_estimate, corrected_assessment = fixture.estimate(
        recorded_at=correction_known,
        known_at=correction_known,
        supersedes_estimate_record_id=original_estimate.record_id,
        supersedes_assessment_record_id=original_assessment.record_id,
        correction_reason="Independent recomputation",
    )
    harness = harness_for(fixture)

    before_correction = harness.replay(
        case_for(fixture, original_estimate.logical_id, known_by=NOW + timedelta(minutes=10))
    )
    after_correction = harness.replay(case_for(fixture, original_estimate.logical_id, known_by=correction_known))

    assert before_correction.estimate == original_estimate
    assert before_correction.assessment == original_assessment
    assert after_correction.estimate == corrected_estimate
    assert after_correction.assessment == corrected_assessment


def test_deterministic_reconstruction_matches_declared_lineage(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, _ = fixture.estimate()
    harness = harness_for(fixture)
    replay = harness.replay(case_for(fixture, estimate.logical_id, known_by=NOW + timedelta(minutes=10)))

    lineage = replay.reconstructed_lineage
    assert lineage is not None
    assert lineage.methodology_record_id == estimate.methodology_record_id
    assert lineage.methodology_record_version == estimate.methodology_record_version
    assert lineage.evidence_record_id == estimate.evidence_record_id
    assert lineage.evidence_record_version == estimate.evidence_record_version
    assert lineage.rule_record_id == estimate.rule_record_id
    assert lineage.rule_record_version == estimate.rule_record_version
    assert lineage.supply_record_id == estimate.supply_record_id
    assert lineage.supply_record_version == estimate.supply_record_version
    assert lineage.market_fact_record_ids == estimate.market_fact_record_ids
    assert lineage.market_fact_record_versions == estimate.market_fact_record_versions


# 3. Calibration reproducibility ----------------------------------------------------------


def test_calibration_run_is_byte_identical_across_repeated_executions(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, _ = fixture.estimate()
    harness = harness_for(fixture)
    cases = (case_for(fixture, estimate.logical_id, known_by=NOW + timedelta(minutes=10)),)

    first_run = harness.run(cases)
    second_run = harness.run(cases)

    assert first_run == second_run
    assert content_hash(first_run) == content_hash(second_run)


def test_calibration_run_hash_changes_when_underlying_state_changes(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, assessment = fixture.estimate()
    harness = harness_for(fixture)
    case = case_for(fixture, estimate.logical_id, known_by=NOW + timedelta(minutes=10))

    before = content_hash(harness.run((case,)))

    correction_known = NOW + timedelta(days=1)
    fixture.estimate(
        recorded_at=correction_known,
        known_at=correction_known,
        supersedes_estimate_record_id=estimate.record_id,
        supersedes_assessment_record_id=assessment.record_id,
        correction_reason="Independent recomputation for hash-change regression",
    )
    after_case = case_for(fixture, estimate.logical_id, known_by=correction_known)
    after = content_hash(harness.run((after_case,)))

    assert before != after


# 4. Boundary timestamps --------------------------------------------------------------------


def test_input_recorded_exactly_at_estimate_recorded_at_boundary_is_accepted(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    boundary = NOW + timedelta(minutes=10)
    boundary_evidence = fixture.vc_service.ingest_evidence(
        fixture.provider,
        evidence_result(fixture.provider, acquisition_id="evidence-boundary", acquired_at=boundary),
    )
    assert boundary_evidence.recorded_at == boundary
    # <= is the documented boundary (never strict <): equal timestamps must be accepted.
    estimate, _ = fixture.estimate(recorded_at=boundary, known_at=boundary)
    assert estimate.evidence_record_id == boundary_evidence.record_id


def test_input_recorded_one_microsecond_after_boundary_is_rejected(tmp_path: Path) -> None:
    # known_at is declared looser than recorded_at so the one-microsecond-late evidence
    # remains strict-known *eligible* (and, being the most recent, gets selected) --
    # isolating the exact boundary of the recorded_at cross-check PR #124 added, rather
    # than the repository's own known_by eligibility filter.
    fixture = setup(tmp_path)
    boundary = NOW + timedelta(minutes=10)
    just_after = boundary + timedelta(microseconds=1)
    fixture.vc_service.ingest_evidence(
        fixture.provider,
        evidence_result(fixture.provider, acquisition_id="evidence-just-after", acquired_at=just_after),
    )
    with pytest.raises(CanonicalValuationAuthorityError, match="cannot support this estimate"):
        fixture.estimate(recorded_at=boundary, known_at=boundary + timedelta(minutes=20))


def test_timestamp_valid_at_boundary_is_inclusive() -> None:
    cutoff = NOW
    assert timestamp_valid_at(NOW, cutoff) is True
    assert timestamp_valid_at(NOW - timedelta(microseconds=1), cutoff) is True
    assert timestamp_valid_at(NOW + timedelta(microseconds=1), cutoff) is False


# 5. Missing history ------------------------------------------------------------------------


def test_replay_before_any_history_exists_is_none_not_a_default(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    fixture.estimate()
    harness = harness_for(fixture)
    case = case_for(fixture, "logical-id-with-no-history-at-all", known_by=NOW + timedelta(minutes=10))
    replay = harness.replay(case)
    assert replay.estimate is None
    assert replay.assessment is None
    assert replay.reconstructed_lineage is None


def test_harness_run_over_empty_case_set_produces_empty_but_valid_run(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    harness = harness_for(fixture)
    run = harness.run(())
    assert run.replays == ()
    assert content_hash(run) == content_hash(harness.run(()))


# 6. Conflicting history --------------------------------------------------------------------


def test_conflicted_evidence_fails_closed_rather_than_averaging(tmp_path: Path) -> None:
    # The *only* evidence record for the category the estimate looks up
    # (evidence_type="official_disclosure") is conflicted -- there is no valid fallback
    # record of the *same* category for strict-known selection to fall back to, so this
    # proves fail-closed rather than "pick whichever candidate remains". A second,
    # non-conflicted evidence record of a *different* evidence_type provides valid
    # provenance for supply/rule ingestion (value_capture's own `_require_evidence`
    # ingest-time gate refuses to reference conflicted evidence at all) without ever
    # competing with the conflicted record in the estimate's own category-scoped lookup.
    manual = ManualService(tmp_path)
    provenance_evidence = manual.vc_service.ingest_evidence(
        manual.provider,
        evidence_result(
            manual.provider,
            acquisition_id="evidence-provenance",
            evidence_type="onchain_observation",
            source_reference="onchain-provenance-only",
        ),
    )
    conflicted_evidence = manual.vc_service.ingest_evidence(
        manual.provider,
        evidence_result(manual.provider, acquisition_id="evidence-conflicted-only", conflict_state="open"),
    )
    assert conflicted_evidence.conflict_state == "open"
    manual.vc_service.ingest_supply(
        manual.provider, supply_result(manual.provider, provenance_evidence.record_id, manual.market_fact)
    )
    manual.vc_service.ingest_rule(manual.provider, rule_result(manual.provider, provenance_evidence.record_id))
    with pytest.raises(CanonicalValuationAuthorityError, match="FundamentalEvidenceRecord"):
        manual.estimate()


def test_conflicted_rule_fails_closed_rather_than_averaging(tmp_path: Path) -> None:
    # Symmetric to the evidence case: the *only* rule for this category is conflicted.
    manual = ManualService(tmp_path)
    evidence = manual.vc_service.ingest_evidence(manual.provider, evidence_result(manual.provider))
    manual.vc_service.ingest_supply(
        manual.provider, supply_result(manual.provider, evidence.record_id, manual.market_fact)
    )
    conflicted_rule = manual.vc_service.ingest_rule(
        manual.provider,
        rule_result(manual.provider, evidence.record_id, acquisition_id="rule-conflicted-only", conflict_state="open"),
    )
    assert conflicted_rule.conflict_state == "open"
    with pytest.raises(CanonicalValuationAuthorityError, match="ValueCaptureRuleSnapshot"):
        manual.estimate()


# 7. Correction chains ------------------------------------------------------------------------


def test_three_deep_correction_chain_replays_correct_version_at_each_step(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    v1_estimate, v1_assessment = fixture.estimate()
    t2 = NOW + timedelta(days=1)
    v2_estimate, v2_assessment = fixture.estimate(
        recorded_at=t2,
        known_at=t2,
        supersedes_estimate_record_id=v1_estimate.record_id,
        supersedes_assessment_record_id=v1_assessment.record_id,
        correction_reason="First correction",
    )
    t3 = NOW + timedelta(days=2)
    v3_estimate, v3_assessment = fixture.estimate(
        recorded_at=t3,
        known_at=t3,
        supersedes_estimate_record_id=v2_estimate.record_id,
        supersedes_assessment_record_id=v2_assessment.record_id,
        correction_reason="Second correction",
    )

    harness = harness_for(fixture)
    at_v1 = harness.replay(case_for(fixture, v1_estimate.logical_id, known_by=NOW + timedelta(minutes=10)))
    at_v2 = harness.replay(case_for(fixture, v1_estimate.logical_id, known_by=t2))
    at_v3 = harness.replay(case_for(fixture, v1_estimate.logical_id, known_by=t3))

    assert at_v1.estimate == v1_estimate
    assert at_v2.estimate == v2_estimate
    assert at_v3.estimate == v3_estimate
    assert len({v1_estimate.record_id, v2_estimate.record_id, v3_estimate.record_id}) == 3
    assert fixture.service.fair_value_history(v1_estimate.logical_id) == (v1_estimate, v2_estimate, v3_estimate)


# 8. Out-of-order corrections -----------------------------------------------------------------


def test_out_of_order_correction_recorded_at_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, assessment = fixture.estimate(recorded_at=NOW + timedelta(days=5), known_at=NOW + timedelta(days=5))
    with pytest.raises(ValueError, match="recorded_at must follow predecessor"):
        fixture.estimate(
            recorded_at=NOW + timedelta(days=1),
            known_at=NOW + timedelta(days=6),
            supersedes_estimate_record_id=estimate.record_id,
            supersedes_assessment_record_id=assessment.record_id,
            correction_reason="Attempted out-of-order correction",
        )


def test_out_of_order_methodology_correction_is_rejected(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    with pytest.raises(ValuationMethodologyIntegrityError, match="recorded_at must follow predecessor"):
        fixture.methodology_authority.persist_methodology(
            **methodology_payload(
                effective_at=NOW - timedelta(days=2),
                recorded_at=NOW - timedelta(minutes=1),
                known_at=NOW - timedelta(seconds=1),
                supersedes_record_id=fixture.methodology.record_id,
                correction_reason="Attempted out-of-order methodology correction",
            )
        )


# 9. Duplicate replay ---------------------------------------------------------------------------


def test_duplicate_replay_of_same_case_is_read_only_and_identical(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, _ = fixture.estimate()
    harness = harness_for(fixture)
    case = case_for(fixture, estimate.logical_id, known_by=NOW + timedelta(minutes=10))

    before_count = fixture.repository.count("fair_value_estimate_records")
    for _ in range(3):
        harness.replay(case)
    after_count = fixture.repository.count("fair_value_estimate_records")

    assert before_count == after_count == 1


def test_duplicate_case_in_same_run_yields_two_identical_replays(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, _ = fixture.estimate()
    harness = harness_for(fixture)
    case = case_for(fixture, estimate.logical_id, known_by=NOW + timedelta(minutes=10))

    run = harness.run((case, case))
    assert len(run.replays) == 2
    assert run.replays[0] == run.replays[1]


# 10. Deterministic replay ------------------------------------------------------------------------


def test_deterministic_replay_across_independently_constructed_harnesses(tmp_path: Path) -> None:
    fixture_a = setup(tmp_path / "a")
    fixture_b = setup(tmp_path / "b")
    estimate_a, _ = fixture_a.estimate()
    estimate_b, _ = fixture_b.estimate()
    assert estimate_a.record_id == estimate_b.record_id

    case_a = case_for(fixture_a, estimate_a.logical_id, known_by=NOW + timedelta(minutes=10))
    case_b = case_for(fixture_b, estimate_b.logical_id, known_by=NOW + timedelta(minutes=10))
    replay_a = harness_for(fixture_a).replay(case_a)
    replay_b = harness_for(fixture_b).replay(case_b)

    assert replay_a.estimate == replay_b.estimate
    assert replay_a.assessment == replay_b.assessment
    assert replay_a.reconstructed_lineage == replay_b.reconstructed_lineage


# 11. Reconstruction-integrity failure surface -----------------------------------------------------


def test_reconstruction_mismatch_raises_rather_than_silently_accepting(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, _ = fixture.estimate()
    harness = harness_for(fixture)
    case = case_for(fixture, estimate.logical_id, known_by=NOW + timedelta(minutes=10))
    replay = harness.replay(case)
    assert replay.reconstructed_lineage is not None

    tampered = replace(replay.reconstructed_lineage, methodology_record_id="not-the-real-record-id")
    with pytest.raises(ValuationCalibrationReconstructionError):
        _cross_check_lineage(case, estimate, tampered)


def test_leakage_error_raised_for_a_record_carrying_a_future_effective_at(tmp_path: Path) -> None:
    fixture = setup(tmp_path)
    estimate, _ = fixture.estimate()

    case = case_for(fixture, estimate.logical_id, known_by=NOW + timedelta(minutes=10))
    tampered = _FakeTimestamped(
        effective_at=NOW + timedelta(days=1), recorded_at=estimate.recorded_at, known_at=estimate.known_at
    )
    with pytest.raises(ValuationCalibrationLeakageError, match="effective_at"):
        _verify_no_leakage(case, "estimate", tampered)


@dataclass(frozen=True)
class _FakeTimestamped:
    """A minimal stand-in exposing only the three timestamp fields `_verify_no_leakage`
    reads -- used instead of constructing an out-of-chronology domain record, which
    `FairValueEstimateRecord.__post_init__` would itself reject before this harness-level
    check is ever reached."""

    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
