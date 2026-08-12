"""Trusted entrypoint policy for Hunter Merge Readiness.

This module keeps the core readiness controller fail-closed while exempting only
structurally identifiable, repository-generated status/advisory comments from the
owner-acknowledgment requirement and governance-freshness invalidation.
"""

import hunter_merge_readiness as core

TRUSTED_BOT_LOGIN = "github-actions[bot]"
DEPENDENCY_REVIEW_MARKER = "<!-- dependency-review-pr-comment-marker -->"
DRAFT_PROMOTION_MARKER_PREFIX = "<!-- hunter-draft-promotion:"


def is_exempt_status_comment(comment: dict) -> bool:
    """Return True only for known repository automation status comments."""
    login = ((comment.get("user") or {}).get("login") or "").strip()
    if login != TRUSTED_BOT_LOGIN:
        return False

    body = comment.get("body") or ""
    if DEPENDENCY_REVIEW_MARKER in body:
        return True
    return DRAFT_PROMOTION_MARKER_PREFIX in body


_original_owner_acknowledged_comment = core.owner_acknowledged_comment
_original_get_latest_invalidation_time = core.get_latest_invalidation_time


def owner_acknowledged_comment_with_bot_exemptions(comment: dict) -> bool:
    if is_exempt_status_comment(comment):
        return True
    return _original_owner_acknowledged_comment(comment)


def semantic_pr_for_freshness(pr: dict) -> dict:
    """Return a PR view that excludes GitHub's aggregate activity timestamp.

    ``updated_at`` is an aggregate issue/PR activity clock, not a precise signal
    that the Governance review subject changed. Trusted automation and other
    bookkeeping can advance it after a valid Governance run and falsely stale the
    run. Exact-head validation plus explicit comment/review timestamps remain the
    semantic freshness authorities, while lifecycle webhooks still invalidate an
    already-green readiness state when PR metadata actually changes.
    """
    semantic_pr = dict(pr)
    semantic_pr["updated_at"] = pr.get("created_at")
    return semantic_pr


def get_latest_invalidation_time_with_bot_exemptions(pr_number: int, pr: dict):
    """Use explicit semantic inputs for governance freshness.

    Trusted repository status comments are removed from the top-level-comment
    freshness input, and aggregate ``pr.updated_at`` drift is neutralized. Human
    or unknown-bot comments, reviews, review comments, exact-head checks, and
    lifecycle invalidation remain fail-closed.
    """
    original_paged = core.paged
    top_level_comments_path = f"issues/{pr_number}/comments"

    def paged_with_bot_exemptions(path: str):
        items = original_paged(path)
        if path.split("?", 1)[0] == top_level_comments_path:
            return [item for item in items if not is_exempt_status_comment(item)]
        return items

    core.paged = paged_with_bot_exemptions
    try:
        return _original_get_latest_invalidation_time(
            pr_number,
            semantic_pr_for_freshness(pr),
        )
    finally:
        core.paged = original_paged


def main() -> None:
    core.owner_acknowledged_comment = owner_acknowledged_comment_with_bot_exemptions
    core.get_latest_invalidation_time = get_latest_invalidation_time_with_bot_exemptions
    core.main()


if __name__ == "__main__":
    main()
