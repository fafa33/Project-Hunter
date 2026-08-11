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


def get_latest_invalidation_time_with_bot_exemptions(pr_number: int, pr: dict):
    """Ignore trusted advisory comments when calculating governance freshness."""
    original_paged = core.paged
    top_level_comments_path = f"issues/{pr_number}/comments"

    def paged_with_bot_exemptions(path: str):
        items = original_paged(path)
        if path.split("?", 1)[0] == top_level_comments_path:
            return [item for item in items if not is_exempt_status_comment(item)]
        return items

    core.paged = paged_with_bot_exemptions
    try:
        return _original_get_latest_invalidation_time(pr_number, pr)
    finally:
        core.paged = original_paged


def main() -> None:
    core.owner_acknowledged_comment = owner_acknowledged_comment_with_bot_exemptions
    core.get_latest_invalidation_time = get_latest_invalidation_time_with_bot_exemptions
    core.main()


if __name__ == "__main__":
    main()
