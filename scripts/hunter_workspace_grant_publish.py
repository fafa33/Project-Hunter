#!/usr/bin/env python3
"""Trusted Mac-only post-dispatch signed workspace grant publication.

This CLI cannot mint an authorization or grant: it only signs an existing grant
read from the single private authority, then publishes its non-secret receipt.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch
from hunter.automation.workspace_authority_client import WorkspaceAuthorityClient
from hunter.automation.workspace_grant_handoff import sign_grant_handoff, verify_grant_handoff
from hunter.automation.workspace_grant_transport import publish_signed_grant_comment, verify_github_run


def publish(
    *,
    issue: int,
    authorization_id: str,
    run_id: int,
    attempt: int,
    grant=None,
    control_sha: str | None = None,
    base_sha: str | None = None,
    authorized_branch: str | None = None,
    ledger_identity: str | None = None,
    workflow_path: str = ".github/workflows/hunter-issue-agent-trigger.yml",
    correlation_store_path: str | os.PathLike[str] | None = None,
    correlation_owner: str | None = None,
) -> None:
    if issue <= 0 or run_id <= 0 or attempt != 1:
        raise WorkspaceAuthorityMismatch("invalid exact-run grant publication")
    environment = os.environ
    repository = environment.get("HUNTER_ISSUE_AGENT_REPOSITORY", "")
    checkpoint = environment.get("HUNTER_WORKSPACE_CHECKPOINT", "")
    signing_hex = environment.get("HUNTER_WORKSPACE_GRANT_SIGNING_KEY", "")
    token = environment.get("HUNTER_WORKSPACE_GITHUB_COMMENT_TOKEN", "")
    if not repository or not checkpoint or not token or len(signing_hex) != 64:
        raise WorkspaceAuthorityMismatch("trusted publication configuration incomplete")
    try:
        signer = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(signing_hex))
    except ValueError:
        raise WorkspaceAuthorityMismatch("invalid trusted grant signing key") from None
    from hunter.automation.issue_agent_control import load_configuration

    pinned = load_configuration(Path(__file__).resolve().parents[1])
    if pinned.repository != repository or pinned.workspace_grant_verifying_key is None:
        raise WorkspaceAuthorityMismatch("repository-pinned workspace root missing")
    signer_public = signer.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    pinned_public = pinned.workspace_grant_verifying_key.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    if signer_public != pinned_public:
        raise WorkspaceAuthorityMismatch("grant signer does not match pinned root")
    # Issuer passes the exact grant returned by its atomic dispatch issuance.
    # The CLI independently reads the same authority when invoked out of process.
    if grant is None:
        grant = WorkspaceAuthorityClient(environment).read_grant(issue)
    if (
        grant.issue != issue
        or grant.repository != repository
        or grant.checkpoint != checkpoint
        or grant.next_action != "dispatch"
    ):
        raise WorkspaceAuthorityMismatch("registry grant not eligible for publication")
    if datetime.fromisoformat(grant.lease_expiry) <= datetime.now(UTC):
        raise WorkspaceAuthorityMismatch("registry lease expired")
    # Exact bindings: caller-supplied values are never authority; when provided they must
    # match the verified run and grant, and correlation binds them atomically.
    expected_control_sha = control_sha
    expected_base_sha = base_sha if base_sha is not None else grant.base_sha
    expected_branch = authorized_branch if authorized_branch is not None else grant.branch
    if expected_base_sha != grant.base_sha or expected_branch != grant.branch:
        raise WorkspaceAuthorityMismatch("grant binding mismatch")
    correlation = None
    claim_state = None
    store_path = correlation_store_path or environment.get("HUNTER_WORKSPACE_CORRELATION_DB", "")
    if store_path:
        from hunter.automation.workspace_grant_correlation import CorrelationClaim, WorkspaceGrantCorrelationStore

        if not ledger_identity or not expected_control_sha:
            raise WorkspaceAuthorityMismatch("correlation binding incomplete")
        correlation = (
            CorrelationClaim(
                repository=repository,
                issue=issue,
                authorization_id=authorization_id,
                ledger_identity=ledger_identity,
                run_id=run_id,
                run_attempt=attempt,
                workflow_path=workflow_path,
                control_sha=expected_control_sha,
                base_sha=expected_base_sha,
                authorized_branch=expected_branch,
                checkpoint=checkpoint,
                status="active",
            ),
            WorkspaceGrantCorrelationStore(Path(store_path)),
            correlation_owner or f"publisher:{os.getpid()}",
        )
        claim_state = correlation[1].claim_or_get(correlation[0], owner=correlation[2], now=datetime.now(UTC))
        if claim_state.get("receipt_published"):
            return
    receipt = sign_grant_handoff(
        grant=grant,
        authorization_id=authorization_id,
        run_id=run_id,
        run_attempt=attempt,
        signing_key=signer,
    )
    verify_grant_handoff(
        receipt,
        verification_key=signer.public_key(),
        authorization_id=authorization_id,
        run_id=run_id,
        run_attempt=attempt,
        repository=repository,
        issue=issue,
        branch=grant.branch,
        base_sha=grant.base_sha,
        checkpoint=checkpoint,
    )
    verify_github_run(
        repository=repository,
        run_id=run_id,
        token=token,
        run_attempt=attempt,
        workflow_path=workflow_path,
        head_branch="main",
        head_sha=expected_control_sha,
    )
    publish_signed_grant_comment(repository=repository, issue=issue, token=token, document=receipt)
    if correlation is not None and claim_state is not None:
        fencing = claim_state["fencing"]
        correlation[1].mark_published(
            correlation[0],
            owner=correlation[2],
            fencing=fencing if isinstance(fencing, int) else 0,
            now=datetime.now(UTC),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    args = parser.parse_args()
    publish(issue=args.issue, authorization_id=args.authorization_id, run_id=args.run_id, attempt=args.run_attempt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
