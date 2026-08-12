"""Trusted entrypoint policy for Hunter Merge Readiness.

This module keeps the core readiness controller fail-closed while exempting only
structurally identifiable, repository-generated status/advisory comments from the
owner-acknowledgment requirement and governance-freshness invalidation.

It also records a durable semantic invalidation marker for pull-request lifecycle
changes that can alter the governance subject. This avoids using GitHub's aggregate
``pull_request.updated_at`` as a semantic clock while still preventing a scheduled
reconciliation from re-accepting an older same-head Governance Review after a
real PR edit.

Migration note: a PR that has never received a semantic invalidation marker under
this policy (i.e. every PR that was already open the moment this code first
evaluates it) falls back one time to a conservative backfill baseline derived
from its raw ``updated_at`` at that moment, persisted durably so the aggregate
clock never has to be consulted again for that PR. This is required so that a
PR which is legitimately stale under the previous (pre-migration) policy is not
silently forgiven the instant this code starts running -- see
``backfill_semantic_baseline``.
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
# Backfill markers encode their baseline in the description because the
# baseline is a historical timestamp (the PR's raw updated_at as observed at
# migration time), not "now" -- and a commit status's created_at is always
# server-assigned to the POST time, so it cannot carry a value from the past.
# Real semantic-edit markers deliberately do NOT use this: their created_at
# (server time) IS the correct invalidation boundary, and trusting only the
# server clock for those is a deliberate security property -- a compromised
# or buggy caller cannot backdate a real edit's invalidation.
BACKFILL_DESCRIPTION_PREFIX = "Migration backfill baseline (raw updated_at):"
BACKFILL_TIMESTAMP_PATTERN = re.compile(re.escape(BACKFILL_DESCRIPTION_PREFIX) + r"\s*(\S+)")


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


def _marker_effective_time(status: dict) -> datetime | None:
    """Returns the semantic time a durable invalidation marker represents.

    A migration-backfill marker encodes its historical baseline in the
    description (see module docstring); any other marker's server-assigned
    ``created_at`` is itself the correct, trustworthy boundary.
    """
    description = status.get("description") or ""
    match = BACKFILL_TIMESTAMP_PATTERN.search(description)
    if match:
        parsed = core.parse_time(match.group(1))
        if parsed is not None:
            return parsed
    return core.parse_time(status.get("created_at"))


def latest_semantic_invalidation_time(pr: dict) -> datetime | None:
    sha = ((pr.get("head") or {}).get("sha") or "").strip()
    if not sha:
        return None
    status = core.latest_commit_status(sha, INVALIDATION_CONTEXT)
    if not status:
        return None
    return _marker_effective_time(status)


def backfill_semantic_baseline(pr: dict) -> datetime | None:
    """One-time migration step for a head SHA with no durable marker at all.

    The instant this policy starts evaluating a PR that predates it, there is
    no recorded semantic evidence for whatever real edits happened before
    deployment -- only the (now-neutralized) aggregate ``updated_at`` still
    reflects that history. This persists that raw value durably, under the
    same commit-status context, so it is never silently forgiven and so every
    later evaluation can stop consulting the aggregate clock entirely (it
    will find this marker instead). Best-effort durability: if the write
    itself cannot be completed, the computed baseline is still returned and
    used for this one evaluation, so a transient persistence failure can
    never silently drop real historical staleness -- only defer re-recording
    it to the next evaluation.
    """
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
        durable_time = backfill_semantic_baseline(pr)
    if durable_time is None:
        return base_time
    return max(base_time, durable_time)


def record_semantic_pr_invalidation(event: dict) -> None:
    """Persist a same-head invalidation boundary before reconciliation can race it.

    Durable, fail-closed write: acquires the per-PR reconciliation lock (so
    this cannot race a concurrent scheduled sweep's freshness recheck for the
    same PR -- see ``hunter_merge_readiness._confirm_still_fresh_before_success``)
    and retries the POST itself with bounded backoff on transient failures.
    If the lock cannot be acquired, or the write cannot be completed after
    retrying, this raises. Callers MUST NOT proceed to any path that could
    publish a governance success for this event after this raises -- the
    real edit's invalidation would otherwise have no record anywhere and a
    later evaluation could wrongly treat stale governance as fresh.
    """
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

    lock_ref = core.acquire_pr_lock(int(pr_number), sha)
    if lock_ref is None:
        raise RuntimeError(
            f"could not acquire PR #{pr_number} reconciliation lock; refusing to record "
            "semantic invalidation without exclusive access (fail-closed)."
        )
    try:

        def _post() -> None:
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

        core.retry_transient(_post)
    finally:
        core.release_pr_lock(lock_ref)
    print(f"{sha[:10]} {INVALIDATION_CONTEXT}: recorded {action}")


def main() -> None:
    core.owner_acknowledged_comment = owner_acknowledged_comment_with_bot_exemptions
    core.get_latest_invalidation_time = get_latest_invalidation_time_with_bot_exemptions

    # Initialize once here so the durable invalidation marker is written before
    # core.main() can evaluate or a concurrent scheduled reconciliation can win.
    core.init_globals()
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)

    try:
        record_semantic_pr_invalidation(event)
    except Exception as exc:
        # Fail closed: a real semantic edit's invalidation could not be
        # durably recorded, so core.main() must not run for this event --
        # doing so could compute and publish a governance success that does
        # not account for this edit. Surface this as clearly as possible and
        # let the next event/schedule tick retry from a clean slate.
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
