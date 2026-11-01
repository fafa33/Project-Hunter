from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.market_validation.membership_history import (
    HistoricalMembershipSnapshot,
    ImmutableMembershipHistory,
    TrustedHistoryAnchor,
)
from hunter.market_validation.models import ProjectValidationTarget

NOW = datetime(2026, 10, 1, tzinfo=UTC)


REPO = "fafa33/Project-Hunter"


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


def anchor(history):
    # Test fixture only: production anchor MUST come from independently trusted storage.
    import sqlite3

    with sqlite3.connect(history.path) as conn:
        row = conn.execute("SELECT sequence, digest FROM membership_history ORDER BY sequence DESC LIMIT 1").fetchone()
    return TrustedHistoryAnchor(repository="fafa33/Project-Hunter", sequence=row[0], digest=row[1])


def test_append_only_cutoff_and_supersession(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO)
    first = history.append(snapshot(), ingested_at=NOW)
    later = NOW + timedelta(days=1)
    second = history.append(snapshot(known_at=later, sector="defi"), ingested_at=later)
    assert first != second
    assert history.as_of(NOW, trusted_anchor=anchor(history))[0].project.sector == "other"
    assert history.as_of(later, trusted_anchor=anchor(history))[0].project.sector == "defi"
    with pytest.raises(ValueError, match="advance"):
        history.append(snapshot(known_at=NOW), ingested_at=NOW)
    history.append(snapshot(2), ingested_at=NOW)  # Different candidates need no global watermark.


def test_mutation_and_deletion_blocked(tmp_path):
    import sqlite3

    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO)
    history.append(snapshot(), ingested_at=NOW)
    with sqlite3.connect(history.path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM membership_history")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE membership_history SET content = 'tampered'")


def test_legacy_shadow_entrypoint_is_disabled(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO)
    history.append(snapshot(), ingested_at=NOW)
    with pytest.raises(ValueError, match="legacy callback admission disabled"):
        history.authorize_shadow(
            cutoff=NOW,
            run_id="candidate",
            baseline_run_id="baseline",
            policy_version="v1",
            output_directory="reports/candidates",
            baseline_output_directory="reports/baseline",
            trusted_anchor=anchor(history),
            verify_attestation=lambda *_: True,
        )


def test_attestation_and_time_required(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO)
    with pytest.raises(ValueError, match="attestation"):
        history.append(replace(snapshot(), upstream_attestation=""), ingested_at=NOW)
    with pytest.raises(ValueError, match="before observation"):
        history.append(snapshot(known_at=NOW - timedelta(seconds=1)), ingested_at=NOW)


def test_replace_insert_cannot_rewrite_history_from_separate_connection(tmp_path):
    import sqlite3

    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO)
    digest = history.append(snapshot(), ingested_at=NOW)
    with sqlite3.connect(history.path) as conn:
        original = conn.execute(
            "SELECT sequence, candidate_id, known_at, content, digest, previous_digest " "FROM membership_history"
        ).fetchone()
        for replacement in (
            (
                original[0],
                original[1],
                original[2],
                original[3].replace("identified", "rejected"),
                "forged-digest",
                original[5],
            ),
            (
                original[0] + 1,
                original[1],
                original[2],
                original[3].replace("identified", "rejected"),
                original[4],
                original[5],
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable history conflict"):
                conn.execute(
                    "INSERT OR REPLACE INTO membership_history "
                    "(sequence, candidate_id, known_at, content, digest, previous_digest) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    replacement,
                )
    assert history.as_of(NOW, trusted_anchor=anchor(history))[0].lifecycle_status == "identified"
    assert history.append(snapshot(2), ingested_at=NOW) != digest


def test_external_anchor_rejects_tail_truncation_and_rollback(tmp_path):
    import sqlite3

    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO)
    history.append(snapshot(1), ingested_at=NOW)
    history.append(snapshot(2), ingested_at=NOW)
    trusted = anchor(history)
    with sqlite3.connect(history.path) as conn:
        conn.execute("DROP TRIGGER membership_history_no_delete")
        conn.execute("DELETE FROM membership_history WHERE sequence=2")
    with pytest.raises(ValueError, match="anchor mismatch"):
        history.as_of(NOW, trusted_anchor=trusted)


def test_preseeded_neutered_trigger_rejected(tmp_path):
    import sqlite3

    path = tmp_path / "history.sqlite"
    ImmutableMembershipHistory(path, repository=REPO)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER membership_history_no_update")
        conn.execute(
            "CREATE TRIGGER membership_history_no_update BEFORE UPDATE ON membership_history WHEN 0 BEGIN SELECT RAISE(ABORT, 'immutable history'); END"
        )
    with pytest.raises(ValueError, match="untrusted membership history trigger"):
        ImmutableMembershipHistory(path, repository=REPO)


def test_canonical_entity_requires_normalized_id(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO)
    for entity in ("Entity-A", " entity-a "):
        with pytest.raises(ValueError, match="normalized"):
            history.append(replace(snapshot(), canonical_entity_id=entity), ingested_at=NOW)


def test_attestation_payload_digest_changes_with_identity_and_sector(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO)
    history.append(snapshot(1), ingested_at=NOW)
    history.append(
        replace(snapshot(2), upstream_attestation="attestation:1", source_digest="sha256:1"), ingested_at=NOW
    )
    seen = []

    def verifier(attestation, payload_digest):
        seen.append((attestation, payload_digest))
        return False

    history.as_of(NOW, trusted_anchor=anchor(history), verify_attestation=verifier)
    assert len(seen) == 2
    assert seen[0][0] == seen[1][0]
    assert seen[0][1] != seen[1][1]


def test_historical_backfill_not_visible_before_ingestion(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO)
    history.append(snapshot(), ingested_at=NOW + timedelta(days=2))
    assert history.as_of(NOW, trusted_anchor=anchor(history)) == ()


def test_same_timestamp_conflict_rejected(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO)
    history.append(snapshot(), ingested_at=NOW)
    with pytest.raises(ValueError, match="advance"):
        history.append(snapshot(sector="defi"), ingested_at=NOW)


def test_reject_unrecognized_identity_outcome(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO)
    with pytest.raises(ValueError, match="invalid identity outcome"):
        history.append(replace(snapshot(), identity_outcome="invented"), ingested_at=NOW)
