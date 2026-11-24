"""Signed canonical dispatch request for Mac-side pull/worker verification.

This is a GitHub message-carrier payload, not authority. The Mac trusted worker
must verify signature, exact GitHub run identity, repo-pinned roots, and local
correlation/registry state before any privileged issuance.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch

_DOMAIN = b"hunter-workspace-grant-dispatch-v1\x00"
_PREFIX = "hunter-workspace-dispatch-v1:"
_FIELDS = frozenset(
    {
        "version",
        "repository",
        "issue",
        "authorization_id",
        "ledger_identity",
        "run_id",
        "run_attempt",
        "workflow_path",
        "control_sha",
        "base_sha",
        "authorized_branch",
        "checkpoint",
        "signature",
    }
)


@dataclass(frozen=True)
class WorkspaceGrantDispatchRequest:
    repository: str
    issue: int
    authorization_id: str
    ledger_identity: str
    run_id: int
    run_attempt: int
    workflow_path: str
    control_sha: str
    base_sha: str
    authorized_branch: str
    checkpoint: str


def _payload(value: Mapping[str, Any]) -> bytes:
    return _DOMAIN + json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(c in "0123456789abcdef" for c in value)


def sign_dispatch_request(request: WorkspaceGrantDispatchRequest, *, signing_key: Ed25519PrivateKey) -> bytes:
    if request.issue <= 0 or request.run_id <= 0 or request.run_attempt != 1:
        raise WorkspaceAuthorityMismatch("invalid dispatch request")
    if not _is_sha(request.control_sha) or not _is_sha(request.base_sha):
        raise WorkspaceAuthorityMismatch("invalid dispatch sha")
    if not request.repository or not request.authorization_id or not request.ledger_identity:
        raise WorkspaceAuthorityMismatch("incomplete dispatch request")
    if not request.workflow_path or not request.authorized_branch or not request.checkpoint:
        raise WorkspaceAuthorityMismatch("incomplete dispatch request")
    body = {
        "version": 1,
        "repository": request.repository,
        "issue": request.issue,
        "authorization_id": request.authorization_id,
        "ledger_identity": request.ledger_identity,
        "run_id": request.run_id,
        "run_attempt": request.run_attempt,
        "workflow_path": request.workflow_path,
        "control_sha": request.control_sha,
        "base_sha": request.base_sha,
        "authorized_branch": request.authorized_branch,
        "checkpoint": request.checkpoint,
    }
    signature = base64.b64encode(signing_key.sign(_payload(body))).decode("ascii")
    return json.dumps({**body, "signature": signature}, sort_keys=True, separators=(",", ":")).encode()


def verify_dispatch_request(document: bytes, *, verification_key: Ed25519PublicKey) -> WorkspaceGrantDispatchRequest:
    try:
        if len(document) > 8192:
            raise ValueError("oversized")
        decoded = json.loads(document)
        if not isinstance(decoded, dict) or set(decoded) != _FIELDS:
            raise ValueError("fields")
        signature = base64.b64decode(decoded["signature"], validate=True)
        body = {k: v for k, v in decoded.items() if k != "signature"}
        verification_key.verify(signature, _payload(body))
        if body["version"] != 1 or body["run_attempt"] != 1:
            raise ValueError("version/attempt")
        req = WorkspaceGrantDispatchRequest(
            repository=body["repository"],
            issue=body["issue"],
            authorization_id=body["authorization_id"],
            ledger_identity=body["ledger_identity"],
            run_id=body["run_id"],
            run_attempt=body["run_attempt"],
            workflow_path=body["workflow_path"],
            control_sha=body["control_sha"],
            base_sha=body["base_sha"],
            authorized_branch=body["authorized_branch"],
            checkpoint=body["checkpoint"],
        )
        if req.issue <= 0 or req.run_id <= 0 or not _is_sha(req.control_sha) or not _is_sha(req.base_sha):
            raise ValueError("binding")
        if not all(
            isinstance(getattr(req, f), str) and getattr(req, f)
            for f in (
                "repository",
                "authorization_id",
                "ledger_identity",
                "workflow_path",
                "authorized_branch",
                "checkpoint",
            )
        ):
            raise ValueError("binding")
        return req
    except (InvalidSignature, ValueError, TypeError, KeyError, OverflowError):
        raise WorkspaceAuthorityMismatch("signed dispatch request rejected") from None


def comment_body(document: bytes) -> str:
    if len(document) > 8192:
        raise WorkspaceAuthorityMismatch("oversized dispatch request")
    return _PREFIX + base64.b64encode(document).decode("ascii")


def select_dispatch_checkpoint(
    comments: list[dict[str, Any]],
    *,
    verification_key: Ed25519PublicKey,
    repository: str,
    issue: int,
    authorization_id: str,
    run_id: int,
    run_attempt: int,
    control_sha: str,
    base_sha: str,
    authorized_branch: str,
    ledger_identity: str,
) -> str:
    """Resolve checkpoint identity only from the exact K_AUTH-signed dispatch."""
    if len(comments) > 100:
        raise WorkspaceAuthorityMismatch("dispatch transport bound exceeded")
    matching = set()
    for item in comments:
        body = item.get("body")
        if not isinstance(body, str) or not body.startswith(_PREFIX):
            continue
        try:
            document = base64.b64decode(body[len(_PREFIX) :], validate=True)
            req = verify_dispatch_request(document, verification_key=verification_key)
        except ValueError:
            raise WorkspaceAuthorityMismatch("invalid signed dispatch transport") from None
        if (req.authorization_id, req.run_id) != (authorization_id, run_id):
            continue
        if (
            req.repository,
            req.issue,
            req.run_attempt,
            req.control_sha,
            req.base_sha,
            req.authorized_branch,
            req.ledger_identity,
            req.workflow_path,
        ) != (
            repository,
            issue,
            run_attempt,
            control_sha,
            base_sha,
            authorized_branch,
            ledger_identity,
            ".github/workflows/hunter-issue-agent-trigger.yml",
        ):
            raise WorkspaceAuthorityMismatch("dispatch checkpoint binding mismatch")
        matching.add(req.checkpoint)
    if len(matching) != 1:
        raise WorkspaceAuthorityMismatch("missing or ambiguous dispatch checkpoint")
    return next(iter(matching))
