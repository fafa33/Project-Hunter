"""Fail-closed targeted validation for governed Issue Agent provider output."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_REPO_ENV = "HUNTER_ISSUE_AGENT_REPO_DIR"
_REPOSITORY_ENV = "HUNTER_ISSUE_AGENT_REPOSITORY"
_EXPECTED_HEAD_ENV = "HUNTER_AGENT_EXPECTED_HEAD"
_BRANCH_ENV = "HUNTER_AGENT_BRANCH"
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ValidationAdapterError(RuntimeError):
    """Raised when exact-head validation cannot be proven."""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValidationAdapterError(f"{name} is required")
    return value


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise ValidationAdapterError(detail)
    return completed.stdout.strip()


def _canonical_remote_url() -> str:
    repository = _required_env(_REPOSITORY_ENV)
    if not _REPOSITORY_RE.fullmatch(repository):
        raise ValidationAdapterError("configured repository must be an owner/name GitHub repository")
    return f"https://github.com/{repository}.git"


def run() -> int:
    repo = Path(_required_env(_REPO_ENV)).resolve()
    expected = _required_env(_EXPECTED_HEAD_ENV).lower()
    branch = _required_env(_BRANCH_ENV)
    remote_url = _canonical_remote_url()
    if not repo.is_dir():
        raise ValidationAdapterError("configured repository checkout does not exist")

    if _git(repo, "branch", "--show-current") != branch:
        raise ValidationAdapterError("validation checkout is not on the authorized execution branch")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=normal"):
        raise ValidationAdapterError("validation requires a clean working tree")

    local_head = _git(repo, "rev-parse", "HEAD").lower()
    if local_head != expected:
        raise ValidationAdapterError("local HEAD does not match fallback expected HEAD")

    remote_line = _git(repo, "ls-remote", "--exit-code", remote_url, f"refs/heads/{branch}")
    fields = remote_line.split()
    if len(fields) != 2 or fields[0].lower() != expected:
        raise ValidationAdapterError("GitHub-visible branch HEAD does not match fallback expected HEAD")

    completed = subprocess.run(
        (sys.executable, "scripts/hunter_pr_preflight.py", "--reuse-receipt"),
        cwd=repo,
        check=False,
        timeout=900,
    )
    return completed.returncode


def main() -> int:
    try:
        return run()
    except (OSError, subprocess.TimeoutExpired, ValidationAdapterError) as error:
        print(f"Hunter targeted validation failed closed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
