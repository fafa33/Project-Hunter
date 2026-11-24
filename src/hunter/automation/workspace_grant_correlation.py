"""Durable replay-safe correlation record for Mac-side trusted grant issuance.

This is local trusted state only. It prevents duplicate grants across restart/retry
by atomically claiming an exact (repo, issue, authorization, run, attempt) tuple
before any privileged issuance. It never stores private keys or lease tokens.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch


@dataclass(frozen=True)
class CorrelationClaim:
    repository: str
    issue: int
    authorization_id: str
    ledger_identity: str
    run_id: int
    run_attempt: int
    workflow_path: str
    control_sha: str
    base_sha: str
    authorized_branch: str
    checkpoint: str
    status: str


class WorkspaceGrantCorrelationStore:
    def __init__(self, path: Path) -> None:
        if not path.is_absolute():
            raise WorkspaceAuthorityMismatch("correlation store path must be absolute")
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS grant_correlations (
                correlation_key TEXT PRIMARY KEY,
                repository TEXT NOT NULL,
                issue INTEGER NOT NULL,
                authorization_id TEXT NOT NULL,
                ledger_identity TEXT NOT NULL,
                run_id INTEGER NOT NULL,
                run_attempt INTEGER NOT NULL,
                workflow_path TEXT NOT NULL,
                control_sha TEXT NOT NULL,
                base_sha TEXT NOT NULL,
                authorized_branch TEXT NOT NULL,
                checkpoint TEXT NOT NULL,
                status TEXT NOT NULL,
                claim_owner TEXT NOT NULL,
                claim_expiry TEXT NOT NULL,
                fencing INTEGER NOT NULL,
                receipt_published INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
        return conn

    @staticmethod
    def _key(claim: CorrelationClaim) -> str:
        raw = "|".join(
            [
                claim.repository,
                str(claim.issue),
                claim.authorization_id,
                str(claim.run_id),
                str(claim.run_attempt),
            ]
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _validate(claim: CorrelationClaim) -> None:
        if not claim.repository or claim.issue <= 0 or not claim.authorization_id:
            raise WorkspaceAuthorityMismatch("invalid correlation identity")
        if claim.run_id <= 0 or claim.run_attempt != 1:
            raise WorkspaceAuthorityMismatch("invalid correlation run")
        for value in (claim.control_sha, claim.base_sha):
            if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
                raise WorkspaceAuthorityMismatch("invalid correlation sha")
        if not claim.workflow_path or not claim.authorized_branch or not claim.checkpoint or not claim.ledger_identity:
            raise WorkspaceAuthorityMismatch("incomplete correlation binding")
        if claim.status not in ("active", "published"):
            raise WorkspaceAuthorityMismatch("invalid correlation status")

    def claim_or_get(
        self, claim: CorrelationClaim, *, owner: str, now: datetime, lease_seconds: int = 300
    ) -> dict[str, object]:
        self._validate(claim)
        if not owner or now.tzinfo is None or lease_seconds <= 0:
            raise WorkspaceAuthorityMismatch("invalid correlation claim")
        key = self._key(claim)
        expiry = datetime.fromtimestamp(now.timestamp() + lease_seconds, tz=UTC).isoformat()
        now_iso = now.isoformat()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT repository,issue,authorization_id,ledger_identity,run_id,run_attempt,workflow_path,control_sha,base_sha,authorized_branch,checkpoint,status,claim_owner,claim_expiry,fencing,receipt_published FROM grant_correlations WHERE correlation_key=?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO grant_correlations(
                        correlation_key,repository,issue,authorization_id,ledger_identity,run_id,run_attempt,
                        workflow_path,control_sha,base_sha,authorized_branch,checkpoint,status,claim_owner,claim_expiry,fencing,receipt_published,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        key,
                        claim.repository,
                        claim.issue,
                        claim.authorization_id,
                        claim.ledger_identity,
                        claim.run_id,
                        claim.run_attempt,
                        claim.workflow_path,
                        claim.control_sha,
                        claim.base_sha,
                        claim.authorized_branch,
                        claim.checkpoint,
                        "active",
                        owner,
                        expiry,
                        1,
                        0,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()
                return {"state": "claimed", "fencing": 1, "receipt_published": False}
            existing = {
                "repository": row[0],
                "issue": row[1],
                "authorization_id": row[2],
                "ledger_identity": row[3],
                "run_id": row[4],
                "run_attempt": row[5],
                "workflow_path": row[6],
                "control_sha": row[7],
                "base_sha": row[8],
                "authorized_branch": row[9],
                "checkpoint": row[10],
                "status": row[11],
                "claim_owner": row[12],
                "claim_expiry": row[13],
                "fencing": row[14],
                "receipt_published": bool(row[15]),
            }
            # Exact binding must match; any drift is fail-closed.
            for field in (
                "repository",
                "issue",
                "authorization_id",
                "ledger_identity",
                "run_id",
                "run_attempt",
                "workflow_path",
                "control_sha",
                "base_sha",
                "authorized_branch",
                "checkpoint",
            ):
                expected = getattr(claim, field)
                if existing[field] != expected:
                    raise WorkspaceAuthorityMismatch("correlation binding drift")
            if existing["receipt_published"]:
                conn.commit()
                return {"state": "published", "fencing": existing["fencing"], "receipt_published": True}
            expiry_dt = datetime.fromisoformat(str(existing["claim_expiry"]))
            if existing["claim_owner"] != owner and expiry_dt > now:
                raise WorkspaceAuthorityMismatch("correlation claim owned by active worker")
            fencing = int(existing["fencing"]) + (0 if existing["claim_owner"] == owner else 1)
            conn.execute(
                "UPDATE grant_correlations SET claim_owner=?, claim_expiry=?, fencing=?, updated_at=? WHERE correlation_key=?",
                (owner, expiry, fencing, now_iso, key),
            )
            conn.commit()
            return {
                "state": "reclaimed" if existing["claim_owner"] != owner else "claimed",
                "fencing": fencing,
                "receipt_published": False,
            }
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mark_published(self, claim: CorrelationClaim, *, owner: str, fencing: int, now: datetime) -> None:
        self._validate(claim)
        if not owner or now.tzinfo is None or fencing < 1:
            raise WorkspaceAuthorityMismatch("invalid correlation publication")
        key = self._key(claim)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT claim_owner, fencing, receipt_published FROM grant_correlations WHERE correlation_key=?",
                (key,),
            ).fetchone()
            if row is None:
                raise WorkspaceAuthorityMismatch("missing correlation claim")
            if row[0] != owner or int(row[1]) != fencing:
                raise WorkspaceAuthorityMismatch("stale correlation fencing")
            conn.execute(
                "UPDATE grant_correlations SET receipt_published=1, status='published', updated_at=? WHERE correlation_key=?",
                (now.isoformat(), key),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
