"""Mac publisher's exact-run admission, with no real GitHub or secrets."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import hunter_workspace_grant_publish as publisher
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch, WorkspaceGrant
from hunter.automation.workspace_grant_handoff import verify_grant_handoff


def test_mac_publisher_signs_only_pinned_exact_run(monkeypatch):
    key = Ed25519PrivateKey.generate()
    digest = "a" * 64
    authorization = "hunter-issue-agent-authorization:" + digest
    grant = WorkspaceGrant(
        repository="fafa33/Project-Hunter",
        issue=575,
        task_scope_digest=digest,
        workspace="/tmp/hunter/575",
        branch="issue-575-test",
        base_sha="b" * 40,
        head_sha="b" * 40,
        checkpoint="checkpoint-test",
        lease_owner="trusted-mac",
        lease_token_digest=hashlib.sha256(b"never-publish-me").hexdigest(),
        lease_expiry=(datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        fencing=1,
        next_action="dispatch",
    )
    from cryptography.hazmat.primitives import serialization

    secret = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    monkeypatch.setenv("HUNTER_ISSUE_AGENT_REPOSITORY", grant.repository)
    monkeypatch.setenv("HUNTER_WORKSPACE_CHECKPOINT", grant.checkpoint)
    monkeypatch.setenv("HUNTER_WORKSPACE_GRANT_SIGNING_KEY", secret.hex())
    monkeypatch.setenv("HUNTER_WORKSPACE_GITHUB_COMMENT_TOKEN", "test-only-token")
    monkeypatch.setattr(
        publisher, "WorkspaceAuthorityClient", lambda _env: SimpleNamespace(read_grant=lambda _issue: grant)
    )
    monkeypatch.setattr(
        "hunter.automation.issue_agent_control.load_configuration",
        lambda _path: SimpleNamespace(repository=grant.repository, workspace_grant_verifying_key=key.public_key()),
    )
    calls = []
    monkeypatch.setattr(publisher, "verify_github_run", lambda **kwargs: calls.append(("run", kwargs)))
    monkeypatch.setattr(publisher, "publish_signed_grant_comment", lambda **kwargs: calls.append(("post", kwargs)))
    publisher.publish(issue=575, authorization_id=authorization, run_id=123, attempt=1)
    assert [name for name, _ in calls] == ["run", "post"]
    receipt = calls[1][1]["document"]
    assert b"never-publish-me" not in receipt
    assert b"test-only-token" not in receipt
    assert (
        verify_grant_handoff(
            receipt,
            verification_key=key.public_key(),
            authorization_id=authorization,
            run_id=123,
            run_attempt=1,
            repository=grant.repository,
            issue=575,
            branch=grant.branch,
            base_sha=grant.base_sha,
            checkpoint=grant.checkpoint,
        )
        == grant
    )
    calls.clear()
    with pytest.raises(WorkspaceAuthorityMismatch):
        publisher.publish(issue=575, authorization_id=authorization, run_id=123, attempt=2)
    assert not calls
    monkeypatch.setattr(
        "hunter.automation.issue_agent_control.load_configuration",
        lambda _path: SimpleNamespace(
            repository=grant.repository, workspace_grant_verifying_key=Ed25519PrivateKey.generate().public_key()
        ),
    )
    with pytest.raises(WorkspaceAuthorityMismatch):
        publisher.publish(issue=575, authorization_id=authorization, run_id=123, attempt=1)
    assert not calls
