"""Trusted, post-dispatch grant issuance into the single Issue #575 registry.

Only the verified issuer may call this function. It never accepts a model's
workspace path, branch, repository or base as independent authority.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hunter.automation.issue_agent_execution import IssueAgentExecutionTarget
from hunter.automation.issue_agent_workspace import workspace_path
from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch, WorkspaceGrant, WorkspaceRegistry
from hunter.automation.workspace_authority_client import WorkspaceAuthorityClient

_AUTHORITY_KEYS = (
    "HUNTER_WORKSPACE_REGISTRY_PATH",
    "HUNTER_WORKSPACE_AUTHORITY_KEY_HEX",
    "HUNTER_WORKSPACE_CHECKPOINT",
    "HUNTER_WORKSPACE_LEASE_TOKEN",
)


def provision_verified_dispatch(
    *,
    target: IssueAgentExecutionTarget,
    repository: str,
    workspace_root: Path,
    trusted_environment: Mapping[str, str],
    issuer_instance: str,
    now: datetime | None = None,
) -> WorkspaceGrant:
    """Issue an atomic CAS grant only for a signed, durably dispatched target.

    Call after signed authorization verification and durable ledger dispatch;
    no independent claim, authorization, or alternate registry is created.
    """
    api_mode = bool(trusted_environment.get("HUNTER_WORKSPACE_API_URL"))
    if api_mode and trusted_environment.get("HUNTER_WORKSPACE_REGISTRY_PATH"):
        raise WorkspaceAuthorityMismatch("conflicting workspace authorities")
    values = [trusted_environment.get(name, "") for name in (_AUTHORITY_KEYS[2:] if api_mode else _AUTHORITY_KEYS)]
    if not all(values):
        raise WorkspaceAuthorityMismatch("trusted issuer workspace configuration required")
    if api_mode:
        checkpoint, lease_token = values
        registry = WorkspaceAuthorityClient(trusted_environment, writer=True)
    else:
        path, key_hex, checkpoint, lease_token = values
        try:
            key = bytes.fromhex(key_hex)
        except ValueError:
            raise WorkspaceAuthorityMismatch("invalid issuer workspace authority key") from None
        registry = WorkspaceRegistry(Path(path), authority_key=key)
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        raise WorkspaceAuthorityMismatch("issuer clock must be timezone-aware")
    if not issuer_instance:
        raise WorkspaceAuthorityMismatch("issuer identity required")
    grant = WorkspaceGrant(
        repository=repository,
        issue=target.issue_number,
        task_scope_digest=target.authorization_id.partition(":")[2],
        workspace=str(workspace_path(workspace_root.resolve(), target)),
        branch=target.branch,
        base_sha=target.base_sha,
        head_sha=target.base_sha,
        checkpoint=checkpoint,
        lease_owner=issuer_instance,
        lease_token_digest=hashlib.sha256(lease_token.encode()).hexdigest(),
        lease_expiry=(max(instant, datetime.now(UTC)) + timedelta(minutes=30)).isoformat(),
        fencing=1,
        next_action="dispatch",
    )
    # Registry owns the lock across the entire read/ownership-check/write.
    registry.issue_exclusive(grant)
    return grant
