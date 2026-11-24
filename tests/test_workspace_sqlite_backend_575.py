"""Durable transactional registry regressions for Issue #575."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch, WorkspaceGrant
from hunter.automation.workspace_sqlite_backend import WorkspaceSQLiteBackend


def test_durable_exclusive_issue_and_workspace_and_lease(tmp_path: Path) -> None:
    now = datetime(2026, 11, 22, tzinfo=UTC)
    token = "secret"
    grant = WorkspaceGrant(
        repository="fafa33/Project-Hunter",
        issue=575,
        task_scope_digest="a" * 64,
        workspace=str(tmp_path / "workspace"),
        branch="issue-575-aaaa",
        base_sha="b" * 40,
        head_sha="b" * 40,
        checkpoint="signed-1",
        lease_owner="issuer-1",
        lease_token_digest=hashlib.sha256(token.encode()).hexdigest(),
        lease_expiry=(now + timedelta(minutes=30)).isoformat(),
        fencing=1,
        next_action="dispatch",
    )
    path = tmp_path / "authority.sqlite"
    first = WorkspaceSQLiteBackend(path, authority_key=b"x" * 32)
    second = WorkspaceSQLiteBackend(path, authority_key=b"x" * 32)
    first.issue_exclusive(grant)
    assert second.read_grant(575) == grant
    with pytest.raises(WorkspaceAuthorityMismatch, match="existing issue"):
        second.issue_exclusive(grant)
    with pytest.raises(WorkspaceAuthorityMismatch, match="workspace"):
        second.issue_exclusive(replace(grant, issue=576))
    with pytest.raises(WorkspaceAuthorityMismatch, match="lease ownership"):
        second.renew_owned_lease(issue=575, owner="attacker", lease_token=token, now=now)
    second.renew_owned_lease(issue=575, owner="issuer-1", lease_token=token, now=now + timedelta(minutes=10))
    renewed = first.read_grant(575)
    assert replace(renewed, lease_expiry=grant.lease_expiry) == grant
    with pytest.raises(WorkspaceAuthorityMismatch, match="expired lease"):
        first.renew_owned_lease(issue=575, owner="issuer-1", lease_token=token, now=now + timedelta(hours=2))
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE workspace_grants SET body='{}' WHERE issue=575")
    with pytest.raises(WorkspaceAuthorityMismatch, match="integrity"):
        second.read_grant(575)
