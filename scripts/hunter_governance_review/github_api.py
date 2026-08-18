"""GitHub interaction for the Hunter Governance Review gate.

The gate talks to GitHub exclusively through the ``gh`` CLI authenticated with
the repository-scoped ``GITHUB_TOKEN``. The gate never checks out or executes
code from the pull request under review: it reads PR metadata and changed
files through the GitHub API, and publishes a commit status. No other
network service is ever contacted.

``GitHubRunner`` is a small protocol so tests can substitute a fake runner.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Protocol

import hunter_github_transport as transport

from hunter_governance_review.contracts import ChangedFile, PullRequest

_PR_VIEW_FIELDS = (
    "number,title,body,state,isDraft,headRefName,headRefOid," "baseRefName,baseRefOid,mergeable,changedFiles,url,author"
)

_GITHUB_APP_AUTHOR_ALIASES = {
    "app/dependabot": "dependabot[bot]",
    "app/renovate": "renovate[bot]",
}

_CLI_RETRY_POLICY = transport.RetryPolicy()

# Sleeper hook so tests can inject a deterministic fake instead of sleeping.
_sleeper = time.sleep


def _normalize_author_login(login: str) -> str:
    """Normalize GitHub CLI app-style author identities to canonical bot logins.

    ``gh pr view --json author`` represents GitHub App authors such as
    Dependabot as ``app/dependabot``. The REST API and Hunter's deterministic
    trust policy use the canonical bot login ``dependabot[bot]``. Normalize at
    the GitHub boundary so downstream governance does not depend on which
    GitHub API representation supplied the same authoritative actor identity.
    """

    normalized = login.strip().lower()
    return _GITHUB_APP_AUTHOR_ALIASES.get(normalized, normalized)


class GitHubError(RuntimeError):
    """Raised when a GitHub query fails or returns unusable data."""


class GitHubUnavailable(GitHubError):
    """Bounded retries exhausted; the GitHub infrastructure is unavailable.

    This is a data-acquisition/infrastructure outcome, never a governance
    verdict: the gate must not treat it as APPROVED, CHANGES REQUIRED, or a
    finding. Callers distinguish it from :class:`GitHubError` so publication
    and exit behavior can stay semantically accurate.
    """

    def __init__(self, what: str, *, attempts: int, last: GitHubError) -> None:
        super().__init__(f"{what} unavailable after {attempts} attempts: {last}")
        self.attempts = attempts
        self.last = last


class GitHubRunner(Protocol):
    """Minimal surface the gate needs from GitHub."""

    repository: str

    def get_pull_request(self, number: int) -> PullRequest: ...
    def get_pull_files(self, number: int) -> list[ChangedFile]: ...
    def get_file_content(self, path: str, ref: str) -> str | None: ...
    def list_directory(self, path: str, ref: str) -> list[str] | None: ...
    def post_commit_status(
        self,
        *,
        sha: str,
        state: str,
        context: str,
        description: str,
        target_url: str,
    ) -> None: ...


class GhCliRunner:
    """GitHub runner backed by the ``gh`` CLI (authenticated via GITHUB_TOKEN)."""

    def __init__(self, repository: str, *, token: str | None = None) -> None:
        self.repository = repository
        env = dict(os.environ)
        env["GH_REPO"] = repository
        if token:
            env["GH_TOKEN"] = token
        self._env = env

    def _run(self, args: list[str]) -> str:
        what = f"gh {' '.join(args)}"
        try:
            return transport.execute_with_retry(
                lambda: self._run_once(args),
                what=what,
                policy=_CLI_RETRY_POLICY,
                sleeper=_sleeper,
            )
        except transport.GitHubUnavailable as exc:
            raise GitHubUnavailable(what, attempts=exc.attempts, last=GitHubError(str(exc.last))) from exc
        except transport.GitHubRequestError as exc:
            raise GitHubError(str(exc)) from exc

    def _run_once(self, args: list[str]) -> str:
        try:
            completed = subprocess.run(
                ["gh", *args],
                capture_output=True,
                text=True,
                env=self._env,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitHubError(f"gh command timed out: gh {' '.join(args)}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise transport.classify_cli_failure(detail or "no output")
        return completed.stdout

    def get_pull_request(self, number: int) -> PullRequest:
        raw = self._run(["pr", "view", str(number), "--json", _PR_VIEW_FIELDS])
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GitHubError(f"gh pr view returned malformed JSON for PR #{number}") from exc
        author_payload = payload.get("author") or {}
        if isinstance(author_payload, dict):
            author_login = str(author_payload.get("login") or "")
        else:
            author_login = str(author_payload)
        author_login = _normalize_author_login(author_login)

        return PullRequest(
            number=int(payload["number"]),
            title=str(payload.get("title") or ""),
            body=str(payload.get("body") or ""),
            state=str(payload.get("state") or "open"),
            draft=bool(payload.get("isDraft") or payload.get("draft") or False),
            head_ref_name=str(payload.get("headRefName") or ""),
            head_oid=str(payload.get("headRefOid") or ""),
            base_ref_name=str(payload.get("baseRefName") or ""),
            base_oid=str(payload.get("baseRefOid") or ""),
            mergeable=payload.get("mergeable"),
            changed_files=int(payload.get("changedFiles") or 0),
            url=str(payload.get("url") or ""),
            author_login=author_login,
        )

    def get_pull_files(self, number: int) -> list[ChangedFile]:
        endpoint = f"repos/{self.repository}/pulls/{number}/files"
        raw = self._run(
            ["api", endpoint, "--paginate", "--jq", ".[] | [.filename, .status, .additions, .deletions] | @tsv"]
        )
        files: list[ChangedFile] = []
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            files.append(
                ChangedFile(
                    filename=parts[0],
                    status=parts[1],
                    additions=int(parts[2] or 0),
                    deletions=int(parts[3] or 0),
                )
            )
        return files

    def get_file_content(self, path: str, ref: str) -> str | None:
        """Fetch a file's exact content at ``ref`` via the Contents API.

        Returns ``None`` only when the file genuinely does not exist at that
        ref (a permanent, non-node-resolution HTTP 404) -- a fact, not an
        error. Transient GitHub failures are retried with the shared bounded
        policy and raise :class:`GitHubUnavailable` on exhaustion; a
        node-resolution 404 is never treated as "file missing". Any other
        failure raises ``GitHubError``, since the caller cannot distinguish
        "genuinely missing" from "could not be retrieved" otherwise.
        """
        endpoint = f"repos/{self.repository}/contents/{path}"
        what = f"gh api {endpoint}@{ref}"
        try:
            return transport.execute_with_retry(
                lambda: self._api_once_raw(endpoint, ref),
                what=what,
                policy=_CLI_RETRY_POLICY,
                sleeper=_sleeper,
            )
        except transport.GitHubUnavailable as exc:
            raise GitHubUnavailable(what, attempts=exc.attempts, last=GitHubError(str(exc.last))) from exc
        except transport.GitHubRequestError as exc:
            raise GitHubError(str(exc)) from exc

    def _api_once_raw(self, endpoint: str, ref: str) -> str | None:
        try:
            completed = subprocess.run(
                ["gh", "api", "-H", "Accept: application/vnd.github.raw", f"{endpoint}?ref={ref}"],
                capture_output=True,
                text=True,
                env=self._env,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitHubError(f"gh api {endpoint}@{ref} timed out") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip()
            classified = transport.classify_cli_failure(detail or "no output")
            if classified.category == "permanent" and classified.status_code == 404:
                return None
            raise classified
        return completed.stdout

    def list_directory(self, path: str, ref: str) -> list[str] | None:
        """List entry names of a directory at ``ref``, or ``None`` if it does not exist.

        ``None`` means a genuine permanent 404 only; transient failures are
        retried and a node-resolution 404 is never read as "directory absent".
        """
        endpoint = f"repos/{self.repository}/contents/{path}"
        what = f"gh api {endpoint}@{ref}"
        try:
            result = transport.execute_with_retry(
                lambda: self._api_once_list(endpoint, ref),
                what=what,
                policy=_CLI_RETRY_POLICY,
                sleeper=_sleeper,
            )
        except transport.GitHubUnavailable as exc:
            raise GitHubUnavailable(what, attempts=exc.attempts, last=GitHubError(str(exc.last))) from exc
        except transport.GitHubRequestError as exc:
            raise GitHubError(str(exc)) from exc
        if result is None:
            return None
        return [line for line in result.splitlines() if line]

    def _api_once_list(self, endpoint: str, ref: str) -> str | None:
        try:
            completed = subprocess.run(
                ["gh", "api", f"{endpoint}?ref={ref}", "--jq", ".[].name"],
                capture_output=True,
                text=True,
                env=self._env,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitHubError(f"gh api {endpoint}@{ref} timed out") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip()
            classified = transport.classify_cli_failure(detail or "no output")
            if classified.category == "permanent" and classified.status_code == 404:
                return None
            raise classified
        return completed.stdout

    def post_commit_status(
        self,
        *,
        sha: str,
        state: str,
        context: str,
        description: str,
        target_url: str,
    ) -> None:
        endpoint = f"repos/{self.repository}/statuses/{sha}"
        self._run(
            [
                "api",
                endpoint,
                "-f",
                f"state={state}",
                "-f",
                f"context={context}",
                "-f",
                f"description={description}",
                "-f",
                f"target_url={target_url}",
            ]
        )
