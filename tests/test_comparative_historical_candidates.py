from datetime import UTC, datetime, timedelta

import pytest

from hunter.comparative_valuation.historical_candidates import (
    HistoricalCandidateAdmission,
    select_historical_candidates,
)
from hunter.comparative_valuation.models import PeerCandidate
from hunter.value_capture.models import EconomicClaimIdentity


def _admission(name="a", days=0):
    t = datetime(2025, 1, 1, tzinfo=UTC)
    identity = EconomicClaimIdentity(name, "claim", "asset", "representation", "token")
    candidate = PeerCandidate(
        identity=identity,
        source_record_id="source-" + name,
        source_record_version="v1",
        lifecycle_state="analyzable",
        sector_classification="protocol",
        value_capture_mechanism_type="burn",
        revenue_accounting_meaning="native",
        native_evidence_type="onchain_observation",
        quote_currency="usd",
        supply_basis="fully_diluted_supply",
    )
    return HistoricalCandidateAdmission(
        candidate=candidate,
        source_id="source-" + name,
        source_version="v1",
        taxonomy_id="taxonomy",
        taxonomy_version="v1",
        effective_at=t,
        published_at=t + timedelta(days=days),
        observed_at=t,
        known_at=t + timedelta(days=days),
        content_hash="sha256:" + "a" * 64,
    )


def _select(items):
    return select_historical_candidates(
        tuple(items),
        cutoff=datetime(2025, 1, 2, tzinfo=UTC),
        known_by=datetime(2025, 1, 2, tzinfo=UTC),
        taxonomy_id="taxonomy",
        taxonomy_version="v1",
    )


def test_future_admission_is_not_backfilled():
    assert _select([_admission(days=2)]) == ()


def test_historical_order_independent_of_input():
    from dataclasses import replace

    a = _admission("a")
    b = replace(_admission("b"), content_hash="sha256:" + "b" * 64)
    assert _select([b, a]) == _select([a, b])


def test_conflicting_membership_fails_closed():
    a = _admission()
    with pytest.raises(ValueError, match="conflicting"):
        _select([a, a])


def test_unversioned_provenance_fails_closed():
    from dataclasses import replace

    with pytest.raises(ValueError, match="versioned provenance"):
        replace(_admission(), taxonomy_version="")


def test_future_known_by_is_rejected():
    with pytest.raises(ValueError, match="known_by"):
        select_historical_candidates(
            (_admission(),),
            cutoff=datetime(2025, 1, 1, tzinfo=UTC),
            known_by=datetime(2025, 1, 2, tzinfo=UTC),
            taxonomy_id="taxonomy",
            taxonomy_version="v1",
        )


def test_second_representation_of_same_entity_fails_closed():
    from dataclasses import replace

    first = _admission("a")
    other_identity = replace(first.candidate.identity, representation_id="different")
    second = replace(first, candidate=replace(first.candidate, identity=other_identity))
    with pytest.raises(ValueError, match="economic-entity"):
        _select([first, second])


def test_malformed_hash_and_missing_taxonomy_fail_closed():
    from dataclasses import replace

    with pytest.raises(ValueError, match="SHA-256"):
        replace(_admission(), content_hash="sha256:" + "z" * 64)
    with pytest.raises(ValueError, match="versioned taxonomy"):
        select_historical_candidates(
            (_admission(),),
            cutoff=datetime(2025, 1, 2, tzinfo=UTC),
            known_by=datetime(2025, 1, 2, tzinfo=UTC),
            taxonomy_id="",
            taxonomy_version="v1",
        )


def test_inconsistent_admission_chronology_fails_closed():
    from dataclasses import replace

    admission = _admission()
    with pytest.raises(ValueError, match="chronology"):
        replace(admission, published_at=admission.effective_at - timedelta(seconds=1))


def test_timezone_equivalent_cutoff_replay():
    from datetime import timezone

    admission = _admission()
    offset = timezone(timedelta(hours=2))
    cutoff = datetime(2025, 1, 2, tzinfo=UTC)
    assert select_historical_candidates(
        (admission,),
        cutoff=cutoff.astimezone(offset),
        known_by=cutoff.astimezone(offset),
        taxonomy_id="taxonomy",
        taxonomy_version="v1",
    ) == _select([admission])


def test_correction_requires_provenance_and_cannot_silently_rewrite_history():
    from dataclasses import replace

    admission = _admission()
    with pytest.raises(ValueError, match="reason"):
        replace(admission, supersedes_content_hash="sha256:" + "b" * 64)
    with pytest.raises(ValueError, match="superseded evidence"):
        replace(admission, correction_reason="updated")
    correction = replace(
        admission,
        content_hash="sha256:" + "c" * 64,
        supersedes_content_hash="sha256:" + "b" * 64,
        correction_reason="source correction",
    )
    with pytest.raises(ValueError, match="canonical lineage resolution"):
        _select([correction])


def test_service_historical_candidate_boundary_requires_strict_known_policy():
    from types import SimpleNamespace

    from hunter.comparative_valuation.service import (
        CanonicalComparativeValuationAuthorityError,
        CanonicalComparativeValuationService,
    )

    admission = _admission()
    cutoff = datetime(2025, 1, 2, tzinfo=UTC)
    policy = SimpleNamespace(
        quality_state="accepted",
        conflict_state="none",
        effective_at=cutoff,
        known_at=cutoff,
        recorded_at=cutoff,
        taxonomy="taxonomy",
        taxonomy_version="v1",
        maximum_candidate_universe_size=10,
    )
    service = object.__new__(CanonicalComparativeValuationService)
    service.repository = SimpleNamespace(get_peer_policy=lambda _: policy)
    assert service.historical_candidates_for_policy(
        policy_record_id="policy",
        admissions=(admission,),
        cutoff=cutoff,
        known_by=cutoff,
    ) == (admission.candidate,)
    policy.recorded_at = cutoff + timedelta(seconds=1)
    with pytest.raises(CanonicalComparativeValuationAuthorityError, match="strict-known"):
        service.historical_candidates_for_policy(
            policy_record_id="policy",
            admissions=(admission,),
            cutoff=cutoff,
            known_by=cutoff,
        )


def test_valid_correction_replays_only_when_known_at_cutoff():
    from dataclasses import replace

    original = _admission()
    corrected = replace(
        original,
        content_hash="sha256:" + "c" * 64,
        supersedes_content_hash=original.content_hash,
        correction_reason="verified upstream correction",
        known_at=original.known_at + timedelta(days=1),
        published_at=original.published_at + timedelta(days=1),
        candidate=replace(original.candidate, sector_classification="corrected-protocol"),
    )
    early = select_historical_candidates(
        (corrected, original),
        cutoff=datetime(2025, 1, 1, tzinfo=UTC),
        known_by=datetime(2025, 1, 1, tzinfo=UTC),
        taxonomy_id="taxonomy",
        taxonomy_version="v1",
    )
    assert early == (original.candidate,)
    assert _select((corrected, original)) == (corrected.candidate,)
    assert _select((original, corrected)) == (corrected.candidate,)


def test_correction_branch_and_cross_identity_fail_closed():
    from dataclasses import replace

    original = _admission()
    correction = replace(
        original,
        content_hash="sha256:" + "c" * 64,
        supersedes_content_hash=original.content_hash,
        correction_reason="correct",
        published_at=original.published_at + timedelta(days=1),
        known_at=original.known_at + timedelta(days=1),
    )
    branch = replace(correction, content_hash="sha256:" + "d" * 64)
    with pytest.raises(ValueError, match="branching"):
        _select((original, correction, branch))
    alien = replace(
        correction, candidate=replace(correction.candidate, identity=_admission("alien").candidate.identity)
    )
    with pytest.raises(ValueError, match="lineage"):
        _select((original, alien))


def test_later_unknown_correction_does_not_rewrite_historical_replay():
    from dataclasses import replace

    original = _admission()
    future = replace(
        original,
        content_hash="sha256:" + "c" * 64,
        supersedes_content_hash=original.content_hash,
        correction_reason="later correction",
        published_at=original.published_at + timedelta(days=3),
        known_at=original.known_at + timedelta(days=3),
    )
    assert _select((original, future)) == (original.candidate,)


def test_cross_entity_digest_reuse_fails_closed():
    # Identical hashes for different entities must not silently create two
    # independently authorized historical membership records.
    with pytest.raises(ValueError, match="content hash"):
        _select((_admission("a"), _admission("b")))


def test_distinct_historical_digests_remain_order_independent():
    from dataclasses import replace

    a = _admission("a")
    b = replace(_admission("b"), content_hash="sha256:" + "b" * 64)
    assert _select((a, b)) == _select((b, a))


def test_historical_native_flow_requires_exact_window_and_known_policy():
    from types import SimpleNamespace

    from hunter.comparative_valuation.service import (
        CanonicalComparativeValuationAuthorityError,
        CanonicalComparativeValuationService,
    )

    cutoff = datetime(2025, 1, 2, tzinfo=UTC)
    policy = SimpleNamespace(
        quality_state="accepted",
        conflict_state="none",
        effective_at=cutoff,
        known_at=cutoff,
        recorded_at=cutoff,
        required_native_evidence_types=("onchain_observation",),
        required_value_capture_mechanism_types=("burn",),
        required_quote_currency="usd",
    )
    evidence = SimpleNamespace(
        accounting_period_start=cutoff - timedelta(days=365),
        accounting_period_end=cutoff,
        unit="usd",
        amount="100",
    )
    rule = SimpleNamespace(rate_or_proportion="0.5")
    service = object.__new__(CanonicalComparativeValuationService)
    service.repository = SimpleNamespace(get_peer_policy=lambda _: policy)
    service._strict_known_flow_evidence = lambda **_: (evidence, rule)
    kwargs = dict(
        policy_record_id="policy",
        identity=_admission().candidate.identity,
        evidence_type="onchain_observation",
        rule_type="burn",
        cutoff=cutoff,
        known_by=cutoff,
    )
    assert service.historical_native_flow_for_cutoff(**kwargs) == (evidence, rule)
    evidence.accounting_period_start += timedelta(days=1)
    with pytest.raises(CanonicalComparativeValuationAuthorityError, match="exact-365-day"):
        service.historical_native_flow_for_cutoff(**kwargs)
    evidence.accounting_period_start -= timedelta(days=1)
    policy.recorded_at += timedelta(seconds=1)
    with pytest.raises(CanonicalComparativeValuationAuthorityError, match="strict-known"):
        service.historical_native_flow_for_cutoff(**kwargs)
    policy.recorded_at = cutoff
    service._strict_known_flow_evidence = lambda **_: None
    with pytest.raises(CanonicalComparativeValuationAuthorityError, match="unavailable"):
        service.historical_native_flow_for_cutoff(**kwargs)
