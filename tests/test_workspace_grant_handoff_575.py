"""Bounded, adversarial signed grant handoff contract."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch, WorkspaceGrant
from hunter.automation.workspace_grant_handoff import sign_grant_handoff, verify_grant_handoff


def test_signed_grant_is_bound_and_not_a_lease_credential():
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
    assert b"private-lease" not in document
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
    assert verify_grant_handoff(document, **kwargs) == grant
    for changed in (
        dict(run_id=124),
        dict(issue=576),
        dict(base_sha="c" * 40),
        dict(checkpoint="checkpoint-2"),
        dict(authorization_id="hunter-issue-agent-authorization-v1:" + "c" * 64),
        dict(now=now + timedelta(minutes=21)),
    ):
        with pytest.raises(WorkspaceAuthorityMismatch):
            verify_grant_handoff(document, **{**kwargs, **changed})
    forged = json.loads(document)
    forged["grant"]["branch"] = "attacker"
    with pytest.raises(WorkspaceAuthorityMismatch):
        verify_grant_handoff(json.dumps(forged).encode(), **kwargs)
    with pytest.raises(WorkspaceAuthorityMismatch):
        verify_grant_handoff(document, **{**kwargs, "verification_key": Ed25519PrivateKey.generate().public_key()})
    with pytest.raises(WorkspaceAuthorityMismatch):
        sign_grant_handoff(
            grant=replace(grant, task_scope_digest="c" * 64),
            authorization_id=authorization,
            run_id=123,
            run_attempt=1,
            signing_key=key,
        )
