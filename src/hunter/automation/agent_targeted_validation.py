"""Fail-closed targeted validation for governed Issue Agent provider output."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

_EXPECTED_HEAD_ENV = "HUNTER_AGENT_EXPECTED_HEAD"
_BRANCH_ENV = "HUNTER_AGENT_BRANCH"
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ValidationAdapterError(RuntimeError):
    """Raised when exact-head validation cannot be proven."""


def _required_env(name: str) -> str:
    import os

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


def _canonical_remote_url(repo: Path) -> str:
    remote = _git(repo, "remote", "get-url", "origin")
    if remote.startswith("git@github.com:"):
        repository = remote.removeprefix("git@github.com:").removesuffix(".git")
    else:
        parsed = urlsplit(remote)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValidationAdapterError("validation origin must be credential-free github.com")
        repository = parsed.path.strip("/").removesuffix(".git")
    if not _REPOSITORY_RE.fullmatch(repository):
        raise ValidationAdapterError("validation origin must name an owner/name GitHub repository")
    return f"https://github.com/{repository}.git"


def run() -> int:
    repo = Path.cwd().resolve()
    expected = _required_env(_EXPECTED_HEAD_ENV).lower()
    branch = _required_env(_BRANCH_ENV)
    if not (repo / ".git").exists():
        raise ValidationAdapterError("validation working directory is not a repository checkout")
    remote_url = _canonical_remote_url(repo)

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
