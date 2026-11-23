#!/usr/bin/env python3
"""Trusted Mac-side pull/worker for Issue #575 workspace grant publication.

Polls GitHub issue comments for signed dispatch requests, verifies exact GitHub
run identity and repo-pinned roots, claims a durable replay-safe correlation
record, issues an atomic workspace grant into the local trusted authority, and
publishes only a signed non-secret receipt. Private keys/tokens never leave the
trusted domain.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation.issue_agent_control import load_configuration
from hunter.automation.issue_agent_execution import IssueAgentExecutionTarget
from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch
from hunter.automation.workspace_authority_client import WorkspaceAuthorityClient
from hunter.automation.workspace_grant_correlation import CorrelationClaim, WorkspaceGrantCorrelationStore
from hunter.automation.workspace_grant_dispatch import _PREFIX as DISPATCH_PREFIX
from hunter.automation.workspace_grant_dispatch import WorkspaceGrantDispatchRequest, verify_dispatch_request
from hunter.automation.workspace_grant_handoff import sign_grant_handoff, verify_grant_handoff
from hunter.automation.workspace_grant_transport import (
    fetch_issue_grant_comments,
    publish_signed_grant_comment,
    verify_github_run,
)
from hunter.automation.workspace_provisioning import provision_verified_dispatch


def _decode_dispatch_comments(comments: list[dict[str, object]], *, verification_key) -> list:
    import base64

    requests = []
    for item in comments:
        body = item.get("body")
        if not isinstance(body, str) or not body.startswith(DISPATCH_PREFIX):
            continue
        raw = body[len(DISPATCH_PREFIX) :]
        try:
            document = base64.b64decode(raw, validate=True)
        except Exception:
            raise WorkspaceAuthorityMismatch("invalid dispatch transport") from None
        requests.append(verify_dispatch_request(document, verification_key=verification_key))
    return requests


def process_dispatch(
    *,
    request,
    environment: dict[str, str],
    signing_key: Ed25519PrivateKey,
    verification_key,
    correlation_store: WorkspaceGrantCorrelationStore,
    worker_owner: str,
    workspace_root: Path,
    now: datetime | None = None,
) -> bytes | None:
    instant = now or datetime.now(UTC)
    repository = environment.get("HUNTER_ISSUE_AGENT_REPOSITORY", "")
    checkpoint = environment.get("HUNTER_WORKSPACE_CHECKPOINT", "")
    token = environment.get("HUNTER_WORKSPACE_GITHUB_COMMENT_TOKEN", "")
    if request.repository != repository or request.checkpoint != checkpoint:
        raise WorkspaceAuthorityMismatch("dispatch binding mismatch")
    if not token:
        raise WorkspaceAuthorityMismatch("GitHub read credential missing")
    # Verify exact GitHub-origin run identity before any privileged issuance.
    verify_github_run(
        repository=repository,
        run_id=request.run_id,
        token=token,
        run_attempt=request.run_attempt,
        workflow_path=request.workflow_path,
        head_branch="main",
        head_sha=request.control_sha,
    )
    claim = CorrelationClaim(
        repository=request.repository,
        issue=request.issue,
        authorization_id=request.authorization_id,
        ledger_identity=request.ledger_identity,
        run_id=request.run_id,
        run_attempt=request.run_attempt,
        workflow_path=request.workflow_path,
        control_sha=request.control_sha,
        base_sha=request.base_sha,
        authorized_branch=request.authorized_branch,
        checkpoint=request.checkpoint,
        status="active",
    )
    state = correlation_store.claim_or_get(claim, owner=worker_owner, now=instant)
    if state.get("receipt_published"):
        return None
    target = IssueAgentExecutionTarget(
        authorization_id=request.authorization_id,
        issue_number=request.issue,
        repository=request.repository,
        branch=request.authorized_branch,
        base_ref="main",
        base_sha=request.base_sha,
    )
    # Reuse an already-issued matching grant after interruption; never duplicate.
    existing_grant = None
    try:
        existing_grant = WorkspaceAuthorityClient(environment).read_grant(request.issue)
    except Exception:
        existing_grant = None
    if existing_grant is not None:
        if (
            existing_grant.issue == request.issue
            and existing_grant.repository == request.repository
            and existing_grant.branch == request.authorized_branch
            and existing_grant.base_sha == request.base_sha
            and existing_grant.checkpoint == request.checkpoint
            and existing_grant.task_scope_digest == request.authorization_id.rpartition(":")[2]
            and existing_grant.next_action == "dispatch"
        ):
            grant = existing_grant
        else:
            raise WorkspaceAuthorityMismatch("existing grant binding mismatch")
    else:
        grant = provision_verified_dispatch(
            target=target,
            repository=request.repository,
            workspace_root=workspace_root,
            trusted_environment=environment,
            issuer_instance=worker_owner,
            now=instant,
        )
    # Registry-issued grant is the authority; dispatch request cannot widen it.
    if (
        grant.issue != request.issue
        or grant.repository != request.repository
        or grant.branch != request.authorized_branch
        or grant.base_sha != request.base_sha
        or grant.checkpoint != request.checkpoint
        or grant.task_scope_digest != request.authorization_id.rpartition(":")[2]
    ):
        raise WorkspaceAuthorityMismatch("issued grant drift")
    receipt = sign_grant_handoff(
        grant=grant,
        authorization_id=request.authorization_id,
        run_id=request.run_id,
        run_attempt=request.run_attempt,
        signing_key=signing_key,
    )
    verify_grant_handoff(
        receipt,
        verification_key=verification_key,
        authorization_id=request.authorization_id,
        run_id=request.run_id,
        run_attempt=request.run_attempt,
        repository=request.repository,
        issue=request.issue,
        branch=grant.branch,
        base_sha=grant.base_sha,
        checkpoint=request.checkpoint,
        now=instant,
    )
    publish_signed_grant_comment(repository=repository, issue=request.issue, token=token, document=receipt)
    fencing = state["fencing"]
    correlation_store.mark_published(
        claim, owner=worker_owner, fencing=int(fencing) if isinstance(fencing, int) else 0, now=datetime.now(UTC)
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--correlation-db", required=True)
    parser.add_argument("--worker-owner", default=f"mac-worker:{os.getpid()}")
    parser.add_argument("--authorization-id")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--run-attempt", type=int, default=1)
    parser.add_argument("--control-sha")
    parser.add_argument("--base-sha")
    parser.add_argument("--authorized-branch")
    parser.add_argument("--ledger-identity")
    parser.add_argument("--checkpoint")
    parser.add_argument("--workflow-path", default=".github/workflows/hunter-issue-agent-trigger.yml")
    args = parser.parse_args()
    env = dict(os.environ)
    pinned = load_configuration(Path(__file__).resolve().parents[1])
    if pinned.workspace_grant_verifying_key is None:
        raise WorkspaceAuthorityMismatch("repository-pinned workspace root missing")
    signing_hex = env.get("HUNTER_WORKSPACE_GRANT_SIGNING_KEY", "")
    if len(signing_hex) != 64:
        raise WorkspaceAuthorityMismatch("trusted publication configuration incomplete")
    signing_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(signing_hex))
    signer_pub = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    pinned_pub = pinned.workspace_grant_verifying_key.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    if signer_pub != pinned_pub:
        raise WorkspaceAuthorityMismatch("grant signer does not match pinned root")
    repository = env.get("HUNTER_ISSUE_AGENT_REPOSITORY", "")
    token = env.get("HUNTER_WORKSPACE_GITHUB_COMMENT_TOKEN", "")
    store = WorkspaceGrantCorrelationStore(Path(args.correlation_db))
    published = 0
    direct = [
        args.authorization_id,
        args.run_id,
        args.control_sha,
        args.base_sha,
        args.authorized_branch,
        args.ledger_identity,
        args.checkpoint,
    ]
    if any(direct):
        if not all(direct):
            raise WorkspaceAuthorityMismatch("incomplete direct correlation binding")
        req = WorkspaceGrantDispatchRequest(
            repository=repository,
            issue=args.issue,
            authorization_id=args.authorization_id,
            ledger_identity=args.ledger_identity,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            workflow_path=args.workflow_path,
            control_sha=args.control_sha,
            base_sha=args.base_sha,
            authorized_branch=args.authorized_branch,
            checkpoint=args.checkpoint,
        )
        if process_dispatch(
            request=req,
            environment=env,
            signing_key=signing_key,
            verification_key=pinned.workspace_grant_verifying_key,
            correlation_store=store,
            worker_owner=args.worker_owner,
            workspace_root=Path(args.workspace_root),
        ):
            published += 1
        print(f"published={published}")
        return 0
    comments = fetch_issue_grant_comments(repository=repository, issue=args.issue, token=token)
    requests = _decode_dispatch_comments(comments, verification_key=pinned.workspace_grant_verifying_key)
    for req in requests:
        if req.issue != args.issue:
            continue
        if process_dispatch(
            request=req,
            environment=env,
            signing_key=signing_key,
            verification_key=pinned.workspace_grant_verifying_key,
            correlation_store=store,
            worker_owner=args.worker_owner,
            workspace_root=Path(args.workspace_root),
        ):
            published += 1
    print(f"published={published}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
