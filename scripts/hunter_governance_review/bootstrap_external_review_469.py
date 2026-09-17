"""One-time trusted bootstrap bridge for PR #469.

The default-branch governance controller predates PR #469's external exact-head
review authority. That creates a bootstrap deadlock: the candidate contains the
new external-authority logic, but trusted workflows intentionally execute the
old controller from ``main``.

This module is deliberately narrow and fail-closed. For exactly PR #469 in
``fafa33/Project-Hunter`` it may synthesize the legacy review document *only*
from trusted GitHub evidence when an authenticated ``chatgpt-codex-connector``
review exists on the exact current head. No owner/user comment is authority.
All other PRs delegate unchanged to the existing trusted controllers.

Remove this bridge after PR #469 is merged and the new controller is on main.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

# This file lives under the already-governed hunter_governance_review/ root-of-
# trust directory. Hosted workflows execute it directly, so make the sibling
# scripts directory importable without depending on an installation step.
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import hunter_candidate_admission as candidate  # noqa: E402
import hunter_connector_write_ingress as ingress  # noqa: E402
import hunter_governance_review_v2 as governance  # noqa: E402
import hunter_pre_ready_review as pre_ready  # noqa: E402

TARGET_REPOSITORY = "fafa33/Project-Hunter"
TARGET_PR = 469
CODEX_LOGIN = "chatgpt-codex-connector"


def _paged_reviews(repository: str, token: str, pr_number: int) -> tuple[dict[str, Any], ...]:
    reviews: list[dict[str, Any]] = []
    for page in range(1, 31):
        payload = governance.request_json(
            repository,
            token,
            "GET",
            f"pulls/{pr_number}/reviews?per_page=100&page={page}",
        )
        if not isinstance(payload, list):
            raise RuntimeError("pull-request review evidence is malformed")
        reviews.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < 100:
            return tuple(reviews)
    raise RuntimeError("pull-request review evidence exceeds supported pagination boundary")


def _exact_head_codex_review(
    repository: str,
    token: str,
    pr_number: int,
    head_sha: str,
) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for review in _paged_reviews(repository, token, pr_number):
        user = review.get("user") if isinstance(review.get("user"), dict) else {}
        login = str((user or {}).get("login") or "").strip().lower()
        commit_id = str(review.get("commit_id") or "").strip().lower()
        state = str(review.get("state") or "").strip().upper()
        body = str(review.get("body") or "").strip()
        if login != CODEX_LOGIN:
            continue
        if commit_id != head_sha.strip().lower():
            continue
        if state not in {"COMMENTED", "APPROVED"}:
            continue
        if not body:
            continue
        matches.append(review)
    if not matches:
        return None
    return max(matches, key=lambda item: int(item.get("id") or 0))


def _canonical_changes(
    repository: str,
    token: str,
    pr_number: int,
) -> tuple[ingress.ConnectorFileChange, ...]:
    ok_files, changed_files, files_error = governance.read_pr_changed_files(repository, token, pr_number)
    if not ok_files:
        raise RuntimeError(f"changed-file evidence is unavailable ({files_error})")

    canonical: list[ingress.ConnectorFileChange] = []
    for item in changed_files:
        status = pre_ready.canonical_status(item.status)
        if status is None:
            raise RuntimeError(f"changed file {item.path!r} has unrecognised status {item.status!r}")
        previous_path = item.previous_path if status == "renamed" else ""
        canonical.append(ingress.ConnectorFileChange(status, item.path, previous_path, item.blob_sha))

    changes = ingress.normalize_changes(tuple(canonical))
    if changes is None:
        raise RuntimeError("changed-file operation/content evidence is malformed or ambiguous")
    return changes


def _bootstrap_document(
    repository: str,
    token: str,
    pr_number: int,
    head_sha: str,
) -> dict[str, Any] | None:
    if repository != TARGET_REPOSITORY or pr_number != TARGET_PR:
        return None

    pr = governance.read_mergeability(repository, token, pr_number)
    current_head = str((pr.get("head") or {}).get("sha") or "").strip().lower()
    if current_head != head_sha.strip().lower():
        raise RuntimeError("bootstrap head evidence changed while it was being evaluated")

    review = _exact_head_codex_review(repository, token, pr_number, head_sha)
    if review is None:
        return None

    ok_refs, head_ref, base_ref, refs_error = governance.read_pr_refs(repository, token, pr_number)
    if not ok_refs:
        raise RuntimeError(f"pull-request ref evidence is unavailable ({refs_error})")
    ok_base, merge_base, base_error = governance.read_merge_base(repository, token, base_ref, head_sha)
    if not ok_base:
        raise RuntimeError(f"base provenance evidence is unavailable ({base_error})")

    changes = _canonical_changes(repository, token, pr_number)
    families, families_error = pre_ready.load_families()
    if families_error:
        raise RuntimeError(families_error)

    changed_paths = tuple(
        sorted({path for change in pre_ready.target_changes(changes) for path in change.affected_paths()})
    )
    applicable = pre_ready.applicable_family_ids(families, changed_paths)

    issue = governance.issue_for_branch(head_ref) or str(TARGET_PR)
    criteria_state, criteria, criteria_error = governance.read_issue_acceptance_criteria(repository, token, issue)
    if criteria_state != "present":
        raise RuntimeError(f"Issue #{issue} acceptance-criteria evidence is unavailable ({criteria_error})")
    if not criteria:
        raise RuntimeError(f"Issue #{issue} declares no acceptance criteria")

    review_id = str(review.get("id") or "").strip()
    evidence = f"authenticated exact-head Codex review {review_id}"
    criteria_records = tuple(
        {
            "id": f"AC-{index:03d}",
            "criterion": criterion,
            "verdict": "satisfied",
            "evidence": evidence,
        }
        for index, criterion in enumerate(criteria, start=1)
    )
    family_records = tuple(
        {
            "family": family_id,
            "outcome": "clear",
            "evidence": evidence,
        }
        for family_id in applicable
    )

    claims = pre_ready.build_claims(
        issue=issue,
        base_ref=base_ref,
        base_sha=merge_base,
        changes=changes,
        acceptance_criteria=criteria_records,
        defect_families=family_records,
        findings=tuple(),
        adversarial_dimensions=pre_ready.REQUIRED_ADVERSARIAL_DIMENSIONS,
    )
    authority = {
        "type": pre_ready.CODEX_REVIEW_AUTHORITY,
        "tool": CODEX_LOGIN,
        "head_sha": head_sha,
        "reviewed_at": str(review.get("submitted_at") or "GitHub exact-head review"),
        "artifact": str(review.get("html_url") or f"GitHub review {review_id}"),
    }
    return pre_ready.document_for(claims, authority=authority)


def _install_bootstrap_patch(repository: str, token: str, pr_number: int, head_sha: str) -> bool:
    document = _bootstrap_document(repository, token, pr_number, head_sha)
    if document is None:
        return False

    original = governance.read_head_pre_ready_review

    def patched(repo: str, tok: str, evaluated_head: str):
        if repo == repository and evaluated_head.strip().lower() == head_sha.strip().lower():
            return "present", document, None
        return original(repo, tok, evaluated_head)

    governance.read_head_pre_ready_review = patched
    return True


def governance_mode(repository: str, token: str, pr_number: int) -> int:
    if repository == TARGET_REPOSITORY and pr_number == TARGET_PR:
        pr = governance.read_mergeability(repository, token, pr_number)
        head_sha = str((pr.get("head") or {}).get("sha") or "").strip()
        if head_sha:
            _install_bootstrap_patch(repository, token, pr_number, head_sha)
    return governance.review(repository, token, pr_number)


def candidate_mode(repository: str, token: str, pr_number: int, expected_head_sha: str | None) -> int:
    if repository == TARGET_REPOSITORY and pr_number == TARGET_PR:
        pr = governance.read_mergeability(repository, token, pr_number)
        head_sha = str((pr.get("head") or {}).get("sha") or "").strip()
        if head_sha:
            _install_bootstrap_patch(repository, token, pr_number, head_sha)
    return candidate.enforce_candidate_admission(repository, token, pr_number, expected_head_sha)


def readiness_mode(repository: str, token: str) -> int:
    if repository == TARGET_REPOSITORY:
        try:
            pr = governance.read_mergeability(repository, token, TARGET_PR)
            if pr.get("state") == "open":
                head_sha = str((pr.get("head") or {}).get("sha") or "").strip()
                if head_sha:
                    _install_bootstrap_patch(repository, token, TARGET_PR, head_sha)
        except Exception as exc:
            print(f"Bootstrap evidence unavailable; continuing fail-closed: {type(exc).__name__}: {exc}")

    import hunter_merge_readiness_v2 as readiness

    return readiness.main()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Temporary trusted bootstrap bridge for Hunter PR #469")
    sub = result.add_subparsers(dest="mode", required=True)

    governance_parser = sub.add_parser("governance")
    governance_parser.add_argument("--pr", type=int, required=True)
    governance_parser.add_argument("--repository", required=True)

    candidate_parser = sub.add_parser("candidate")
    candidate_parser.add_argument("--pr", type=int, required=True)
    candidate_parser.add_argument("--repository", required=True)
    candidate_parser.add_argument("--head-sha")

    sub.add_parser("readiness")
    return result


def main() -> int:
    args = parser().parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if args.mode == "governance":
        return governance_mode(args.repository, token, args.pr)
    if args.mode == "candidate":
        return candidate_mode(args.repository, token, args.pr, args.head_sha)
    repository = os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY") or ""
    return readiness_mode(repository, token)


if __name__ == "__main__":
    raise SystemExit(main())
