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
from hunter.automation.workspace_grant_transport import publish_signed_grant_comment


def publish(*, issue: int, authorization_id: str, run_id: int, attempt: int) -> None:
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
    grant = WorkspaceAuthorityClient(environment).read_grant(issue)
    if grant.repository != repository or grant.checkpoint != checkpoint or grant.next_action != "dispatch":
        raise WorkspaceAuthorityMismatch("registry grant not eligible for publication")
    if datetime.fromisoformat(grant.lease_expiry) <= datetime.now(UTC):
        raise WorkspaceAuthorityMismatch("registry lease expired")
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
    publish_signed_grant_comment(repository=repository, issue=issue, token=token, document=receipt)


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
