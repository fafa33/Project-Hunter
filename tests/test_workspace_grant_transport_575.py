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
