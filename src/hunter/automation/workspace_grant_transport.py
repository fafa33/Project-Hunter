"""Bounded public GitHub comment transport for signed, non-secret workspace grants.

GitHub comments are untrusted storage, never an authority. Only a grant signed by
repository-pinned Ed25519 authority and bound to the exact run may be consumed.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch, WorkspaceGrant
from hunter.automation.workspace_grant_handoff import verify_grant_handoff

_PREFIX = "hunter-workspace-grant-v1:"
_PATTERN = re.compile(r"^hunter-workspace-grant-v1:([A-Za-z0-9+/=]{1,11000})$")
_MAX_COMMENTS = 100


def comment_body(document: bytes) -> str:
    """Encode a non-secret signed grant, never a lease token or private key."""
    if len(document) > 8192:
        raise WorkspaceAuthorityMismatch("oversized grant document")
    return _PREFIX + base64.b64encode(document).decode("ascii")


def select_verified_grant(
    comments: list[dict[str, Any]],
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
    """Refuse missing, forged, expired, or ambiguous receipts; never trust comment author."""
    if len(comments) > _MAX_COMMENTS:
        raise WorkspaceAuthorityMismatch("comment transport bound exceeded")
    matching: list[WorkspaceGrant] = []
    for item in comments:
        body = item.get("body")
        if not isinstance(body, str) or not body.startswith(_PREFIX):
            continue
        match = _PATTERN.fullmatch(body)
        if match is None:
            raise WorkspaceAuthorityMismatch("malformed grant transport")
        try:
            document = base64.b64decode(match.group(1), validate=True)
            decoded = json.loads(document)
        except (ValueError, TypeError):
            raise WorkspaceAuthorityMismatch("invalid grant transport") from None
        if not isinstance(decoded, dict):
            raise WorkspaceAuthorityMismatch("invalid grant transport")
        # Other runs can share the Issue timeline. Their receipts are not candidates.
        if decoded.get("run_id") != run_id or decoded.get("authorization_id") != authorization_id:
            continue
        matching.append(
            verify_grant_handoff(
                document,
                verification_key=verification_key,
                authorization_id=authorization_id,
                run_id=run_id,
                run_attempt=run_attempt,
                repository=repository,
                issue=issue,
                branch=branch,
                base_sha=base_sha,
                checkpoint=checkpoint,
                now=now,
            )
        )
    if len(matching) != 1:
        raise WorkspaceAuthorityMismatch("missing or ambiguous signed grant transport")
    return matching[0]


def fetch_issue_grant_comments(*, repository: str, issue: int, token: str) -> list[dict[str, Any]]:
    """Read the bounded Issue timeline over GitHub TLS; fail closed on pagination."""
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}", repository) or issue <= 0:
        raise WorkspaceAuthorityMismatch("invalid repository/issue transport target")
    if not token:
        raise WorkspaceAuthorityMismatch("GitHub read credential missing")
    request = Request(
        f"https://api.github.com/repos/{repository}/issues/{issue}/comments?per_page=100&page=1",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            if response.status != 200 or response.headers.get("Link"):
                raise WorkspaceAuthorityMismatch("unbounded or failed GitHub comment read")
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise WorkspaceAuthorityMismatch("oversized GitHub comment response")
        comments = json.loads(raw)
        if not isinstance(comments, list) or len(comments) > _MAX_COMMENTS:
            raise WorkspaceAuthorityMismatch("invalid GitHub comment response")
        return comments
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        raise WorkspaceAuthorityMismatch("GitHub grant transport unavailable") from None


def publish_signed_grant_comment(*, repository: str, issue: int, token: str, document: bytes) -> None:
    """Trusted Mac publisher: transport a non-secret signed receipt, never grant authority."""
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}", repository) or issue <= 0 or not token:
        raise WorkspaceAuthorityMismatch("invalid grant publisher configuration")
    body = json.dumps({"body": comment_body(document)}).encode("utf-8")
    request = Request(
        f"https://api.github.com/repos/{repository}/issues/{issue}/comments",
        data=body,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            if response.status != 201:
                raise WorkspaceAuthorityMismatch("GitHub grant receipt publication failed")
    except (HTTPError, URLError, TimeoutError, OSError):
        raise WorkspaceAuthorityMismatch("GitHub grant receipt publication unavailable") from None


def verify_github_run(*, repository: str, run_id: int, token: str) -> None:
    """Refuse caller-invented runs: require a live, first-attempt main lifecycle run."""
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}", repository) or run_id <= 0 or not token:
        raise WorkspaceAuthorityMismatch("invalid workflow run lookup")
    request = Request(
        f"https://api.github.com/repos/{repository}/actions/runs/{run_id}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            if response.status != 200:
                raise WorkspaceAuthorityMismatch("GitHub workflow run unavailable")
            raw = response.read(32769)
        if len(raw) > 32768:
            raise WorkspaceAuthorityMismatch("oversized workflow run")
        run = json.loads(raw)
        if not isinstance(run, dict) or (
            run.get("id") != run_id
            or run.get("run_attempt") != 1
            or run.get("path", "").split("@")[0] != ".github/workflows/hunter-issue-agent-trigger.yml"
            or run.get("head_branch") != "main"
            or run.get("status") not in ("queued", "in_progress", "waiting", "pending", "requested")
            or run.get("repository", {}).get("full_name") != repository
        ):
            raise WorkspaceAuthorityMismatch("workflow run identity or status mismatch")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, AttributeError, TypeError):
        raise WorkspaceAuthorityMismatch("GitHub workflow run verification failed") from None
