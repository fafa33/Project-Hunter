"""Fail-closed client of the single private Issue #575 workspace authority."""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch, WorkspaceGrant, vars_grant


class WorkspaceAuthorityClient:
    def __init__(self, environment: Mapping[str, str], *, writer: bool = False) -> None:
        endpoint = environment.get("HUNTER_WORKSPACE_API_URL", "")
        token = environment.get("HUNTER_WORKSPACE_WRITER_TOKEN" if writer else "HUNTER_WORKSPACE_READER_TOKEN", "")
        parsed = urllib.parse.urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ("127.0.0.1", "localhost")
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or not parsed.port
            or len(token) < 32
        ):
            raise WorkspaceAuthorityMismatch("private workspace API configuration")
        self.url = f"http://127.0.0.1:{parsed.port}"
        self.token = token

    def _request(self, method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.url + path,
            method=method,
            data=data,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                result = json.loads(response.read(16384))
                if not isinstance(result, dict):
                    raise WorkspaceAuthorityMismatch("authority response schema")
                return result
        except (OSError, ValueError, urllib.error.URLError):
            raise WorkspaceAuthorityMismatch("authority API unavailable or refused") from None

    def issue_exclusive(self, grant: WorkspaceGrant) -> None:
        self._request("POST", "/v1/grants", vars_grant(grant))

    def renew_owned_lease(self, *, issue: int, owner: str, lease_token: str, now: datetime) -> None:
        self._request(
            "POST",
            "/v1/leases/renew",
            {"issue": issue, "owner": owner, "lease_token": lease_token, "now": now.isoformat()},
        )

    def read_grant(self, issue: int) -> WorkspaceGrant:
        response = self._request("GET", f"/v1/grants/{issue}")
        try:
            grant = WorkspaceGrant(**response["grant"])
            grant.validate()
            return grant
        except (TypeError, ValueError, KeyError):
            raise WorkspaceAuthorityMismatch("invalid authority grant") from None

    def admit(
        self,
        *,
        issue: int,
        repository: str,
        workspace: Path,
        branch: str,
        head_sha: str,
        base_sha: str,
        checkpoint: str,
        lease_token: str,
    ) -> WorkspaceGrant:
        grant = self.read_grant(issue)
        if (
            grant.issue != issue
            or grant.repository != repository
            or Path(grant.workspace).resolve() != workspace.resolve()
            or grant.branch != branch
            or grant.head_sha != head_sha
            or grant.base_sha != base_sha
            or grant.checkpoint != checkpoint
        ):
            raise WorkspaceAuthorityMismatch("workspace lineage differs")
        self._lease(grant, lease_token)
        return grant

    def admit_publication(
        self, *, issue: int, repository: str, branch: str, base_sha: str, checkpoint: str, lease_token: str
    ) -> WorkspaceGrant:
        grant = self.read_grant(issue)
        if (
            grant.issue != issue
            or grant.repository != repository
            or grant.branch != branch
            or grant.base_sha != base_sha
            or grant.checkpoint != checkpoint
        ):
            raise WorkspaceAuthorityMismatch("publication lineage differs")
        self._lease(grant, lease_token)
        return grant

    @staticmethod
    def _lease(grant: WorkspaceGrant, token: str) -> None:
        if not hmac.compare_digest(
            grant.lease_token_digest, hashlib.sha256(token.encode()).hexdigest()
        ) or datetime.fromisoformat(grant.lease_expiry) <= datetime.now(UTC):
            raise WorkspaceAuthorityMismatch("lease expired or token mismatch")
