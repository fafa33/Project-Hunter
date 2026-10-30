"""Owner-authorized, idempotent issue evidence comment writer (not code-write ingress).

Uses existing gh authentication; never retries platform policy denials or changes
channels after denial. Does not grant PR/merge authority.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Callable

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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--issue", required=True, type=int)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--body-file", required=True)
    args = parser.parse_args()
    from pathlib import Path

    print(publish(args.repo, args.issue, args.marker, Path(args.body_file).read_text()))


if __name__ == "__main__":
    main()
