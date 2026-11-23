"""Untrusted comment transport cannot substitute for signed workspace authority."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch, WorkspaceGrant
from hunter.automation.workspace_grant_handoff import sign_grant_handoff
from hunter.automation.workspace_grant_transport import comment_body, select_verified_grant


def test_comment_transport_refuses_forgery_replay_and_ambiguity():
    key = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    digest = "a" * 64
    authorization = "hunter-issue-agent-authorization-v1:" + digest
    grant = WorkspaceGrant(
        repository="fafa33/Project-Hunter",
        issue=575,
        task_scope_digest=digest,
        workspace="/tmp/hunter/workspace",
        branch="issue-575-a",
        base_sha="b" * 40,
        head_sha="b" * 40,
        checkpoint="checkpoint-1",
        lease_owner="mac-issuer",
        lease_token_digest=hashlib.sha256(b"private-lease").hexdigest(),
        lease_expiry=(now + timedelta(minutes=20)).isoformat(),
        fencing=1,
        next_action="dispatch",
    )
    document = sign_grant_handoff(
        grant=grant, authorization_id=authorization, run_id=123, run_attempt=1, signing_key=key
    )
    receipt = {"body": comment_body(document)}
    kwargs = dict(
        verification_key=key.public_key(),
        authorization_id=authorization,
        run_id=123,
        run_attempt=1,
        repository=grant.repository,
        issue=575,
        branch=grant.branch,
        base_sha=grant.base_sha,
        checkpoint=grant.checkpoint,
        now=now,
    )
    assert b"private-lease" not in document
    assert select_verified_grant([{"body": "other"}, receipt], **kwargs) == grant
    for comments in (
        [],
        [receipt, receipt],
        [{"body": comment_body(document) + "!"}],
        [{"body": comment_body(document).replace("a", "z", 1)}],
    ):
        with pytest.raises(WorkspaceAuthorityMismatch):
            select_verified_grant(comments, **kwargs)
    for changed in (
        dict(run_id=124),
        dict(checkpoint="wrong"),
        dict(now=now + timedelta(minutes=21)),
        dict(verification_key=Ed25519PrivateKey.generate().public_key()),
    ):
        with pytest.raises(WorkspaceAuthorityMismatch):
            select_verified_grant([receipt], **{**kwargs, **changed})
    forged = json.loads(document)
    forged["grant"]["branch"] = "attacker"
    with pytest.raises(WorkspaceAuthorityMismatch):
        select_verified_grant([{"body": comment_body(json.dumps(forged).encode())}], **kwargs)


def test_fetch_transport_requires_trusted_target_and_token():
    from hunter.automation.workspace_grant_transport import fetch_issue_grant_comments

    for repo, issue, token in (
        ("other/../repo", 575, "token"),
        ("fafa33/Project-Hunter", 0, "token"),
        ("fafa33/Project-Hunter", 575, ""),
    ):
        with pytest.raises(WorkspaceAuthorityMismatch):
            fetch_issue_grant_comments(repository=repo, issue=issue, token=token)


def test_run_lookup_refuses_forged_and_stale_runs(monkeypatch):
    from unittest.mock import Mock

    from hunter.automation.workspace_grant_transport import verify_github_run

    valid = dict(
        id=123,
        run_attempt=1,
        path=".github/workflows/hunter-issue-agent-trigger.yml@refs/heads/main",
        head_branch="main",
        status="in_progress",
        repository={"full_name": "fafa33/Project-Hunter"},
    )

    def response(payload):
        result = Mock()
        result.status = 200
        result.read.return_value = json.dumps(payload).encode()
        result.__enter__ = Mock(return_value=result)
        result.__exit__ = Mock(return_value=False)
        return result

    monkeypatch.setattr(
        "hunter.automation.workspace_grant_transport.urlopen", lambda *_args, **_kwargs: response(valid)
    )
    kwargs = dict(repository="fafa33/Project-Hunter", run_id=123, token="token")
    verify_github_run(**kwargs)
    for change in (
        dict(run_attempt=2),
        dict(status="completed"),
        dict(head_branch="attacker"),
        dict(id=999),
        dict(path=".github/workflows/other.yml@refs/heads/main"),
        dict(repository={"full_name": "other/repo"}),
    ):
        monkeypatch.setattr(
            "hunter.automation.workspace_grant_transport.urlopen",
            lambda *_args, _payload={**valid, **change}, **_kwargs: response(_payload),
        )
        with pytest.raises(WorkspaceAuthorityMismatch):
            verify_github_run(**kwargs)
