import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch, WorkspaceGrant
from hunter.automation.workspace_grant_correlation import CorrelationClaim, WorkspaceGrantCorrelationStore
from hunter.automation.workspace_grant_dispatch import (
    WorkspaceGrantDispatchRequest,
    sign_dispatch_request,
    verify_dispatch_request,
)
from hunter.automation.workspace_grant_dispatch import (
    comment_body as dispatch_comment,
)
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


def _dispatch_request(**overrides):
    from hunter.automation import issue_agent_state as state

    base = dict(
        repository="fafa33/Project-Hunter",
        issue=575,
        authorization_id="hunter-issue-agent-authorization-v1:" + "a" * 64,
        ledger_identity=state.ledger_ref(575),
        run_id=321,
        run_attempt=1,
        workflow_path=".github/workflows/hunter-issue-agent-trigger.yml",
        control_sha="1" * 40,
        base_sha="2" * 40,
        authorized_branch="issue-575-a",
        checkpoint="checkpoint-575",
    )
    base.update(overrides)
    return WorkspaceGrantDispatchRequest(**base)


def _load_worker():
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    scripts = str(root / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    path = root / "scripts" / "hunter_workspace_grant_worker.py"
    spec = importlib.util.spec_from_file_location("hunter_workspace_grant_worker_575_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_k_auth_signed_dispatch_accepted_by_worker_decode():
    k_auth = Ed25519PrivateKey.generate()
    req = _dispatch_request()
    doc = sign_dispatch_request(req, signing_key=k_auth)
    worker = _load_worker()
    decoded = worker._decode_dispatch_comments([{"body": dispatch_comment(doc)}], verification_key=k_auth.public_key())
    assert len(decoded) == 1
    assert decoded[0] == req


def test_forged_or_wrong_key_dispatch_rejected():
    k_auth = Ed25519PrivateKey.generate()
    wrong = Ed25519PrivateKey.generate()
    req = _dispatch_request()
    doc = sign_dispatch_request(req, signing_key=k_auth)
    with pytest.raises(WorkspaceAuthorityMismatch):
        verify_dispatch_request(doc, verification_key=wrong.public_key())
    tampered = json.loads(doc)
    tampered["checkpoint"] = "evil"
    with pytest.raises(WorkspaceAuthorityMismatch):
        verify_dispatch_request(json.dumps(tampered).encode(), verification_key=k_auth.public_key())


def test_unsigned_direct_dispatch_rejected(monkeypatch, tmp_path):
    from types import SimpleNamespace

    worker = _load_worker()
    signing = Ed25519PrivateKey.generate()
    signing_hex = signing.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex()
    auth = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        worker,
        "load_configuration",
        lambda *_args, **_kwargs: SimpleNamespace(
            workspace_grant_verifying_key=signing.public_key(),
            authorization_verifying_key=auth.public_key()
            .public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
            .hex(),
        ),
    )
    monkeypatch.setenv("HUNTER_WORKSPACE_GRANT_SIGNING_KEY", signing_hex)
    monkeypatch.setenv("HUNTER_ISSUE_AGENT_REPOSITORY", "fafa33/Project-Hunter")
    monkeypatch.setattr(
        "sys.argv",
        [
            "hunter_workspace_grant_worker.py",
            "--issue",
            "575",
            "--workspace-root",
            str(tmp_path),
            "--correlation-db",
            str(tmp_path / "corr.sqlite"),
            "--authorization-id",
            "hunter-issue-agent-authorization-v1:" + "a" * 64,
        ],
    )
    with pytest.raises(WorkspaceAuthorityMismatch, match="direct unsigned dispatch is forbidden"):
        worker.main()


def _ledger_view_for(req, *, state_name="AUTHORIZED", active=None, evidence=None):
    from hunter.automation import issue_agent_state as state

    auth_id = req.authorization_id
    ev = evidence or {
        "base_sha": req.base_sha,
        "control_sha": req.control_sha,
        "execution_branch": req.authorized_branch,
        "authorize_run_id": req.run_id,
        "task_scope": {"base_sha": req.base_sha},
    }
    view = state.AuthorizationView(
        authorization_id=auth_id,
        state=state_name,
        records=[{}],
        evidence={state.AUTHORIZED: ev},
    )
    active_id = auth_id if active is None else active
    return state.LedgerView(
        repository_id=1,
        issue_number=req.issue,
        authorizations={auth_id: view},
        claimed=[auth_id],
        active=active_id,
        head_record_digest="h" * 64,
        next_seq=2,
    )


def _patch_ledger(monkeypatch, worker, ledger_view):
    monkeypatch.setattr(worker.state, "GitLedgerStore", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(worker.control, "GitHubRest", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(worker.control, "run_provenance", lambda *_args, **_kwargs: (lambda *_a, **_k: True))
    monkeypatch.setattr(worker.control, "load_ledger", lambda *_args, **_kwargs: ("head", ledger_view))


def test_worker_rejects_forged_ledger_identity(monkeypatch):
    worker = _load_worker()
    req = _dispatch_request(ledger_identity="ledger:forged")
    with pytest.raises(WorkspaceAuthorityMismatch, match="fabricated or non-canonical ledger identity"):
        worker._verify_canonical_ledger(request=req, configuration=object(), token="tok")


def test_worker_rejects_missing_ledger_record(monkeypatch):
    from hunter.automation import issue_agent_state as state

    worker = _load_worker()
    req = _dispatch_request()
    empty = state.LedgerView(1, req.issue, {}, [], None, None, 0)
    _patch_ledger(monkeypatch, worker, empty)
    with pytest.raises(WorkspaceAuthorityMismatch, match="not active on canonical ledger"):
        worker._verify_canonical_ledger(request=req, configuration=object(), token="tok")


def test_worker_rejects_revoked_or_superseded_authorization(monkeypatch):
    worker = _load_worker()
    req = _dispatch_request()
    ledger_view = _ledger_view_for(req, active="other-auth")
    _patch_ledger(monkeypatch, worker, ledger_view)
    with pytest.raises(WorkspaceAuthorityMismatch, match="not active on canonical ledger"):
        worker._verify_canonical_ledger(request=req, configuration=object(), token="tok")


def test_worker_rejects_wrong_ledger_bindings(monkeypatch):
    worker = _load_worker()
    base = _dispatch_request()
    for field, value in [
        ("run_id", 999),
        ("run_attempt", 2),
        ("control_sha", "f" * 40),
        ("base_sha", "e" * 40),
        ("authorized_branch", "evil-branch"),
    ]:
        req = _dispatch_request(**{field: value})
        # ledger has canonical/base bindings; dispatch is wrong
        ledger_view = _ledger_view_for(base)
        _patch_ledger(monkeypatch, worker, ledger_view)
        with pytest.raises(WorkspaceAuthorityMismatch):
            worker._verify_canonical_ledger(request=req, configuration=object(), token="tok")


def test_worker_accepts_valid_authorized_ledger_record(monkeypatch):
    worker = _load_worker()
    req = _dispatch_request()
    ledger_view = _ledger_view_for(req)
    _patch_ledger(monkeypatch, worker, ledger_view)
    worker._verify_canonical_ledger(request=req, configuration=object(), token="tok")
