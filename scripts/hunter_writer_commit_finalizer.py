"""Common local writer pre-commit review finalization boundary.

Only supplied, verifiable judgement may mint a request. No invented reviewer
claims, and no optional-review failure may deadlock ordinary code commits.

Issue #585: a governed candidate that already carries a committed review
request must not go stale silently. When a later commit changes the candidate
content and the staged request no longer describes it, the finalizer fails the
commit closed. The committed request's own claims are never reused as a
judgement for changed candidate content: only a freshly substantiated trusted
judgement, supplied by the writer through ``HUNTER_REVIEW_JUDGEMENT`` and
``HUNTER_GOVERNING_ISSUE``, may mint the refreshed exact-head request, and that
mint is still re-verified live by ``finalize_writer_review_request``
(governing-Issue criteria coverage and exact defect-family applicability are
recomputed from trusted evidence). An unusable or wrong-Issue judgement is
refused, and the staleness check reads the review artifact from git blobs --
never from unstaged working-tree edits, which are neither consumed as claims
nor staged by this boundary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

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


def _staged_review_document(root: Path) -> Any:
    """The review artifact exactly as staged -- the content this commit publishes.

    Read from the index's own git blob, never from the working tree: unstaged
    edits to the review document are invisible to this boundary, are never
    consumed as claims, and are never staged by it. ``None`` means no review
    artifact is staged at all, i.e. an ordinary commit that optional review
    cannot block. A staged artifact that does not parse as JSON returns a
    non-dict placeholder so it can only fail verification, never pass it.
    """

    try:
        raw = subprocess.check_output(
            ("git", "show", f":{review.REVIEW_RELATIVE_PATH}"), cwd=root, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", errors="replace")


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
    """Refuse a governed commit whose staged review request no longer verifies.

    Engages only when a governed range exists and the staged request no longer
    describes the staged content. The committed request's own claims are never
    reused as a judgement for changed candidate content -- the commit fails
    closed until the writer supplies a freshly substantiated trusted judgement
    through ``HUNTER_REVIEW_JUDGEMENT`` and ``HUNTER_GOVERNING_ISSUE``. An
    already-current request needs no live verification and never deadlocks an
    offline commit, and a commit that stages no review artifact at all stays
    optional and non-blocking.
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
    document = _staged_review_document(root)
    verdict = review.verify_review_request(document, base_sha=base, changes=changes, families=families, head_sha=head)
    if verdict.ok or verdict.state == "awaiting-trusted-review-policy":
        print("[Hunter Writer Finalizer] PASS: unchanged governed review request")
        return 0
    if document is None:
        print("[Hunter Writer Finalizer] SKIP: no governed review request is staged")
        return 0
    # An unchanged artifact inherited from origin/main belongs to a previous
    # candidate. It imposes no review obligation on this new candidate.
    base_request = subprocess.run(
        ("git", "show", f"{base}:{review.REVIEW_RELATIVE_PATH}"),
        cwd=root,
        capture_output=True,
        check=False,
    )
    staged_request = subprocess.run(
        ("git", "show", f":{review.REVIEW_RELATIVE_PATH}"),
        cwd=root,
        capture_output=True,
        check=False,
    )
    if base_request.returncode == 0 and staged_request.returncode == 0 and base_request.stdout == staged_request.stdout:
        print("[Hunter Writer Finalizer] SKIP: inherited unchanged base review request")
        return 0
    print(
        f"[Hunter Writer Finalizer] FAIL: the staged governed review request no longer describes the staged "
        f"candidate content ({verdict.state}: {verdict.reason}); the committed request's own claims are never "
        f"reused as a judgement for changed content. Supply HUNTER_REVIEW_JUDGEMENT and HUNTER_GOVERNING_ISSUE "
        f"with a freshly substantiated judgement for the governing Issue and this exact staged head, then retry.",
        file=sys.stderr,
    )
    return 2


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
