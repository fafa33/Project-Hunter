"""Durable single-writer authority store for a trusted self-hosted issuer.

SQLite transactions serialize independent processes on one persistent local
filesystem. This is NOT a network/distributed SQLite backend; remote callers
must use a single trusted issuer service, never a shared SQLite file over NFS.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from hunter.automation.workspace_authority import VERSION, WorkspaceAuthorityMismatch, WorkspaceGrant, vars_grant


class WorkspaceSQLiteBackend:
    """Transactional, authenticated grant store with atomic exclusive ownership."""

    def __init__(self, path: Path, *, authority_key: bytes) -> None:
        if not path.is_absolute() or len(authority_key) < 32:
            raise WorkspaceAuthorityMismatch("trusted absolute database and key required")
        self.path = path
        self.key = authority_key

    def _connect(self) -> sqlite3.Connection:
        # No symlink, NFS or world-writable trust claim is made here.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS workspace_grants (issue INTEGER PRIMARY KEY, workspace TEXT NOT NULL UNIQUE, body TEXT NOT NULL, seal TEXT NOT NULL)"
        )
        return conn

    def _seal(self, body: str) -> str:
        return hmac.new(self.key, (VERSION + ":" + body).encode(), hashlib.sha256).hexdigest()

    def _decode(self, body: str, seal: str) -> WorkspaceGrant:
        if not hmac.compare_digest(seal, self._seal(body)):
            raise WorkspaceAuthorityMismatch("database grant integrity")
        try:
            grant = WorkspaceGrant(**json.loads(body))
            grant.validate()
            return grant
        except (TypeError, ValueError, KeyError):
            raise WorkspaceAuthorityMismatch("database grant schema") from None

    def read_grant(self, issue: int) -> WorkspaceGrant:
        with self._connect() as conn:
            row = conn.execute("SELECT body, seal FROM workspace_grants WHERE issue=?", (issue,)).fetchone()
        if row is None:
            raise WorkspaceAuthorityMismatch("unregistered issue")
        return self._decode(*row)

    def issue_exclusive(self, grant: WorkspaceGrant) -> None:
        grant.validate()
        body = json.dumps(vars_grant(grant), sort_keys=True, separators=(",", ":"))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO workspace_grants(issue,workspace,body,seal) VALUES(?,?,?,?)",
                    (grant.issue, str(Path(grant.workspace).resolve()), body, self._seal(body)),
                )
            except sqlite3.IntegrityError:
                raise WorkspaceAuthorityMismatch("existing issue or workspace requires governed handoff") from None
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def renew_owned_lease(
        self, *, issue: int, owner: str, lease_token: str, now: datetime, duration_seconds: int = 1800
    ) -> None:
        from dataclasses import replace

        if now.tzinfo is None or duration_seconds <= 0:
            raise WorkspaceAuthorityMismatch("invalid renewal time")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT body,seal FROM workspace_grants WHERE issue=?", (issue,)).fetchone()
            if row is None:
                raise WorkspaceAuthorityMismatch("unregistered issue")
            grant = self._decode(*row)
            if grant.lease_owner != owner or not hmac.compare_digest(
                grant.lease_token_digest, hashlib.sha256(lease_token.encode()).hexdigest()
            ):
                raise WorkspaceAuthorityMismatch("lease ownership")
            if datetime.fromisoformat(grant.lease_expiry) <= now:
                raise WorkspaceAuthorityMismatch("expired lease")
            updated = replace(grant, lease_expiry=(now + timedelta(seconds=duration_seconds)).isoformat())
            body = json.dumps(vars_grant(updated), sort_keys=True, separators=(",", ":"))
            conn.execute("UPDATE workspace_grants SET body=?,seal=? WHERE issue=?", (body, self._seal(body), issue))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
