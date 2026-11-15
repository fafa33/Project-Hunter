"""Common local writer pre-commit review finalization boundary.

Only supplied, verifiable judgement may mint a request. No invented reviewer
claims, and no optional-review failure may deadlock ordinary code commits.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import hunter_pre_ready_review as review


def _staged_review_head(root: Path) -> str:
    """An unreferenced commit object for the exact staged (indexed) tree.

    ``git write-tree`` serializes the current index -- the staged snapshot this
    pre-commit hook is about to commit -- without touching HEAD, the index, or
    the working tree. Wrapping that tree in a ``commit-tree`` object (parented
    at HEAD, never written to any ref) gives ``local_changes`` an exact-SHA
    "head" to diff against, so the finalized review binds to what is actually
    about to be committed rather than to the stale pre-commit HEAD. The object
    is plumbing only -- it is never pushed, never reachable from a branch, and
    is discarded as soon as this process exits -- so it is signed with
    ``--no-gpg-sign`` to avoid an interactive signing prompt for a commit that
    represents no authored history and that nobody will ever read or trust.
    """
    tree = subprocess.check_output(("git", "write-tree"), cwd=root, text=True).strip()
    parent = subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=root, text=True).strip()
    return subprocess.check_output(
        (
            "git",
            "commit-tree",
            "--no-gpg-sign",
            tree,
            "-p",
            parent,
            "-m",
            "hunter-writer-finalizer: staged-content review snapshot (not a ref, never pushed)",
        ),
        cwd=root,
        text=True,
    ).strip()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = os.environ.get("HUNTER_REVIEW_JUDGEMENT")
    issue = os.environ.get("HUNTER_GOVERNING_ISSUE")
    if not source and not issue:
        print(
            "[Hunter Writer Finalizer] SKIP: no authenticated judgement supplied; optional review cannot be fabricated"
        )
        return 0
    if not source or not issue:
        print("[Hunter Writer Finalizer] FAIL: both judgement and governing Issue are required", file=sys.stderr)
        return 2
    try:
        judgement = json.loads(review._trusted_judgement_path(source).read_text(encoding="utf-8"))
        if not isinstance(judgement, dict):
            raise ValueError("judgement must be a JSON object")
        base = subprocess.check_output(("git", "merge-base", "HEAD", "origin/main"), cwd=root, text=True).strip()
        head = _staged_review_head(root)
        changed = review.finalize_writer_review_request(
            issue=issue, base=base, head=head, base_ref="main", judgement=judgement, cwd=root
        )
        if changed:
            subprocess.run(("git", "add", "--", review.REVIEW_RELATIVE_PATH), cwd=root, check=True)
        print(
            "[Hunter Writer Finalizer] PASS: governed review request finalized"
            if changed
            else "[Hunter Writer Finalizer] PASS: unchanged governed review request"
        )
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError, review.GitEvidenceUnavailable) as exc:
        print(f"[Hunter Writer Finalizer] FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
