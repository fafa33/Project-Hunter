"""GitHub interaction for the Hunter Governance Review gate.

The gate talks to GitHub exclusively through the ``gh`` CLI authenticated with
the repository-scoped ``GITHUB_TOKEN``. The gate never checks out or executes
code from the pull request under review: it reads PR metadata, changed files,
and the diff through the GitHub API, and publishes a commit status.

``GitHubRunner`` is a small protocol so tests can substitute a fake runner.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Protocol

from hunter_governance_review.contracts import ChangedFile, PullRequest

_PR_VIEW_FIELDS = (
    "number,title,body,state,isDraft,headRefName,headRefOid," "baseRefName,baseRefOid,mergeable,changedFiles,url"
)


class GitHubError(RuntimeError):
    """Raised when a GitHub query fails or returns unusable data."""


class GitHubRunner(Protocol):
    """Minimal surface the gate needs from GitHub."""

    repository: str

    def get_pull_request(self, number: int) -> PullRequest: ...
    def get_pull_files(self, number: int) -> list[ChangedFile]: ...
    def get_pull_diff(self, number: int) -> str: ...
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
            raise GitHubError(f"gh {' '.join(args)} failed ({completed.returncode}): {detail or 'no output'}")
        return completed.stdout

    def get_pull_request(self, number: int) -> PullRequest:
        raw = self._run(["pr", "view", str(number), "--json", _PR_VIEW_FIELDS])
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GitHubError(f"gh pr view returned malformed JSON for PR #{number}") from exc
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

    def get_pull_diff(self, number: int) -> str:
        return self._run(["pr", "diff", str(number)])

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


def truncate_diff(diff: str, limit: int) -> str:
    """Bound the diff size sent to the LLM, marking truncation explicitly."""
    if len(diff) <= limit:
        return diff
    return diff[:limit] + f"\n\n[DIFF TRUNCATED at {limit} characters]"
