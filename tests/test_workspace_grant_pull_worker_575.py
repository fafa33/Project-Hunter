import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch, WorkspaceGrant
from hunter.automation.workspace_grant_correlation import CorrelationClaim, WorkspaceGrantCorrelationStore
from hunter.automation.workspace_grant_handoff import sign_grant_handoff
from hunter.automation.workspace_grant_transport import comment_body as grant_comment
from hunter.automation.workspace_grant_transport import select_verified_grant


def _keypair():
    key = Ed25519PrivateKey.generate()
    return (
        key,
        key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        ).hex(),
    )


def _grant(digest="a" * 64, **overrides):
    base = dict(
        repository="fafa33/Project-Hunter",
        issue=575,
        task_scope_digest=digest,
        workspace="/tmp/hunter/575",
        branch="issue-575-a",
        base_sha="b" * 40,
        head_sha="b" * 40,
        checkpoint="checkpoint-1",
        lease_owner="mac-worker",
        lease_token_digest=hashlib.sha256(b"lease").hexdigest(),
        lease_expiry=(datetime.now(UTC) + timedelta(minutes=20)).isoformat(),
        fencing=1,
        next_action="dispatch",
    )
    base.update(overrides)
    return WorkspaceGrant(**base)


def test_replayed_or_stale_signed_grant_fails_closed():
    key = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    digest = "a" * 64
    auth = "hunter-issue-agent-authorization-v1:" + digest
    fresh = _grant(digest=digest, lease_expiry=(now + timedelta(minutes=5)).isoformat())
    doc = sign_grant_handoff(grant=fresh, authorization_id=auth, run_id=1, run_attempt=1, signing_key=key)
    kwargs = dict(
        verification_key=key.public_key(),
        authorization_id=auth,
        run_id=1,
        run_attempt=1,
        repository=fresh.repository,
        issue=575,
        branch=fresh.branch,
        base_sha=fresh.base_sha,
        checkpoint=fresh.checkpoint,
    )
    # stale receipt: validly signed then expired at verification time
    with pytest.raises(WorkspaceAuthorityMismatch):
        select_verified_grant([{"body": grant_comment(doc)}], **kwargs, now=now + timedelta(minutes=6))
    # replayed across different run/authorization is rejected
    with pytest.raises(WorkspaceAuthorityMismatch):
        select_verified_grant([{"body": grant_comment(doc)}], **{**kwargs, "run_id": 2, "now": now})


def test_forged_grant_signature_fails_closed():
    key, other = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    digest = "a" * 64
    auth = "hunter-issue-agent-authorization-v1:" + digest
    grant = _grant(digest=digest, lease_expiry=(now + timedelta(minutes=5)).isoformat())
    doc = sign_grant_handoff(grant=grant, authorization_id=auth, run_id=7, run_attempt=1, signing_key=key)
    with pytest.raises(WorkspaceAuthorityMismatch):
        select_verified_grant(
            [{"body": grant_comment(doc)}],
            verification_key=other.public_key(),
            authorization_id=auth,
            run_id=7,
            run_attempt=1,
            repository=grant.repository,
            issue=575,
            branch=grant.branch,
            base_sha=grant.base_sha,
            checkpoint=grant.checkpoint,
            now=now,
        )


def test_grant_from_different_auth_run_branch_base_workspace_fails_closed():
    key = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    digest = "a" * 64
    auth = "hunter-issue-agent-authorization-v1:" + digest
    grant = _grant(digest=digest, lease_expiry=(now + timedelta(minutes=5)).isoformat())
    doc = sign_grant_handoff(grant=grant, authorization_id=auth, run_id=7, run_attempt=1, signing_key=key)
    base_kwargs = dict(
        verification_key=key.public_key(),
        authorization_id=auth,
        run_id=7,
        run_attempt=1,
        repository=grant.repository,
        issue=575,
        branch=grant.branch,
        base_sha=grant.base_sha,
        checkpoint=grant.checkpoint,
        now=now,
    )
    for delta in [
        {"authorization_id": "hunter-issue-agent-authorization-v1:" + "b" * 64},
        {"run_id": 8},
        {"branch": "other"},
        {"base_sha": "c" * 40},
    ]:
        with pytest.raises(WorkspaceAuthorityMismatch):
            select_verified_grant([{"body": grant_comment(doc)}], **{**base_kwargs, **delta})
    # different workspace is bound inside signed grant; tampering fails signature/binding
    tampered = json.loads(doc)
    tampered["grant"]["workspace"] = "/tmp/evil"
    with pytest.raises(WorkspaceAuthorityMismatch):
        select_verified_grant([{"body": grant_comment(json.dumps(tampered).encode())}], **base_kwargs)


def test_expired_or_revoked_lease_fails_closed():
    key = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    digest = "a" * 64
    auth = "hunter-issue-agent-authorization-v1:" + digest
    grant = _grant(digest=digest, lease_expiry=(now + timedelta(minutes=1)).isoformat())
    doc = sign_grant_handoff(grant=grant, authorization_id=auth, run_id=9, run_attempt=1, signing_key=key)
    with pytest.raises(WorkspaceAuthorityMismatch):
        select_verified_grant(
            [{"body": grant_comment(doc)}],
            verification_key=key.public_key(),
            authorization_id=auth,
            run_id=9,
            run_attempt=1,
            repository=grant.repository,
            issue=575,
            branch=grant.branch,
            base_sha=grant.base_sha,
            checkpoint=grant.checkpoint,
            now=now + timedelta(minutes=2),
        )


def test_duplicate_publisher_race_has_single_correlation_winner(tmp_path):
    store = WorkspaceGrantCorrelationStore(tmp_path / "corr.sqlite")
    claim = CorrelationClaim(
        repository="fafa33/Project-Hunter",
        issue=575,
        authorization_id="auth:" + "a" * 64,
        ledger_identity="ledger:1",
        run_id=10,
        run_attempt=1,
        workflow_path=".github/workflows/hunter-issue-agent-trigger.yml",
        control_sha="1" * 40,
        base_sha="2" * 40,
        authorized_branch="issue-575-a",
        checkpoint="cp",
        status="active",
    )
    now = datetime.now(UTC)
    first = store.claim_or_get(claim, owner="w1", now=now)
    assert first["state"] == "claimed"
    with pytest.raises(WorkspaceAuthorityMismatch):
        store.claim_or_get(claim, owner="w2", now=now + timedelta(seconds=1))
    # after expiry, another worker can reclaim with fencing bump
    reclaimed = store.claim_or_get(claim, owner="w2", now=now + timedelta(minutes=10))
    assert reclaimed["state"] == "reclaimed"
    assert int(reclaimed["fencing"]) >= 2


def test_publication_interruption_before_comment_recovers_without_duplicate(tmp_path, monkeypatch):
    # simulate interruption: correlation claimed, not published; retry should not duplicate publish once marked.
    store = WorkspaceGrantCorrelationStore(tmp_path / "corr.sqlite")
    claim = CorrelationClaim(
        repository="fafa33/Project-Hunter",
        issue=575,
        authorization_id="auth:" + "a" * 64,
        ledger_identity="ledger:1",
        run_id=11,
        run_attempt=1,
        workflow_path=".github/workflows/hunter-issue-agent-trigger.yml",
        control_sha="1" * 40,
        base_sha="2" * 40,
        authorized_branch="issue-575-a",
        checkpoint="cp",
        status="active",
    )
    now = datetime.now(UTC)
    store.claim_or_get(claim, owner="w1", now=now)
    # no publish happened; same owner retries and gets claim back
    retry = store.claim_or_get(claim, owner="w1", now=now + timedelta(seconds=1))
    assert retry["state"] == "claimed"
    assert retry["receipt_published"] is False
    store.mark_published(claim, owner="w1", fencing=int(retry["fencing"]), now=now + timedelta(seconds=2))
    done = store.claim_or_get(claim, owner="w1", now=now + timedelta(seconds=3))
    assert done["state"] == "published"


def test_mac_trust_or_clock_failure_fails_closed(tmp_path):
    store = WorkspaceGrantCorrelationStore(tmp_path / "corr.sqlite")
    claim = CorrelationClaim(
        repository="fafa33/Project-Hunter",
        issue=575,
        authorization_id="auth:" + "a" * 64,
        ledger_identity="ledger:1",
        run_id=12,
        run_attempt=1,
        workflow_path=".github/workflows/hunter-issue-agent-trigger.yml",
        control_sha="1" * 40,
        base_sha="2" * 40,
        authorized_branch="issue-575-a",
        checkpoint="cp",
        status="active",
    )
    with pytest.raises(WorkspaceAuthorityMismatch):
        store.claim_or_get(claim, owner="w1", now=datetime.now())  # naive clock fails closed
