"""Owner-authorized, idempotent issue evidence comment writer (not code-write ingress).

Uses existing gh authentication; never retries platform policy denials or changes
channels after denial. Does not grant PR/merge authority.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

MARKER = re.compile(r"\A[a-zA-Z0-9][a-zA-Z0-9._:-]{7,127}\Z")


class EvidenceWriteError(RuntimeError):
    pass


def publish(repo: str, issue: int, marker: str, body: str, *, run: Callable = subprocess.run) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository")
    if issue < 1 or not MARKER.fullmatch(marker):
        raise ValueError("invalid issue or marker")
    if not body.strip():
        raise ValueError("empty evidence")
    tag = f"<!-- hunter-evidence:{marker} -->"
    endpoint = f"repos/{repo}/issues/{issue}/comments"

    def api(*args: str):
        result = run(["gh", "api", *args], capture_output=True, text=True, check=False)
        if result.returncode:
            raise EvidenceWriteError(
                "GitHub operation failed; inspect authenticated CLI and policy (no automatic channel fallback)"
            )
        return json.loads(result.stdout)

    def lookup():
        page = 1
        while True:
            comments = api(endpoint, "-f", "per_page=100", "-f", f"page={page}", "-X", "GET")
            matches = [x for x in comments if tag in x.get("body", "")]
            if len(matches) > 1:
                raise EvidenceWriteError("duplicate marker found; manual reconciliation required")
            if matches:
                return matches[0]["html_url"]
            if len(comments) < 100:
                return None
            page += 1
            if page > 10:
                raise EvidenceWriteError("comment history exceeds bounded scan")

    existing = lookup()
    if existing:
        return existing
    # Never blindly retry a POST: a lost response may mean the write succeeded.
    created = api(endpoint, "-X", "POST", "-f", f"body={tag}\n{body}")
    verified = lookup()
    if not verified or verified != created.get("html_url"):
        raise EvidenceWriteError("write not verified; reconcile before retry")
    return verified


def read_evidence_file(raw_path: str, *, root: Path | None = None) -> str:
    """Read only a regular, non-symlink evidence file beneath the work directory."""
    allowed = (root or Path.cwd()).resolve(strict=True)
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("evidence file must be a relative path within the working directory")
    # Open relative to the trusted directory, rejecting symlinks in every component.
    parts = candidate.parts
    if not parts or parts == (".",):
        raise ValueError("invalid evidence file")
    directory_fd = os.open(allowed, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            import stat

            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("evidence file must be regular")
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                fd = -1
                return stream.read()
        finally:
            if fd >= 0:
                os.close(fd)
    finally:
        os.close(directory_fd)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--issue", required=True, type=int)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--body-file", required=True)
    args = parser.parse_args()
    print(publish(args.repo, args.issue, args.marker, read_evidence_file(args.body_file)))


if __name__ == "__main__":
    main()
