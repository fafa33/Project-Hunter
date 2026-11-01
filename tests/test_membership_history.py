from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.market_validation.membership_history import HistoricalMembershipSnapshot, ImmutableMembershipHistory
from hunter.market_validation.models import ProjectValidationTarget

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def snapshot(i=1, *, known_at=NOW, sector="other"):
    return HistoricalMembershipSnapshot(
        candidate_id=f"candidate-{i}",
        canonical_entity_id=f"entity-{i}",
        project=ProjectValidationTarget(f"entity-{i}", f"Entity {i}", sector),
        identity_outcome="exact",
        lifecycle_status="identified",
        observed_at=NOW,
        known_at=known_at,
        source_digest=f"sha256:{i}",
        registry_reference=f"registry:{i}",
        upstream_attestation=f"attestation:{i}",
    )


def test_append_only_cutoff_and_supersession(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite")
    first = history.append(snapshot())
    later = NOW + timedelta(days=1)
    second = history.append(snapshot(known_at=later, sector="defi"))
    assert first != second
    assert history.as_of(NOW)[0].project.sector == "other"
    assert history.as_of(later)[0].project.sector == "defi"
    with pytest.raises(ValueError, match="backdating"):
        history.append(snapshot(2))


def test_mutation_and_deletion_blocked(tmp_path):
    import sqlite3

    history = ImmutableMembershipHistory(tmp_path / "history.sqlite")
    history.append(snapshot())
    with sqlite3.connect(history.path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM membership_history")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE membership_history SET content = 'tampered'")


def test_shadow_is_fail_closed_without_verified_attestation(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite")
    for i in range(50):
        history.append(snapshot(i))
    result = history.authorize_shadow(
        cutoff=NOW,
        run_id="candidate",
        baseline_run_id="baseline",
        policy_version="v1",
        output_directory="reports/candidates",
        baseline_output_directory="reports/baseline",
    )
    assert not result.runnable
    assert len(result.targets) == 0
    assert all(d.outcome == "unavailable" for d in result.decisions)


def test_attestation_and_time_required(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite")
    with pytest.raises(ValueError, match="attestation"):
        history.append(replace(snapshot(), upstream_attestation=""))
    with pytest.raises(ValueError, match="before observation"):
        history.append(snapshot(known_at=NOW - timedelta(seconds=1)))


def test_trusted_verifier_controls_shadow_admission(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite")
    for i in range(50):
        history.append(snapshot(i))
    kwargs = dict(
        cutoff=NOW,
        run_id="candidate",
        baseline_run_id="baseline",
        policy_version="v1",
        output_directory="reports/candidates",
        baseline_output_directory="reports/baseline",
    )
    rejected = history.authorize_shadow(**kwargs, verify_attestation=lambda att, source, digest: False)
    accepted = history.authorize_shadow(**kwargs, verify_attestation=lambda att, source, digest: True)
    assert not rejected.runnable and accepted.runnable
    assert len(accepted.targets) == 50
