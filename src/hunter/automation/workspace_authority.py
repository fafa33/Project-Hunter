"""Issue #575: fail-closed, integrity-bound workspace authority foundation.

This is an opt-in admission primitive, NOT a production-wide cutover. Existing
issuer leases remain authoritative until the global integration is proven.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VERSION = "hunter-workspace-authority-v1"
MISMATCH = "WORKSPACE_AUTHORITY_MISMATCH"


class WorkspaceAuthorityMismatch(ValueError):
    """Missing, stale, ambiguous, or forged workspace authority."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"{MISMATCH}: {reason}")


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _require_sha(value: str) -> None:
    if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
        raise WorkspaceAuthorityMismatch("invalid commit identity")


@dataclass(frozen=True)
class WorkspaceGrant:
    repository: str
    issue: int
    task_scope_digest: str
    workspace: str
    branch: str
    base_sha: str
    head_sha: str
    checkpoint: str
    lease_owner: str
    lease_token_digest: str
    lease_expiry: str
    fencing: int
    next_action: str

    def validate(self) -> None:
        if not self.repository or "/" not in self.repository or self.issue <= 0:
            raise WorkspaceAuthorityMismatch("repository or issue")
        if not self.workspace or not Path(self.workspace).is_absolute():
            raise WorkspaceAuthorityMismatch("workspace must be absolute")
        if not self.branch or not self.checkpoint or not self.next_action:
            raise WorkspaceAuthorityMismatch("branch/checkpoint/action missing")
        for value in (self.base_sha, self.head_sha, self.task_scope_digest, self.lease_token_digest):
            if len(value) == 64 and value in (self.task_scope_digest, self.lease_token_digest):
                if any(c not in "0123456789abcdef" for c in value):
                    raise WorkspaceAuthorityMismatch("invalid digest")
            else:
                _require_sha(value)
        if self.fencing < 1 or not self.lease_owner:
            raise WorkspaceAuthorityMismatch("missing lease fencing")
        try:
            expiry = datetime.fromisoformat(self.lease_expiry)
        except ValueError:
            raise WorkspaceAuthorityMismatch("invalid lease expiry") from None
        if expiry.tzinfo is None:
            raise WorkspaceAuthorityMismatch("lease expiry must be timezone-aware")


class WorkspaceRegistry:
    """Atomic revisioned registry. Only one external serialized writer is allowed.

    Callers must hold the existing trusted writer lock before mutation; this
    class deliberately never invents owner authorization or a signer.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise WorkspaceAuthorityMismatch("registry unavailable") from None
        if not isinstance(value, dict) or value.get("version") != VERSION:
            raise WorkspaceAuthorityMismatch("registry schema")
        expected = value.get("digest")
        body = {k: v for k, v in value.items() if k != "digest"}
        if not isinstance(expected, str) or not hmac.compare_digest(expected, _digest(body)):
            raise WorkspaceAuthorityMismatch("registry integrity")
        if not isinstance(value.get("revision"), int) or value["revision"] < 1:
            raise WorkspaceAuthorityMismatch("registry revision")
        if not isinstance(value.get("grants"), dict):
            raise WorkspaceAuthorityMismatch("registry grants")
        return value

    def admit(
        self,
        *,
        issue: int,
        repository: str,
        workspace: Path,
        branch: str,
        head_sha: str,
        checkpoint: str,
        lease_token: str,
        now: datetime | None = None,
    ) -> WorkspaceGrant:
        registry = self.read()
        raw = registry["grants"].get(str(issue))
        if not isinstance(raw, dict):
            raise WorkspaceAuthorityMismatch("unregistered issue")
        try:
            grant = WorkspaceGrant(**raw)
        except TypeError:
            raise WorkspaceAuthorityMismatch("invalid grant") from None
        grant.validate()
        if (
            grant.issue != issue
            or grant.repository != repository
            or Path(grant.workspace).resolve() != workspace.resolve()
            or grant.branch != branch
            or grant.head_sha != head_sha
            or grant.checkpoint != checkpoint
        ):
            raise WorkspaceAuthorityMismatch("workspace lineage differs")
        if not hmac.compare_digest(grant.lease_token_digest, hashlib.sha256(lease_token.encode()).hexdigest()):
            raise WorkspaceAuthorityMismatch("lease token")
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None or datetime.fromisoformat(grant.lease_expiry) <= instant:
            raise WorkspaceAuthorityMismatch("lease expired")
        return grant

    def write_cas(self, *, expected_revision: int | None, grants: dict[str, WorkspaceGrant]) -> int:
        if self.path.exists():
            old = self.read()
            if expected_revision != old["revision"]:
                raise WorkspaceAuthorityMismatch("stale registry revision")
            revision = old["revision"] + 1
        else:
            if expected_revision is not None:
                raise WorkspaceAuthorityMismatch("registry absent")
            revision = 1
        entries = {key: vars_grant(value) for key, value in grants.items()}
        if len({grant.workspace for grant in grants.values()}) != len(grants):
            raise WorkspaceAuthorityMismatch("workspace ownership conflict")
        for key, grant in grants.items():
            grant.validate()
            if key != str(grant.issue):
                raise WorkspaceAuthorityMismatch("issue key conflict")
        body = {"version": VERSION, "revision": revision, "grants": entries}
        document = {**body, "digest": _digest(body)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".workspace-registry-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(document, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return revision


def vars_grant(grant: WorkspaceGrant) -> dict[str, Any]:
    return {key: getattr(grant, key) for key in grant.__dataclass_fields__}
