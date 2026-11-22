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
