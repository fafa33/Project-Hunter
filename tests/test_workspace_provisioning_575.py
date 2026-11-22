"""Trusted issuer registry provisioning and immutable lineage regressions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hunter.automation.issue_agent_execution import IssueAgentExecutionTarget
from hunter.automation.issue_agent_workspace import workspace_path
from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch, WorkspaceRegistry
from hunter.automation.workspace_provisioning import provision_verified_dispatch


def test_issuer_provisions_runtime_and_publisher_from_same_grant(tmp_path: Path) -> None:
    target = IssueAgentExecutionTarget(
        authorization_id="hunter-issue-agent-authorization-v1:" + "a" * 64,
        issue_number=575,
        repository="fafa33/Project-Hunter",
        branch="issue-575-aaaaaaaaaaaaaaaa",
        base_ref="main",
        base_sha="b" * 40,
    )
    env = {
        "HUNTER_WORKSPACE_REGISTRY_PATH": str(tmp_path / "registry.json"),
        "HUNTER_WORKSPACE_AUTHORITY_KEY_HEX": "11" * 32,
        "HUNTER_WORKSPACE_CHECKPOINT": "signed-checkpoint-1",
        "HUNTER_WORKSPACE_LEASE_TOKEN": "issuer-secret-lease",
    }
    now = datetime(2026, 11, 22, tzinfo=UTC)
    args = dict(
        target=target,
        repository=target.repository,
        workspace_root=tmp_path / "workspaces",
        trusted_environment=env,
        issuer_instance="trusted-issuer",
        now=now,
    )
    issued = provision_verified_dispatch(**args)
    registry = WorkspaceRegistry(
        Path(env["HUNTER_WORKSPACE_REGISTRY_PATH"]),
        authority_key=bytes.fromhex(env["HUNTER_WORKSPACE_AUTHORITY_KEY_HEX"]),
    )
    assert (
        registry.admit(
            issue=575,
            repository=target.repository,
            workspace=workspace_path(tmp_path / "workspaces", target),
            branch=target.branch,
            head_sha=target.base_sha,
            base_sha=target.base_sha,
            checkpoint="signed-checkpoint-1",
            lease_token="issuer-secret-lease",
            now=now,
        )
        == issued
    )
    assert (
        registry.admit_publication(
            issue=575,
            repository=target.repository,
            branch=target.branch,
            base_sha=target.base_sha,
            checkpoint="signed-checkpoint-1",
            lease_token="issuer-secret-lease",
            now=now,
        )
        == issued
    )
    with pytest.raises(WorkspaceAuthorityMismatch, match="governed lease handoff"):
        provision_verified_dispatch(**args)
    assert registry.read()["revision"] == 1
    with pytest.raises(WorkspaceAuthorityMismatch, match="configuration required"):
        provision_verified_dispatch(**{**args, "trusted_environment": {}})


def test_owned_lease_renews_without_changing_fence_or_lineage(tmp_path: Path) -> None:
    import hashlib
    from dataclasses import replace
    from datetime import timedelta

    from hunter.automation.workspace_authority import WorkspaceGrant

    now = datetime(2026, 11, 22, tzinfo=UTC)
    token = "renew-token"
    registry = WorkspaceRegistry(tmp_path / "registry.json", authority_key=b"k" * 32)
    original = WorkspaceGrant(
        repository="fafa33/Project-Hunter",
        issue=575,
        task_scope_digest="a" * 64,
        workspace=str(tmp_path / "workspace"),
        branch="issue-575-aaaa",
        base_sha="b" * 40,
        head_sha="b" * 40,
        checkpoint="checkpoint-1",
        lease_owner="issuer-1",
        lease_token_digest=hashlib.sha256(token.encode()).hexdigest(),
        lease_expiry=(now + timedelta(minutes=30)).isoformat(),
        fencing=1,
        next_action="dispatch",
    )
    assert registry.issue_exclusive(original) == 1
    with pytest.raises(WorkspaceAuthorityMismatch, match="governed lease handoff"):
        registry.issue_exclusive(replace(original, lease_owner="issuer-2"))
    with pytest.raises(WorkspaceAuthorityMismatch, match="renewal ownership"):
        registry.renew_owned_lease(issue=575, owner="issuer-2", lease_token=token, now=now)
    with pytest.raises(WorkspaceAuthorityMismatch, match="renewal token"):
        registry.renew_owned_lease(issue=575, owner="issuer-1", lease_token="wrong", now=now)
    assert (
        registry.renew_owned_lease(issue=575, owner="issuer-1", lease_token=token, now=now + timedelta(minutes=10)) == 2
    )
    updated = WorkspaceGrant(**registry.read()["grants"]["575"])
    assert replace(updated, lease_expiry=original.lease_expiry) == original
    with pytest.raises(WorkspaceAuthorityMismatch, match="expired lease"):
        registry.renew_owned_lease(issue=575, owner="issuer-1", lease_token=token, now=now + timedelta(hours=2))
