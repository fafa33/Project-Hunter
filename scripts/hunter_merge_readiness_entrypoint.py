"""Trusted entrypoint policy for Hunter Merge Readiness.

This module keeps the core readiness controller fail-closed while exempting only
structurally identifiable, repository-generated status/advisory comments from the
owner-acknowledgment requirement and governance-freshness invalidation.

It also records a durable semantic invalidation marker for pull-request lifecycle
changes that can alter the governance subject. This avoids using GitHub's aggregate
``pull_request.updated_at`` as a semantic clock while still preventing a scheduled
reconciliation from re-accepting an older same-head Governance Review after a
real PR edit.
"""

import json
import os

import hunter_merge_readiness as core

TRUSTED_BOT_LOGIN = "github-actions[bot]"
DEPENDENCY_REVIEW_MARKER = "<!-- dependency-review-pr-comment-marker -->"
DRAFT_PROMOTION_MARKER_PREFIX = "<!-- hunter-draft-promotion:"
INVALIDATION_CONTEXT = "Hunter Governance Invalidation"
SEMANTIC_PR_ACTIONS = {
    "opened",
    "reopened",
    "synchronize",
    "edited",
    "ready_for_review",
    "converted_to_draft",
}


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


def semantic_pr_view(pr: dict) -> dict:
    """Return a copy whose aggregate activity timestamp cannot stale governance.

    ``updated_at`` is intentionally neutralized to the stable creation timestamp.
    Real same-head PR edits are represented by the durable invalidation status
    written from their lifecycle webhook instead.
    """
    view = dict(pr)
    view["updated_at"] = pr.get("created_at")
    return view


def latest_semantic_invalidation_time(pr: dict):
    sha = ((pr.get("head") or {}).get("sha") or "").strip()
    if not sha:
        return None
    status = core.latest_commit_status(sha, INVALIDATION_CONTEXT)
    if not status:
        return None
    return core.parse_time(status.get("created_at"))


def get_latest_invalidation_time_with_bot_exemptions(pr_number: int, pr: dict):
    """Use semantic evidence plus a durable PR-edit marker for freshness."""
    original_paged = core.paged
    top_level_comments_path = f"issues/{pr_number}/comments"

    def paged_with_bot_exemptions(path: str):
        items = original_paged(path)
        if path.split("?", 1)[0] == top_level_comments_path:
            return [item for item in items if not is_exempt_status_comment(item)]
        return items

    core.paged = paged_with_bot_exemptions
    try:
        base_time = _original_get_latest_invalidation_time(pr_number, semantic_pr_view(pr))
    finally:
        core.paged = original_paged

    durable_time = latest_semantic_invalidation_time(pr)
    if durable_time is None:
        return base_time
    return max(base_time, durable_time)


def record_semantic_pr_invalidation(event: dict) -> None:
    """Persist a same-head invalidation boundary before reconciliation can race it."""
    if core.event_name != "pull_request_target":
        return
    action = (event.get("action") or "").strip()
    if action not in SEMANTIC_PR_ACTIONS:
        return
    pr = event.get("pull_request") or {}
    sha = ((pr.get("head") or {}).get("sha") or "").strip()
    if not sha:
        raise RuntimeError("semantic PR invalidation event has no head SHA")
    core.request_json(
        "POST",
        f"statuses/{sha}",
        {
            "state": "success",
            "context": INVALIDATION_CONTEXT,
            "description": f"Semantic PR invalidation recorded: {action}",
            "target_url": core.run_url,
        },
    )
    print(f"{sha[:10]} {INVALIDATION_CONTEXT}: recorded {action}")


def main() -> None:
    core.owner_acknowledged_comment = owner_acknowledged_comment_with_bot_exemptions
    core.get_latest_invalidation_time = get_latest_invalidation_time_with_bot_exemptions

    # Initialize once here so the durable invalidation marker is written before
    # core.main() can evaluate or a concurrent scheduled reconciliation can win.
    core.init_globals()
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    record_semantic_pr_invalidation(event)
    core.main()


if __name__ == "__main__":
    main()
