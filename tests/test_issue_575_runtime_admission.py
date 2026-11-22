"""Issue #575: production Issue Agent workspace dispatch admission boundary."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from hunter.automation.issue_agent_execution import IssueAgentExecutionTarget
from hunter.automation.issue_agent_workspace import IssueAgentWorkspaceRuntime, workspace_path
from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch, WorkspaceGrant, WorkspaceRegistry


def test_dispatch_rejects_wrong_checkpoint_before_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = IssueAgentExecutionTarget(
        authorization_id="hunter-issue-agent-authorization-v1:" + "a" * 64,
        issue_number=575,
        repository="fafa33/Project-Hunter",
        branch="issue-575-aaaa",
        base_ref="main",
        base_sha="b" * 40,
    )
    token = "runtime-token"
    key = b"z" * 32
    root = tmp_path / "workspaces"
    grant = WorkspaceGrant(
        repository=target.repository,
        issue=575,
        task_scope_digest="c" * 64,
        workspace=str(workspace_path(root, target)),
        branch=target.branch,
        base_sha=target.base_sha,
        head_sha=target.base_sha,
        checkpoint="approved-checkpoint",
        lease_owner="trusted-issuer",
        lease_token_digest=hashlib.sha256(token.encode()).hexdigest(),
        lease_expiry=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        fencing=1,
        next_action="execute",
    )
    registry_path = tmp_path / "registry.json"
    WorkspaceRegistry(registry_path, authority_key=key).write_cas(expected_revision=None, grants={"575": grant})
    materialize = Mock(side_effect=AssertionError("must not read or materialize workspace"))
    monkeypatch.setattr("hunter.automation.issue_agent_workspace.materialize_workspace", materialize)
    settings = Mock()
    monkeypatch.setattr(
        "hunter.automation.issue_agent_workspace.AgentFallbackRuntimeSettings.from_environment",
        lambda _: settings,
    )
    env = {
        "HUNTER_WORKSPACE_REGISTRY_PATH": str(registry_path),
        "HUNTER_WORKSPACE_AUTHORITY_KEY_HEX": key.hex(),
        "HUNTER_WORKSPACE_CHECKPOINT": "wrong-checkpoint",
        "HUNTER_WORKSPACE_LEASE_TOKEN": token,
    }
    runtime = IssueAgentWorkspaceRuntime(workspace_root=root, repository=target.repository, environ=env)
    with pytest.raises(WorkspaceAuthorityMismatch, match="workspace lineage differs"):
        runtime.dispatch("signed", target)
    materialize.assert_not_called()
    env["HUNTER_WORKSPACE_CHECKPOINT"] = "approved-checkpoint"
    env["HUNTER_WORKSPACE_LEASE_TOKEN"] = "wrong"
    runtime = IssueAgentWorkspaceRuntime(workspace_root=root, repository=target.repository, environ=env)
    with pytest.raises(WorkspaceAuthorityMismatch, match="lease token"):
        runtime.dispatch("signed", target)
    materialize.assert_not_called()
    env.pop("HUNTER_WORKSPACE_LEASE_TOKEN")
    runtime = IssueAgentWorkspaceRuntime(workspace_root=root, repository=target.repository, environ=env)
    with pytest.raises(WorkspaceAuthorityMismatch, match="trusted workspace admission configuration required"):
        runtime.dispatch("signed", target)
    materialize.assert_not_called()


def test_missing_all_configuration_fails_closed_before_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = IssueAgentExecutionTarget(
        authorization_id="hunter-issue-agent-authorization-v1:" + "a" * 64,
        issue_number=575,
        repository="fafa33/Project-Hunter",
        branch="issue-575-aaaa",
        base_ref="main",
        base_sha="b" * 40,
    )
    materialize = Mock(side_effect=AssertionError("must not materialize"))
    monkeypatch.setattr("hunter.automation.issue_agent_workspace.materialize_workspace", materialize)
    monkeypatch.setattr(
        "hunter.automation.issue_agent_workspace.AgentFallbackRuntimeSettings.from_environment",
        lambda _: Mock(),
    )
    runtime = IssueAgentWorkspaceRuntime(
        workspace_root=tmp_path / "workspaces", repository=target.repository, environ={}
    )
    with pytest.raises(WorkspaceAuthorityMismatch, match="trusted workspace admission configuration required"):
        runtime.dispatch("signed", target)
    materialize.assert_not_called()
