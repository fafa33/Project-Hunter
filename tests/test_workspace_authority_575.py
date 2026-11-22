"""Issue #575: fail-closed workspace authority foundation regressions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.automation.workspace_authority import (
    WorkspaceAuthorityMismatch,
    WorkspaceGrant,
    WorkspaceRegistry,
)

SHA = "a" * 40


def grant(tmp_path: Path) -> tuple[WorkspaceGrant, str]:
    token = "secret-fence"
    return (
        WorkspaceGrant(
            repository="fafa33/Project-Hunter",
            issue=575,
            task_scope_digest="b" * 64,
            workspace=str(tmp_path / "registered"),
            branch="issue-575-global-workspace-authority",
            base_sha=SHA,
            head_sha=SHA,
            checkpoint="checkpoint-575-1",
            lease_owner="writer-1",
            lease_token_digest=hashlib.sha256(token.encode()).hexdigest(),
            lease_expiry=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            fencing=1,
            next_action="validate",
        ),
        token,
    )


def admit(registry: WorkspaceRegistry, g: WorkspaceGrant, token: str, **overrides: object) -> WorkspaceGrant:
    args = dict(
        issue=g.issue,
        repository=g.repository,
        workspace=Path(g.workspace),
        branch=g.branch,
        head_sha=g.head_sha,
        checkpoint=g.checkpoint,
        lease_token=token,
    )
    args.update(overrides)
    return registry.admit(**args)


def test_registered_exact_lineage_admits(tmp_path: Path) -> None:
    g, token = grant(tmp_path)
    registry = WorkspaceRegistry(tmp_path / "authority.json")
    assert registry.write_cas(expected_revision=None, grants={"575": g}) == 1
    assert admit(registry, g, token) == g
    assert registry.write_cas(expected_revision=1, grants={"575": g}) == 2
    with pytest.raises(WorkspaceAuthorityMismatch, match="stale registry revision"):
        registry.write_cas(expected_revision=1, grants={"575": g})


@pytest.mark.parametrize(
    "override",
    [
        {"issue": 576},
        {"repository": "other/repo"},
        {"workspace": Path("/tmp/unregistered")},
        {"branch": "main"},
        {"head_sha": "c" * 40},
        {"checkpoint": ""},
        {"lease_token": "wrong"},
    ],
)
def test_mismatched_coordinates_refuse(tmp_path: Path, override: dict[str, object]) -> None:
    g, token = grant(tmp_path)
    registry = WorkspaceRegistry(tmp_path / "authority.json")
    registry.write_cas(expected_revision=None, grants={"575": g})
    with pytest.raises(WorkspaceAuthorityMismatch, match="WORKSPACE_AUTHORITY_MISMATCH"):
        admit(registry, g, token, **override)


def test_missing_and_tampered_registry_refuse(tmp_path: Path) -> None:
    g, token = grant(tmp_path)
    registry = WorkspaceRegistry(tmp_path / "authority.json")
    with pytest.raises(WorkspaceAuthorityMismatch, match="registry unavailable"):
        admit(registry, g, token)
    registry.write_cas(expected_revision=None, grants={"575": g})
    raw = json.loads(registry.path.read_text())
    raw["grants"]["575"]["head_sha"] = "c" * 40
    registry.path.write_text(json.dumps(raw))
    with pytest.raises(WorkspaceAuthorityMismatch, match="registry integrity"):
        admit(registry, g, token)


def test_expired_lease_and_missing_checkpoint_refuse(tmp_path: Path) -> None:
    g, token = grant(tmp_path)
    registry = WorkspaceRegistry(tmp_path / "authority.json")
    expired = replace(g, lease_expiry=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    registry.write_cas(expected_revision=None, grants={"575": expired})
    with pytest.raises(WorkspaceAuthorityMismatch, match="lease expired"):
        admit(registry, expired, token)
    with pytest.raises(WorkspaceAuthorityMismatch, match="checkpoint"):
        registry.write_cas(expected_revision=1, grants={"575": replace(g, checkpoint="")})


def test_duplicate_workspace_refused(tmp_path: Path) -> None:
    g, _ = grant(tmp_path)
    registry = WorkspaceRegistry(tmp_path / "authority.json")
    with pytest.raises(WorkspaceAuthorityMismatch, match="workspace ownership conflict"):
        registry.write_cas(expected_revision=None, grants={"575": g, "576": replace(g, issue=576)})
