"""Shared immutable-membership test builders; never production trust evidence."""

import sqlite3
from datetime import UTC, datetime

from hunter.market_validation.membership_history import HistoricalMembershipSnapshot, TrustedHistoryAnchor
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
    with sqlite3.connect(history.path) as conn:
        row = conn.execute("SELECT sequence, digest FROM membership_history ORDER BY sequence DESC LIMIT 1").fetchone()
    return TrustedHistoryAnchor(repository="fafa33/Project-Hunter", sequence=row[0], digest=row[1])
