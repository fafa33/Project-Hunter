"""Trusted entrypoint policy for Hunter Merge Readiness.

This module keeps the core readiness controller fail-closed while exempting only
structurally identifiable, repository-generated status/advisory comments from the
owner-acknowledgment requirement.
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


def owner_acknowledged_comment_with_bot_exemptions(comment: dict) -> bool:
    if is_exempt_status_comment(comment):
        return True
    return _original_owner_acknowledged_comment(comment)


def main() -> None:
    core.owner_acknowledged_comment = owner_acknowledged_comment_with_bot_exemptions
    core.main()


if __name__ == "__main__":
    main()
