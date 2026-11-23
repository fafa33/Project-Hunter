"""Signed, non-secret workspace grant receipt for the existing GitHub handoff.

This attests a registry-issued grant; it does not create authorization, carry a
lease credential, or substitute for the canonical ledger/registry admission.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch, WorkspaceGrant

_DOMAIN = b"hunter-workspace-grant-handoff-v1\x00"
_FIELDS = frozenset({"version", "authorization_id", "run_id", "run_attempt", "grant", "signature"})


def _payload(value: Mapping[str, Any]) -> bytes:
    return _DOMAIN + json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sign_grant_handoff(
    *,
    grant: WorkspaceGrant,
    authorization_id: str,
    run_id: int,
    run_attempt: int,
    signing_key: Ed25519PrivateKey,
) -> bytes:
    """Only the trusted post-dispatch issuer may call this with a registry-issued grant."""
    grant.validate()
    if authorization_id != "hunter-issue-agent-authorization-v1:" + grant.task_scope_digest:
        raise WorkspaceAuthorityMismatch("authorization/grant mismatch")
    if run_id <= 0 or run_attempt != 1:
        raise WorkspaceAuthorityMismatch("invalid bound run")
    body = {
        "version": 1,
        "authorization_id": authorization_id,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "grant": asdict(grant),
    }
    signature = base64.b64encode(signing_key.sign(_payload(body))).decode("ascii")
    return json.dumps({**body, "signature": signature}, sort_keys=True, separators=(",", ":")).encode()


def verify_grant_handoff(
    document: bytes,
    *,
    verification_key: Ed25519PublicKey,
    authorization_id: str,
    run_id: int,
    run_attempt: int,
    repository: str,
    issue: int,
    branch: str,
    base_sha: str,
    checkpoint: str,
    now: datetime | None = None,
) -> WorkspaceGrant:
    """Reject forgery, replay, stale lease and cross-authorization substitution."""
    try:
        if len(document) > 8192:
            raise ValueError("oversized")
        decoded = json.loads(document)
        if not isinstance(decoded, dict) or set(decoded) != _FIELDS:
            raise ValueError("envelope fields")
        signature = base64.b64decode(decoded["signature"], validate=True)
        body = {key: value for key, value in decoded.items() if key != "signature"}
        verification_key.verify(signature, _payload(body))
        if body["version"] != 1 or body["run_attempt"] != 1:
            raise ValueError("version or attempt")
        grant = WorkspaceGrant(**body["grant"])
        grant.validate()
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None or datetime.fromisoformat(grant.lease_expiry) <= instant:
            raise ValueError("expired grant")
        if (
            body["authorization_id"] != authorization_id
            or authorization_id != "hunter-issue-agent-authorization-v1:" + grant.task_scope_digest
            or body["run_id"] != run_id
            or body["run_attempt"] != run_attempt
            or grant.repository != repository
            or grant.issue != issue
            or grant.branch != branch
            or grant.base_sha != base_sha
            or grant.head_sha != base_sha
            or grant.checkpoint != checkpoint
        ):
            raise ValueError("binding mismatch")
        return grant
    except (InvalidSignature, ValueError, TypeError, KeyError, OverflowError):
        raise WorkspaceAuthorityMismatch("signed grant handoff rejected") from None
