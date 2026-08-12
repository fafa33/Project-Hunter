"""Trusted entrypoint policy for Hunter Merge Readiness.

This module keeps the core readiness controller fail-closed while exempting only
structurally identifiable, repository-generated status/advisory comments from the
owner-acknowledgment requirement and governance-freshness invalidation.

It records durable semantic invalidation markers for pull-request lifecycle
changes. For real GitHub lifecycle payloads, the marker carries the GitHub-authored
event timestamp captured from the trusted ``pull_request_target`` payload. That
timestamp represents when the semantic PR change occurred, while commit-status
``created_at`` represents only when the readiness worker happened to persist the
marker. Using event time prevents workflow scheduling latency from falsely making
an exact-head Governance Review look older than the change that triggered it.

Migration note: a PR that has never received a semantic invalidation marker under
this policy falls back one time to a conservative backfill baseline derived from
its raw ``updated_at`` at that moment, persisted durably so the aggregate clock
never has to be consulted again for that PR.
"""

import json
import os
import re
from datetime import datetime

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
BACKFILL_DESCRIPTION_PREFIX = "Migration backfill baseline (raw updated_at):"
BACKFILL_TIMESTAMP_PATTERN = re.compile(re.escape(BACKFILL_DESCRIPTION_PREFIX) + r"\s*(\S+)")
SEMANTIC_EVENT_TIME_PREFIX = "event_time="
SEMANTIC_EVENT_TIME_PATTERN = re.compile(re.escape(SEMANTIC_EVENT_TIME_PREFIX) + r"(\S+)")


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
    """Return a copy whose aggregate activity timestamp cannot stale governance."""
    view = dict(pr)
    view["updated_at"] = pr.get("created_at")
    return view


def _marker_effective_time(status: dict) -> datetime | None:
    """Return the semantic time represented by a durable invalidation marker.

    New lifecycle markers persist the GitHub-authored event time in their
    description. Status ``created_at`` is persistence latency and is used only
    for legacy markers that predate event-time encoding. Migration markers
    continue to encode their historical baseline explicitly.
    """
    description = status.get("description") or ""
    backfill_match = BACKFILL_TIMESTAMP_PATTERN.search(description)
    if backfill_match:
        parsed = core.parse_time(backfill_match.group(1))
        if parsed is not None:
            return parsed
    event_match = SEMANTIC_EVENT_TIME_PATTERN.search(description)
    if event_match:
        parsed = core.parse_time(event_match.group(1))
        if parsed is not None:
            return parsed
    return core.parse_time(status.get("created_at"))


def latest_semantic_invalidation_time(pr: dict) -> datetime | None:
    """Return the monotonic semantic invalidation boundary for the current head.

    Persistence order is not semantic order: GitHub may rerun or finish an older
    same-head lifecycle workflow after a newer one, producing a higher-ID status
    whose encoded ``event_time`` is earlier. Therefore the boundary is the maximum
    effective time across *all* durable invalidation markers for this exact head,
    never simply the most recently persisted status.
    """
    sha = ((pr.get("head") or {}).get("sha") or "").strip()
    if not sha:
        return None

    statuses = core.all_commit_statuses(sha)
    effective_times = []
    for status in statuses:
        if not isinstance(status, dict) or status.get("context") != INVALIDATION_CONTEXT:
            continue
        effective_time = _marker_effective_time(status)
        if effective_time is not None:
            effective_times.append(effective_time)

    if not effective_times:
        return None
    return max(effective_times)


def backfill_semantic_baseline(pr: dict) -> datetime | None:
    """One-time migration step for a head SHA with no durable marker at all."""
    sha = ((pr.get("head") or {}).get("sha") or "").strip()
    if not sha:
        return None
    raw_updated_at = pr.get("updated_at") or pr.get("created_at")
    if not raw_updated_at:
        return None
    baseline = core.parse_time(raw_updated_at)
    if baseline is None:
        return None

    def _post() -> None:
        core.request_json(
            "POST",
            f"statuses/{sha}",
            {
                "state": "success",
                "context": INVALIDATION_CONTEXT,
                "description": f"{BACKFILL_DESCRIPTION_PREFIX} {raw_updated_at}",
                "target_url": core.run_url,
            },
        )

    try:
        core.retry_transient(_post)
        print(f"{sha[:10]} {INVALIDATION_CONTEXT}: backfilled migration baseline {raw_updated_at}")
    except Exception as exc:
        print(
            f"{sha[:10]} {INVALIDATION_CONTEXT}: could not durably persist migration backfill "
            f"({type(exc).__name__}: {exc}); using {raw_updated_at} for this evaluation only, "
            "will retry backfill on the next evaluation."
        )
    return baseline


def get_latest_invalidation_time_with_bot_exemptions(pr_number: int, pr: dict):
    """Use explicit feedback plus durable semantic PR-event evidence."""
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
        durable_time = backfill_semantic_baseline(pr)
    if durable_time is None:
        return base_time
    return max(base_time, durable_time)


def _semantic_event_time(pr: dict) -> str | None:
    """Return a valid GitHub-authored lifecycle timestamp when present.

    Real ``pull_request_target`` payloads include ``updated_at``. The ``None``
    fallback exists only for legacy/synthetic callers and old unit fixtures;
    such markers retain their historical server-``created_at`` semantics.
    """
    value = (pr.get("updated_at") or pr.get("created_at") or "").strip()
    if not value or core.parse_time(value) is None:
        return None
    return value


def record_semantic_pr_invalidation(event: dict) -> None:
    """Persist a same-head invalidation boundary before reconciliation can race it."""
    if core.event_name != "pull_request_target":
        return
    action = (event.get("action") or "").strip()
    if action not in SEMANTIC_PR_ACTIONS:
        return
    pr = event.get("pull_request") or {}
    pr_number = pr.get("number")
    sha = ((pr.get("head") or {}).get("sha") or "").strip()
    if not sha or not pr_number:
        raise RuntimeError("semantic PR invalidation event has no head SHA/PR number")
    event_time = _semantic_event_time(pr)

    lock_ref = core.acquire_pr_lock(int(pr_number), sha)
    if lock_ref is None:
        raise RuntimeError(
            f"could not acquire PR #{pr_number} reconciliation lock; refusing to record "
            "semantic invalidation without exclusive access (fail-closed)."
        )
    try:

        def _post() -> None:
            description = f"Semantic PR invalidation recorded: {action}"
            if event_time is not None:
                description += f"; {SEMANTIC_EVENT_TIME_PREFIX}{event_time}"
            core.request_json(
                "POST",
                f"statuses/{sha}",
                {
                    "state": "success",
                    "context": INVALIDATION_CONTEXT,
                    "description": description,
                    "target_url": core.run_url,
                },
            )

        core.retry_transient(_post)
    finally:
        core.release_pr_lock(lock_ref)
    suffix = f" at semantic event time {event_time}" if event_time else " using legacy persistence time"
    print(f"{sha[:10]} {INVALIDATION_CONTEXT}: recorded {action}{suffix}")


def main() -> None:
    core.owner_acknowledged_comment = owner_acknowledged_comment_with_bot_exemptions
    core.get_latest_invalidation_time = get_latest_invalidation_time_with_bot_exemptions

    core.init_globals()
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)

    try:
        record_semantic_pr_invalidation(event)
    except Exception as exc:
        print(
            f"Could not durably record semantic PR invalidation ({type(exc).__name__}: {exc}); "
            "failing closed instead of proceeding to a possible success publish."
        )
        sha = (((event.get("pull_request") or {}).get("head") or {}).get("sha") or "").strip()
        if sha:
            try:
                core.publish(
                    sha,
                    "pending",
                    "Waiting: could not durably record this PR edit's governance invalidation yet; retrying.",
                )
            except Exception as publish_exc:
                print(
                    f"Could not publish fail-closed pending status either: {type(publish_exc).__name__}: {publish_exc}"
                )
        raise

    core.main()


if __name__ == "__main__":
    main()
