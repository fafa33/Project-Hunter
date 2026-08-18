from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

CODERABBIT_AUTHORS = {"coderabbitai", "coderabbitai[bot]"}
CODERABBIT_STATUS_CONTEXT = "coderabbit"
EXACT_PAIR_RE = re.compile(
    r"Reviewing files that changed from the base of the PR and between\s+"
    r"(?P<base>[0-9a-f]{40})\s+and\s+(?P<head>[0-9a-f]{40})\.?",
    re.IGNORECASE,
)


class AdapterError(RuntimeError):
    """A deterministic CodeRabbit hostile-review adapter failure."""


@dataclass(frozen=True)
class QualifiedReview:
    review_id: int
    html_url: str
    submitted_at: str
    author: str
    state: str
    commit_id: str
    base_sha: str
    head_sha: str


def _login(review: Mapping[str, Any]) -> str:
    return str((review.get("user") or {}).get("login") or "").strip()


def _exact_pair(body: str) -> tuple[str, str] | None:
    matches = list(EXACT_PAIR_RE.finditer(body))
    if not matches:
        return None
    unique = {(m.group("base").lower(), m.group("head").lower()) for m in matches}
    if len(unique) != 1:
        raise AdapterError("CodeRabbit review body contains conflicting exact-pair claims.")
    return next(iter(unique))


def _pr_exact_pair(pr: Mapping[str, Any]) -> tuple[str, str]:
    head_sha = str((pr.get("head") or {}).get("sha") or "").strip().lower()
    base_sha = str((pr.get("base") or {}).get("sha") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha) or not re.fullmatch(
        r"[0-9a-f]{40}", base_sha
    ):
        raise AdapterError("PR exact head/base pair is unavailable.")
    return head_sha, base_sha


def select_qualified_coderabbit_review(
    reviews: Sequence[Mapping[str, Any]],
    *,
    pr_author: str,
    head_sha: str,
    base_sha: str,
) -> QualifiedReview:
    if not pr_author:
        raise AdapterError("PR author identity is required.")
    wanted_head = head_sha.lower()
    wanted_base = base_sha.lower()

    current_head_reviews: list[Mapping[str, Any]] = []
    for review in reviews:
        author = _login(review)
        if author.lower() not in CODERABBIT_AUTHORS:
            continue
        if author.lower() == pr_author.lower():
            continue
        if str(review.get("commit_id") or "").lower() != wanted_head:
            continue
        current_head_reviews.append(review)

    if not current_head_reviews:
        raise AdapterError("No independent CodeRabbit review is anchored to the current head.")

    adverse = [
        review
        for review in current_head_reviews
        if str(review.get("state") or "").strip().lower() == "changes_requested"
    ]
    if adverse:
        raise AdapterError("Current-head CodeRabbit review is adverse (CHANGES_REQUESTED).")

    candidates: list[QualifiedReview] = []
    for review in current_head_reviews:
        state = str(review.get("state") or "").strip().lower()
        if state != "commented":
            continue
        body = str(review.get("body") or "")
        if not body.strip():
            continue
        pair = _exact_pair(body)
        if pair is None:
            continue
        pair_base, pair_head = pair
        if pair_head != wanted_head or pair_base != wanted_base:
            continue
        submitted_at = str(review.get("submitted_at") or "").strip()
        html_url = str(review.get("html_url") or "").strip()
        review_id = int(review.get("id") or 0)
        if not submitted_at or not html_url or not review_id:
            continue
        candidates.append(
            QualifiedReview(
                review_id=review_id,
                html_url=html_url,
                submitted_at=submitted_at,
                author=_login(review),
                state=state,
                commit_id=wanted_head,
                base_sha=wanted_base,
                head_sha=wanted_head,
            )
        )

    if not candidates:
        raise AdapterError(
            "No valid exact-pair CodeRabbit COMMENTED review exists for the current head/base pair."
        )
    return max(candidates, key=lambda item: (item.submitted_at, item.review_id))


def canonical_attestation_body(review: QualifiedReview) -> str:
    reference = review.html_url
    return "\n".join(
        (
            f"<!-- hunter-hostile-review:v1 head={review.head_sha} base={review.base_sha} outcome=no-blocking-findings -->",
            f"Hostile review evidence: result=verified; reference={reference}",
            f"Scope probed: result=verified; reference={reference}",
            f"Limitations: result=disclosed; reference={reference}",
            "Unresolved blocking findings: 0",
            "",
            "This attestation is a deterministic bridge over the independently authored CodeRabbit exact-pair review above. It does not replace Hunter feedback, exact-head checks, Governance Review, or unresolved-thread gates.",
        )
    )


def _request_json(
    method: str,
    path: str,
    *,
    token: str,
    payload: Mapping[str, Any] | None = None,
) -> Any:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{api}/{path.lstrip('/')}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "hunter-coderabbit-review-adapter",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AdapterError(
            f"GitHub API request failed ({exc.code}): {detail[:400]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise AdapterError(f"GitHub API request failed: {exc.reason}") from exc
    return json.loads(raw) if raw else None


def _paged_reviews(
    repository: str, pr_number: int, *, token: str
) -> list[Mapping[str, Any]]:
    reviews: list[Mapping[str, Any]] = []
    page = 1
    while True:
        payload = _request_json(
            "GET",
            f"repos/{repository}/pulls/{pr_number}/reviews?per_page=100&page={page}",
            token=token,
        )
        if not isinstance(payload, list):
            raise AdapterError("GitHub returned an invalid PR review payload.")
        reviews.extend(item for item in payload if isinstance(item, Mapping))
        if len(payload) < 100:
            return reviews
        page += 1


def _require_successful_coderabbit_status(
    repository: str, head_sha: str, *, token: str
) -> None:
    payload = _request_json(
        "GET",
        f"repos/{repository}/commits/{head_sha}/status",
        token=token,
    )
    if not isinstance(payload, Mapping):
        raise AdapterError("GitHub returned an invalid commit-status payload.")

    status_sha = str(payload.get("sha") or "").strip().lower()
    if status_sha != head_sha.lower():
        raise AdapterError("CodeRabbit status is not anchored to the exact PR head.")

    statuses = payload.get("statuses")
    if not isinstance(statuses, Sequence) or isinstance(statuses, (str, bytes)):
        raise AdapterError("GitHub returned an invalid commit-status list.")

    coderabbit_statuses = [
        item
        for item in statuses
        if isinstance(item, Mapping)
        and str(item.get("context") or "").strip().lower()
        == CODERABBIT_STATUS_CONTEXT
    ]
    if not coderabbit_statuses:
        raise AdapterError("No governed CodeRabbit status exists for the exact PR head.")

    state = str(coderabbit_statuses[0].get("state") or "").strip().lower()
    if state != "success":
        raise AdapterError("Exact-head CodeRabbit status is not successful.")


def attest(repository: str, pr_number: int, *, token: str) -> QualifiedReview:
    pr_path = f"repos/{repository}/pulls/{pr_number}"
    pr = _request_json("GET", pr_path, token=token)
    if not isinstance(pr, Mapping):
        raise AdapterError("GitHub returned an invalid PR payload.")
    pr_author = str((pr.get("user") or {}).get("login") or "").strip()
    head_sha, base_sha = _pr_exact_pair(pr)

    select_qualified_coderabbit_review(
        _paged_reviews(repository, pr_number, token=token),
        pr_author=pr_author,
        head_sha=head_sha,
        base_sha=base_sha,
    )

    current_pr = _request_json("GET", pr_path, token=token)
    if not isinstance(current_pr, Mapping):
        raise AdapterError("GitHub returned an invalid refreshed PR payload.")
    current_head, current_base = _pr_exact_pair(current_pr)
    if current_head != head_sha or current_base != base_sha:
        raise AdapterError("PR exact head/base pair changed during attestation.")

    review = select_qualified_coderabbit_review(
        _paged_reviews(repository, pr_number, token=token),
        pr_author=pr_author,
        head_sha=head_sha,
        base_sha=base_sha,
    )
    _require_successful_coderabbit_status(repository, head_sha, token=token)

    final_pr = _request_json("GET", pr_path, token=token)
    if not isinstance(final_pr, Mapping):
        raise AdapterError("GitHub returned an invalid final PR payload.")
    final_head, final_base = _pr_exact_pair(final_pr)
    if final_head != head_sha or final_base != base_sha:
        raise AdapterError("PR exact head/base pair changed during attestation.")

    _request_json(
        "POST",
        f"repos/{repository}/pulls/{pr_number}/reviews",
        token=token,
        payload={
            "commit_id": head_sha,
            "event": "COMMENT",
            "body": canonical_attestation_body(review),
        },
    )
    return review


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Attest exact-pair CodeRabbit hostile-review evidence."
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--attest", action="store_true")
    args = parser.parse_args()

    if not args.attest:
        raise AdapterError("Refusing no-op invocation; pass --attest explicitly.")
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise AdapterError("GITHUB_TOKEN is required.")
    review = attest(args.repository, args.pr, token=token)
    print(
        f"CodeRabbit exact-pair hostile-review attestation posted for PR #{args.pr}: "
        f"review={review.review_id} head={review.head_sha} base={review.base_sha}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
