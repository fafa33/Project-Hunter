"""Common local writer pre-commit review finalization boundary.

Only supplied, verifiable judgement may mint a request. No invented reviewer
claims, and no optional-review failure may deadlock ordinary code commits.

Issue #585: a governed candidate that already carries a committed review
request must not go stale silently. When a later commit changes the candidate
content and no fresh authenticated judgement is supplied, the finalizer
automatically re-mints the exact-head request from the committed request's own
claims. A review request carries no review authority, so this fabricates
nothing; every claim is still re-verified live by
``finalize_writer_review_request`` (governing-Issue criteria coverage and exact
defect-family applicability are recomputed from trusted evidence), and an
unusable or wrong-Issue request is refused instead of refreshed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import hunter_pre_ready_review as review

#: The committed request's claim lists an automatic refresh may reuse verbatim.
#: Everything binding is re-derived from git, and everything authoritative is
#: re-verified from live evidence; none of this is review authority.
REQUEST_CLAIM_KEYS = ("acceptance_criteria", "defect_families", "findings", "adversarial_dimensions")
OPTIONAL_REQUEST_CLAIM_KEYS = ("coverage_scope", "review_eligibility")


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


def _previous_request(root: Path) -> dict[str, Any] | None:
    """The committed request's own claims -- the only source an automatic refresh may reuse."""

    try:
        document = review.read_review_document(root / review.REVIEW_RELATIVE_PATH)
    except review.GitEvidenceUnavailable:
        return None
    if not isinstance(document, dict) or document.get("schema") != review.REVIEW_SCHEMA:
        return None
    request = document.get("review_request")
    claims = document.get("claims")
    if not (isinstance(request, dict) and request.get("schema") == "hunter.review-request.v1"):
        return None
    if not isinstance(claims, dict):
        return None
    issue = claims.get("issue")
    if not isinstance(issue, str) or not issue.isdecimal():
        return None
    if any(not isinstance(claims.get(key), list) for key in REQUEST_CLAIM_KEYS):
        return None
    judgement: dict[str, Any] = {key: claims[key] for key in REQUEST_CLAIM_KEYS}
    for key in OPTIONAL_REQUEST_CLAIM_KEYS:
        if isinstance(document.get(key), dict):
            judgement[key] = document[key]
    # Bind the refresh to the request's own recorded governing Issue: a refresh
    # must never silently carry the request across to another Issue.
    judgement["issue"] = issue
    return judgement


def _finalize(root: Path, *, issue: str, judgement: dict[str, Any]) -> int:
    try:
        base = subprocess.check_output(("git", "merge-base", "HEAD", "origin/main"), cwd=root, text=True).strip()
        head = _staged_review_head(root)
        changed = review.finalize_writer_review_request(
            issue=issue, base=base, head=head, base_ref="main", judgement=judgement, cwd=root
        )
        if changed:
            subprocess.run(("git", "add", "--", review.REVIEW_RELATIVE_PATH), cwd=root, check=True)
    except (OSError, ValueError, subprocess.CalledProcessError, review.GitEvidenceUnavailable) as exc:
        print(f"[Hunter Writer Finalizer] FAIL: {exc}", file=sys.stderr)
        return 2
    print(
        "[Hunter Writer Finalizer] PASS: governed review request finalized"
        if changed
        else "[Hunter Writer Finalizer] PASS: unchanged governed review request"
    )
    return 0


def _automatic_refresh(root: Path) -> int:
    """Refresh a governed candidate's committed request when its content changed.

    Engages only when a governed range exists and the committed request no
    longer describes the staged content; an already-current request needs no
    live verification and never deadlocks an offline commit.
    """

    try:
        base = subprocess.check_output(("git", "merge-base", "HEAD", "origin/main"), cwd=root, text=True).strip()
        head = _staged_review_head(root)
        changes = review.local_changes(base, head, cwd=root)
    except (OSError, ValueError, subprocess.CalledProcessError, review.GitEvidenceUnavailable) as exc:
        print(f"[Hunter Writer Finalizer] NOTE: automatic review freshness is unknown ({type(exc).__name__}: {exc})")
        return 0
    families, families_error = review.load_families(root / review.REGISTRY_RELATIVE_PATH)
    if families_error:
        print(f"[Hunter Writer Finalizer] NOTE: automatic review freshness is unknown ({families_error})")
        return 0
    try:
        document = review.read_review_document(root / review.REVIEW_RELATIVE_PATH)
    except review.GitEvidenceUnavailable:
        document = None
    verdict = review.verify_review_request(document, base_sha=base, changes=changes, families=families, head_sha=head)
    if verdict.ok or verdict.state == "awaiting-trusted-review-policy":
        print("[Hunter Writer Finalizer] PASS: unchanged governed review request")
        return 0
    previous = _previous_request(root)
    if previous is None:
        print(
            "[Hunter Writer Finalizer] SKIP: no authenticated judgement supplied; optional review cannot be fabricated"
        )
        return 0
    issue = previous.pop("issue")
    assert isinstance(issue, str)  # noqa: S101 - narrowed by _previous_request
    print(
        f"[Hunter Writer Finalizer] AUTO-REFRESH: committed review request no longer describes the staged "
        f"candidate content; regenerating the exact-head request for governing Issue #{issue} from its own claims"
    )
    return _finalize(root, issue=issue, judgement=previous)


def main(root: Path | None = None) -> int:
    resolved = root or Path(__file__).resolve().parents[1]
    source = os.environ.get("HUNTER_REVIEW_JUDGEMENT")
    issue = os.environ.get("HUNTER_GOVERNING_ISSUE")
    if not source and not issue:
        return _automatic_refresh(resolved)
    if not source or not issue:
        print("[Hunter Writer Finalizer] FAIL: both judgement and governing Issue are required", file=sys.stderr)
        return 2
    try:
        judgement = json.loads(review._trusted_judgement_path(source).read_text(encoding="utf-8"))
        if not isinstance(judgement, dict):
            raise ValueError("judgement must be a JSON object")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"[Hunter Writer Finalizer] FAIL: {exc}", file=sys.stderr)
        return 2
    return _finalize(resolved, issue=issue, judgement=judgement)


if __name__ == "__main__":
    raise SystemExit(main())
