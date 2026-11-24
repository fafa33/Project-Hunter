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
import base64
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from hunter.automation import issue_agent_control as control
from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_control import load_configuration
from hunter.automation.issue_agent_execution import IssueAgentExecutionTarget
from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch
from hunter.automation.workspace_authority_client import WorkspaceAuthorityClient
from hunter.automation.workspace_grant_correlation import CorrelationClaim, WorkspaceGrantCorrelationStore
from hunter.automation.workspace_grant_dispatch import _PREFIX as DISPATCH_PREFIX
from hunter.automation.workspace_grant_dispatch import verify_dispatch_request
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


def _verify_canonical_ledger(
    *,
    request,
    configuration,
    token: str,
) -> None:
    """Verify the exact canonical AUTHORIZED ledger record before any grant issuance."""
    ledger_ref = state.ledger_ref(request.issue)
    if request.ledger_identity != ledger_ref:
        raise WorkspaceAuthorityMismatch("fabricated or non-canonical ledger identity")
    auth_header = "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
    store = state.GitLedgerStore(f"https://github.com/{request.repository}.git", auth_header=auth_header)
    github = control.GitHubRest(request.repository, token)
    provenance = control.run_provenance(github, configuration)
    try:
        _head, view = control.load_ledger(store, configuration, provenance, request.issue)
    except Exception as error:
        if isinstance(error, WorkspaceAuthorityMismatch):
            raise
        raise WorkspaceAuthorityMismatch("canonical ledger verification failed") from None
    if view.active != request.authorization_id:
        raise WorkspaceAuthorityMismatch("authorization is not active on canonical ledger")
    auth_view = view.authorizations.get(request.authorization_id)
    if auth_view is None:
        raise WorkspaceAuthorityMismatch("canonical authorization record missing")
    if auth_view.state != state.AUTHORIZED:
        raise WorkspaceAuthorityMismatch("authorization is not eligible for grant issuance")
    if auth_view.terminal:
        raise WorkspaceAuthorityMismatch("authorization is terminal or ineligible")
    evidence = auth_view.evidence.get(state.AUTHORIZED)
    if not isinstance(evidence, dict):
        raise WorkspaceAuthorityMismatch("canonical AUTHORIZED evidence missing")
    bindings = {
        "base_sha": request.base_sha,
        "control_sha": request.control_sha,
        "execution_branch": request.authorized_branch,
        "authorize_run_id": request.run_id,
    }
    for field, expected in bindings.items():
        if evidence.get(field) != expected:
            raise WorkspaceAuthorityMismatch(f"canonical ledger binding mismatch: {field}")
    task_scope = evidence.get("task_scope")
    if not isinstance(task_scope, dict) or task_scope.get("base_sha") != request.base_sha:
        raise WorkspaceAuthorityMismatch("canonical ledger task scope mismatch")
    if request.run_attempt != 1:
        raise WorkspaceAuthorityMismatch("canonical ledger run attempt mismatch")
    # The ledger record is state-signed and chain-verified; dispatch must not widen it.
    if evidence.get("base_sha") != request.base_sha:
        raise WorkspaceAuthorityMismatch("canonical ledger base binding mismatch")


def process_dispatch(
    *,
    request,
    environment: dict[str, str],
    signing_key: Ed25519PrivateKey,
    verification_key,
    correlation_store: WorkspaceGrantCorrelationStore,
    worker_owner: str,
    workspace_root: Path,
    configuration,
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
    # Canonical ledger evidence is mandatory before any privileged grant issuance.
    _verify_canonical_ledger(request=request, configuration=configuration, token=token)
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
    # Dispatch requests are signed by K_AUTH (GitHub control domain). The Mac
    # verifies them with the repo-pinned authorization key; the Mac grant
    # signing key remains exclusively local for grant receipt publication.
    auth_pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pinned.authorization_verifying_key))
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
    refused = 0
    # A caller-provided run/authorization tuple is not proof of an authorized
    # dispatch. Only the signed GitHub transport may reach privileged issuance.
    # In particular, do not turn CLI flags into a parallel authorization path.
    if any(
        (
            args.authorization_id,
            args.run_id,
            args.control_sha,
            args.base_sha,
            args.authorized_branch,
            args.ledger_identity,
            args.checkpoint,
        )
    ):
        raise WorkspaceAuthorityMismatch("direct unsigned dispatch is forbidden")
    comments = fetch_issue_grant_comments(repository=repository, issue=args.issue, token=token)
    requests = _decode_dispatch_comments(comments, verification_key=auth_pub)
    for req in requests:
        if req.issue != args.issue:
            continue
        try:
            if process_dispatch(
                request=req,
                environment=env,
                signing_key=signing_key,
                verification_key=pinned.workspace_grant_verifying_key,
                correlation_store=store,
                worker_owner=args.worker_owner,
                workspace_root=Path(args.workspace_root),
                configuration=pinned,
            ):
                published += 1
        except WorkspaceAuthorityMismatch as error:
            # A refused request never grants authority; continue scanning signed requests.
            print(f"dispatch refused: run={req.run_id}: {error}", file=sys.stderr)
            refused += 1
    print(f"published={published}")
    return int(refused > 0 and published == 0)


if __name__ == "__main__":
    raise SystemExit(main())
