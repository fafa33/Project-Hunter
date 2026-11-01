"""ADR 0040: append-only, cutoff-aware candidate membership evidence history.

This shadow-only service owns its own evidence ledger; mutable Registry rows are
never used to reconstruct historical state. SQLite integrity is not an external
signature: only entries explicitly attested by an authorized upstream verifier
may be admitted, and this module does not manufacture such attestations.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hunter.market_validation.models import ProjectValidationTarget
from hunter.market_validation.universe_membership import (
    AuthorizedUniverse,
    MarketValidationUniverseMembershipAuthority,
    MembershipEvidence,
)


def _utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("history timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class HistoricalMembershipSnapshot:
    candidate_id: str
    canonical_entity_id: str
    project: ProjectValidationTarget
    identity_outcome: str
    lifecycle_status: str
    observed_at: datetime
    known_at: datetime
    source_digest: str
    registry_reference: str
    upstream_attestation: str


class ImmutableMembershipHistory:
    """Append-only snapshots with explicit attestation and no retrospective backfill."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS membership_history (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id TEXT NOT NULL,
                    known_at TEXT NOT NULL,
                    content TEXT NOT NULL,
                    digest TEXT NOT NULL UNIQUE,
                    previous_digest TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS membership_history_candidate
                    ON membership_history(candidate_id, known_at, sequence);
                CREATE TRIGGER IF NOT EXISTS membership_history_no_replace
                    BEFORE INSERT ON membership_history
                    WHEN EXISTS (
                        SELECT 1 FROM membership_history
                        WHERE sequence = NEW.sequence OR digest = NEW.digest
                    )
                    BEGIN SELECT RAISE(ABORT, 'immutable history conflict'); END;
                CREATE TRIGGER IF NOT EXISTS membership_history_no_update
                    BEFORE UPDATE ON membership_history BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
                CREATE TRIGGER IF NOT EXISTS membership_history_no_delete
                    BEFORE DELETE ON membership_history BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def append(self, snapshot: HistoricalMembershipSnapshot) -> str:
        if not snapshot.candidate_id.strip() or not snapshot.canonical_entity_id.strip():
            raise ValueError("candidate and canonical economic identity required")
        if snapshot.project.project_id != snapshot.canonical_entity_id:
            raise ValueError("canonical entity mapping must match project identity")
        if not snapshot.source_digest.strip() or not snapshot.registry_reference.strip():
            raise ValueError("source provenance required")
        if not snapshot.upstream_attestation.strip():
            raise ValueError("independent upstream attestation required")
        known, observed = _utc(snapshot.known_at), _utc(snapshot.observed_at)
        if observed > known:
            raise ValueError("evidence cannot be known before observation")
        payload = {
            "candidate_id": snapshot.candidate_id,
            "canonical_entity_id": snapshot.canonical_entity_id,
            "project": {
                "project_id": snapshot.project.project_id,
                "name": snapshot.project.name,
                "sector": snapshot.project.sector,
                "metadata": dict(snapshot.project.metadata),
            },
            "identity_outcome": snapshot.identity_outcome,
            "lifecycle_status": snapshot.lifecycle_status,
            "known_at": known,
            "observed_at": observed,
            "source_digest": snapshot.source_digest,
            "registry_reference": snapshot.registry_reference,
            "upstream_attestation": snapshot.upstream_attestation,
        }
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            last = conn.execute(
                "SELECT digest, known_at FROM membership_history ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if last and known < last["known_at"]:
                raise ValueError("historical backdating is forbidden")
            previous = str(last["digest"]) if last else "GENESIS"
            digest = hashlib.sha256((previous + "\n" + content).encode()).hexdigest()
            conn.execute(
                "INSERT INTO membership_history(candidate_id, known_at, content, digest, previous_digest) VALUES (?, ?, ?, ?, ?)",
                (snapshot.candidate_id, known, content, digest, previous),
            )
        return digest

    def as_of(
        self,
        cutoff: datetime,
        *,
        verify_attestation: Callable[[str, str, str], bool] | None = None,
    ) -> tuple[MembershipEvidence, ...]:
        limit = _utc(cutoff)
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM membership_history ORDER BY sequence").fetchall()
        previous = "GENESIS"
        latest: dict[str, tuple[dict, str]] = {}
        for row in rows:
            digest = hashlib.sha256((previous + "\n" + row["content"]).encode()).hexdigest()
            if row["previous_digest"] != previous or row["digest"] != digest:
                raise ValueError("membership history integrity failure")
            previous = digest
            data = json.loads(row["content"])
            if data["candidate_id"] != row["candidate_id"] or data["known_at"] != row["known_at"]:
                raise ValueError("membership history index mismatch")
            if data["known_at"] <= limit:
                latest[data["candidate_id"]] = (data, digest)
        results = []
        for candidate_id, (data, digest) in sorted(latest.items()):
            project = ProjectValidationTarget(**data["project"])
            results.append(
                MembershipEvidence(
                    candidate_id=candidate_id,
                    canonical_entity_id=data["canonical_entity_id"],
                    project=project,
                    identity_outcome=data["identity_outcome"],
                    lifecycle_status=data["lifecycle_status"],
                    known_at=datetime.fromisoformat(data["known_at"]),
                    observed_at=datetime.fromisoformat(data["observed_at"]),
                    source_digest=data["source_digest"],
                    registry_reference=f"history:{digest}",
                    provenance_verified=(
                        verify_attestation(data["upstream_attestation"], data["source_digest"], digest)
                        if verify_attestation is not None
                        else False
                    ),
                )
            )
        return tuple(results)

    def authorize_shadow(
        self,
        *,
        cutoff: datetime,
        run_id: str,
        baseline_run_id: str,
        policy_version: str,
        output_directory: str,
        baseline_output_directory: str,
        verify_attestation: Callable[[str, str, str], bool] | None = None,
    ) -> AuthorizedUniverse:
        # Only a caller-supplied trusted verifier can attest historical evidence.
        # Without one, the shadow path remains non-runnable by design.
        return MarketValidationUniverseMembershipAuthority().authorize(
            self.as_of(cutoff, verify_attestation=verify_attestation),
            cutoff=cutoff,
            run_id=run_id,
            baseline_run_id=baseline_run_id,
            policy_version=policy_version,
            output_directory=output_directory,
            baseline_output_directory=baseline_output_directory,
        )
