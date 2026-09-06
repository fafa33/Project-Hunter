"""The repository-owned push boundary.

This boundary is where Issue #412 requires provenance and evidence-lifecycle
defects to be caught: *before* any remote mutation, with an actionable
diagnosis, rather than after a hosted push has already created a state that
needs a rewind to escape.

Issue #415 sharpened what that means. The boundary owns exactly the defects
whose discovery *after* publication would force a history rewrite or a
force-push -- commit identity, a stale authorization receipt, a ref that is not
the checked-out head -- plus the cheap deterministic gates, which fail fast and
cost seconds. It does not own the full repository test suite: a failing test is
repaired by the next commit with no rewind, so paying nine minutes here to
learn ten minutes early was duplicating, not protecting. The authoritative
exact-head full repository proof belongs to the hosted branch preflight, which
is the evidence trusted candidate admission has always actually required and
which no writer on any channel can mint. ``docs/VALIDATION_STAGE_CONTRACT.json``
is the machine-readable form of that ownership split.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import hunter_connector_write_ingress as ingress
import hunter_pr_preflight as preflight
import hunter_pre_ready_review as review
import hunter_validation_receipt as receipts
import hunter_writer_provenance as provenance

ZERO_SHA = "0" * 40
NORMAL_MODE = "normal"
TESTS_FIRST_RED_MODE = "tests-first-red"
MODE_MARKER = Path(".hunter-preflight-mode")


def _run_git(*args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise RuntimeError(detail)
    return completed.stdout.strip()


def _parse_updates(lines: Iterable[str]) -> list[tuple[str, str, str]]:
    updates: list[tuple[str, str, str]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 4:
            raise ValueError("malformed pre-push ref update")
        local_ref, local_sha, remote_ref, _remote_sha = parts
        if local_sha == ZERO_SHA:
            continue
        updates.append((local_ref, local_sha, remote_ref))
    return updates


def _require_clean_tree() -> None:
    if _run_git("status", "--porcelain=v1", "--untracked-files=normal"):
        raise RuntimeError("working tree must be clean before push preflight")


def _require_exact_head(updates: list[tuple[str, str, str]], head_sha: str) -> None:
    branch_updates = [
        (remote_ref, local_sha) for _local_ref, local_sha, remote_ref in updates if remote_ref.startswith("refs/heads/")
    ]
    if not branch_updates:
        return
    mismatched = [(remote_ref, local_sha) for remote_ref, local_sha in branch_updates if local_sha != head_sha]
    if mismatched:
        refs = ", ".join(remote_ref for remote_ref, _local_sha in mismatched)
        raise RuntimeError(
            "pre-push enforcement only authorizes the checked-out exact HEAD; "
            f"checkout the branch being pushed first: {refs}"
        )


def _committed_marker(head_sha: str) -> str | None:
    marker_spec = f"{head_sha}:{MODE_MARKER.as_posix()}"
    exists = subprocess.run(
        ("git", "cat-file", "-e", marker_spec),
        check=False,
        capture_output=True,
        text=True,
    )
    if exists.returncode != 0:
        return None
    return _run_git("show", marker_spec)


def _select_preflight_mode(head_sha: str) -> str:
    raw = _committed_marker(head_sha)
    if raw is None:
        return NORMAL_MODE
    mode = raw.rstrip("\n")
    if mode != TESTS_FIRST_RED_MODE:
        raise RuntimeError("committed .hunter-preflight-mode must contain exactly tests-first-red")
    return mode


def _preflight_command(mode: str) -> tuple[str, ...]:
    return ("python", "scripts/hunter_pr_preflight.py", "--mode", mode)


def _run_push_safety_lane() -> int:
    """Run the deterministic gates that must pass before any network mutation.

    Every gate here is cheap and fails fast, so a formatting or registry defect
    is reported in seconds instead of behind ten minutes of tests. The full
    repository suite is deliberately not among them; see the module docstring.
    """
    return preflight.run_quality_gates(preflight.PUSH_SAFETY_GATES)


def _lane_label(mode: str) -> str:
    if mode == TESTS_FIRST_RED_MODE:
        return "canonical tests-first-red preflight"
    return "push-safety lane"


def report_full_repository_proof_ownership(repo_root: Path, head_sha: str, mode: str) -> None:
    """Say who owns the full repository proof for this head -- and never re-run it.

    A recorded local receipt for this exact identity is reported rather than
    repeated: Issue #415 forbids validating one immutable candidate with the
    same suite twice. Without one, the hosted exact-head branch preflight owns
    that proof and trusted candidate admission fails closed until it lands, so
    nothing is being skipped here -- only moved to the boundary that owns it.
    """

    if mode == TESTS_FIRST_RED_MODE:
        print(
            "[Hunter Pre-Push] DRAFT-ONLY: tests-first-red proves a declared RED result, "
            "which is never full repository proof."
        )
        return

    try:
        blocker = receipts.reuse_blocker(repo_root, head_sha=head_sha)
    except Exception as exc:  # noqa: BLE001 - reporting must never block an otherwise authorized push
        print(f"[Hunter Pre-Push] NOTE: full repository proof ownership is unreportable ({exc})")
        return
    if blocker is None:
        print(
            f"[Hunter Pre-Push] LOCAL-FULL-PROOF: a recorded receipt already covers exact HEAD {head_sha}; "
            "the full lane is not re-run."
        )
        return
    print(
        f"[Hunter Pre-Push] HOSTED-OWNS-FULL-PROOF: {blocker}. "
        "Hunter / Pre-PR Preflight validates this exact head and candidate admission fails closed without it."
    )


def _validate_writer_provenance(head_sha: str) -> None:
    """Refuse to publish a range whose commit identity is not authorization-bound.

    The mismatch PR #411 hit -- commits recorded under an implementation agent's
    Git identity instead of the authorization-bound writer -- is only repairable
    by rewriting the commits, so discovering it after the push is what forces a
    rewind. Checked here, the repair is local and free.
    """

    problem = provenance.check_range(head_sha)
    if problem is not None:
        raise RuntimeError(problem)


def _validate_receipt_freshness(head_sha: str) -> None:
    """The connector receipt is the final mutation, so a stale one blocks the push.

    Applicable only to a receipt *this candidate wrote*. A merged connector
    contribution leaves its receipt on the base branch, so every later candidate
    inherits the file at its head; reading mere presence as a claim about the
    current range would refuse every ordinary push for a receipt belonging to
    already-merged work. When the candidate does write one, its bound change set
    must still describe the exact governed range: any content edited after the
    receipt was minted invalidates it, and pushing it would publish claims that
    trusted admission will reject anyway.
    """

    try:
        base = provenance.resolve_governed_base(head_sha)
        changes = review.local_changes(base, head_sha)
    except (provenance.GitEvidenceUnavailable, review.GitEvidenceUnavailable) as exc:
        raise RuntimeError(f"receipt freshness evidence is unavailable ({exc})") from exc

    # Both governance-evidence artifacts sit outside the receipt's authorized
    # content set, matching what the trusted controller re-derives at admission.
    evidence_paths = {ingress.AUTHORIZATION_RECEIPT_PATH, review.REVIEW_RELATIVE_PATH}
    renamed_onto_evidence = sorted(
        change.previous_path for change in changes if change.status == "renamed" and change.path in evidence_paths
    )
    if renamed_onto_evidence:
        # Mirrors what trusted admission refuses. Without it, renaming a file
        # onto an evidence path would remove it from the receipt-bound set and
        # carry its disappearance out of the governed range unnoticed.
        raise RuntimeError(
            "a governance evidence path may not be used as a rename destination: " + ", ".join(renamed_onto_evidence)
        )

    written = [
        change
        for change in changes
        if change.path == ingress.AUTHORIZATION_RECEIPT_PATH and change.status in {"added", "modified"}
    ]
    if not written:
        # Either the candidate does not touch the receipt at all, or it retires
        # one inherited from the base branch. Retiring is not a claim about this
        # range, so there is nothing to hold to the range; a connector candidate
        # that removes its own receipt is refused by trusted admission instead,
        # which requires a namespace candidate to carry one.
        return

    receipt_path = Path(ingress.AUTHORIZATION_RECEIPT_PATH)
    if not receipt_path.is_file():
        raise RuntimeError(
            f"{ingress.AUTHORIZATION_RECEIPT_PATH} is in this candidate's change set but missing from the tree"
        )
    try:
        document = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{ingress.AUTHORIZATION_RECEIPT_PATH} is unreadable ({type(exc).__name__}: {exc})") from exc

    authorization, parse_error = ingress.ConnectorWriteAuthorization.from_document(document)
    if authorization is None:
        raise RuntimeError(f"{ingress.AUTHORIZATION_RECEIPT_PATH} is not a valid receipt ({parse_error})")

    governed = tuple(change for change in changes if change.path not in evidence_paths)
    if ingress.normalize_changes(governed) != ingress.normalize_changes(authorization.changes):
        raise RuntimeError(
            f"{ingress.AUTHORIZATION_RECEIPT_PATH} is stale: content changed after the receipt was minted. "
            "Regenerate the receipt as the final mutation, after code, tests, formatting and review fixes."
        )


def report_pre_ready_review_state(head_sha: str) -> None:
    """Report, without blocking, whether this head could stand as Ready.

    Pushing an incomplete candidate to a Draft pull request is ordinary work, so
    the push boundary does not block on review state. Ready does: trusted
    candidate admission refuses an unreviewed or stale head. Saying so here is
    what stops "mark Ready" from being the moment the first hostile review is
    discovered to be missing.
    """

    try:
        base = provenance.resolve_governed_base(head_sha)
    except provenance.GitEvidenceUnavailable as exc:
        print(f"[Hunter Pre-Push] NOTE: pre-ready review state is unknown ({exc})")
        return
    verdict = review.verify_local(base, head_sha)
    if verdict.ok:
        print(f"[Hunter Pre-Push] READY-ELIGIBLE: {verdict.reason}")
    else:
        print(
            f"[Hunter Pre-Push] DRAFT-ONLY: pre-ready hostile review is {verdict.state} ({verdict.reason}). "
            "Ready is blocked until it is recorded against this exact head."
        )


def enforce_pre_push(lines: Iterable[str]) -> int:
    updates = _parse_updates(lines)
    if not updates:
        return 0

    repo_root = Path(_run_git("rev-parse", "--show-toplevel")).resolve()
    os.chdir(repo_root)
    before_head = _run_git("rev-parse", "HEAD")
    _require_clean_tree()
    _require_exact_head(updates, before_head)
    _validate_writer_provenance(before_head)
    _validate_receipt_freshness(before_head)
    mode = _select_preflight_mode(before_head)

    if mode == TESTS_FIRST_RED_MODE:
        returncode = subprocess.run(_preflight_command(mode), check=False).returncode
    else:
        returncode = _run_push_safety_lane()
    if returncode != 0:
        print(
            f"[Hunter Pre-Push] BLOCKED: {_lane_label(mode)} exited {returncode}",
            file=sys.stderr,
        )
        return returncode or 1

    after_head = _run_git("rev-parse", "HEAD")
    if after_head != before_head:
        print("[Hunter Pre-Push] BLOCKED: HEAD changed during preflight", file=sys.stderr)
        return 2
    try:
        _require_clean_tree()
        _require_exact_head(updates, after_head)
    except RuntimeError as error:
        print(f"[Hunter Pre-Push] BLOCKED: {error}", file=sys.stderr)
        return 2

    report_full_repository_proof_ownership(repo_root, after_head, mode)
    report_pre_ready_review_state(after_head)
    print(f"[Hunter Pre-Push] PASS: exact HEAD {after_head} passed the {_lane_label(mode)}")
    return 0


def main() -> int:
    try:
        return enforce_pre_push(sys.stdin)
    except (RuntimeError, ValueError) as error:
        print(f"[Hunter Pre-Push] BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
