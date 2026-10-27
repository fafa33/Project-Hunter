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
    a, b = _admission("a"), _admission("b")
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
