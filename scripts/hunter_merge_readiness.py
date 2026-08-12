import json
import os
import re
import time
import urllib.request
from datetime import UTC, datetime
from typing import Any

import hunter_controller_admission as admission

# Configuration
context = "Hunter Merge Readiness"
governance_context = "Hunter Governance Review"
required_checks = ("Quality Gates", "dependency-review", "CodeQL")
hard_failures = {"failure", "timed_out", "action_required", "startup_failure"}
# Event names that perform a full, check-runs-based reconciliation for a single PR
# instead of the lightweight "invalidate and wait" webhook shortcut. `schedule` is
# the periodic self-healing sweep; `workflow_dispatch` is an operator-triggered
# on-demand equivalent for a single PR (see hunter-merge-readiness.yml).
RECONCILIATION_EVENT_NAMES = {"schedule", "workflow_dispatch"}

# Per-PR reconciliation lock. GitHub's Statuses/Comments/Issues APIs offer no
# atomic compare-and-swap primitive, so this uses the one GitHub REST endpoint
# that does: git ref creation fails (422) if the ref already exists. This is
# the only mechanism in this module that can give a true mutual-exclusion
# guarantee between a concurrent scheduled sweep and a real semantic PR edit
# for the same PR, since GitHub Actions `concurrency:` groups cannot express a
# per-PR key from inside a single schedule-triggered run that loops over many
# PRs (see hunter-merge-readiness.yml).
#
# Deliberately NOT under refs/heads/: a refs/heads/* ref is a branch, and
# creating one fires a `push` webhook event -- this repository's
# hunter-pre-pr-preflight.yml triggers on `push: branches-ignore: [main]`,
# so a lock ref living under refs/heads/ would recursively spawn a full
# Preflight CI run on every single lock acquisition. A custom top-level ref
# namespace (refs/hunter-merge-readiness-locks/*) is a fully supported use of
# the Git References API, gives the identical atomic create-conflict (422)
# semantics, never triggers `push`/`create` events, is exempt from branch
# protection, and does not clutter the repository's branch list.
LOCK_REF_NAMESPACE = "refs/hunter-merge-readiness-locks/pr-"
LOCK_ACQUIRE_ATTEMPTS = 5
LOCK_BASE_DELAY_SECONDS = 1.0
RETRY_ATTEMPTS = 4
RETRY_BASE_DELAY_SECONDS = 1.0


# Global state to allow test mocking
repo: str = ""
repo_owner: str = ""
token: str = ""
event_name: str = ""
run_url: str = ""
active_sha: str | None = None
latest_readiness: dict[str, Any] | None = None


def init_globals() -> None:
    global repo, repo_owner, token, event_name, run_url
    repo = os.environ.get("GH_REPO", "")
    if not repo:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
    repo_owner = repo.split("/", 1)[0] if "/" in repo else ""
    token = os.environ.get("GH_TOKEN", "")
    event_name = os.environ.get("EVENT_NAME", "")
    run_url = os.environ.get("RUN_URL", "")


def request_json(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            # A successful DELETE (e.g. releasing the per-PR lock ref) returns
            # 204 No Content with an empty body; json.loads("") raises, so an
            # empty body must be treated as "no JSON payload", not an error.
            raw_body = response.read().decode("utf-8")
            if not raw_body:
                return None
            return json.loads(raw_body)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
            print(f"HTTP Error {exc.code} for {method} {path}: {body}")
        except Exception as read_exc:
            print(f"HTTP Error {exc.code} for {method} {path} (could not read response body: {read_exc})")
        raise


def graphql_json(query: str, variables: dict[str, Any]) -> Any:
    data = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=data,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
            print(f"GraphQL HTTP Error {exc.code}: {body}")
        except Exception as read_exc:
            print(f"GraphQL HTTP Error {exc.code} (could not read response body: {read_exc})")
        raise
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL query failed: {payload['errors']}")
    return payload["data"]


def parse_time(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp, or return None if it cannot be parsed.

    Every other caller only ever passes a GitHub-server-controlled field
    (always valid ISO-8601 when present) or None. The one caller that parses
    an arbitrary regex-captured substring from a status description
    (hunter_merge_readiness_entrypoint._marker_effective_time) depends on
    this returning None -- not raising -- for a malformed value, so it can
    fall back to created_at instead of crashing the controller.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def paged(path: str) -> list[Any]:
    items: list[Any] = []
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        batch = request_json("GET", f"{path}{separator}per_page=100&page={page}")
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def publish(sha: str, state: str, description: str) -> None:
    global latest_readiness
    # Replace common emojis with text equivalents and strip characters > 0xFFFF to prevent GitHub API 422
    description = description.replace("👍", "+1")
    description = "".join(c for c in description if ord(c) <= 0xFFFF)
    description = description[:140]

    if (
        latest_readiness
        and latest_readiness.get("state") == state
        and (latest_readiness.get("description") or "").strip() == description.strip()
    ):
        print(f"Skipping redundant publish for {sha[:10]}: already {state} — {description}")
        return
    request_json(
        "POST",
        f"statuses/{sha}",
        {
            "state": state,
            "context": context,
            "description": description,
            "target_url": run_url,
        },
    )
    print(f"{sha[:10]} {context}: {state} — {description}")
    latest_readiness = {"state": state, "description": description}


def dependency_only_pr(pr_number: int, author: str) -> bool:
    if author not in {"dependabot[bot]", "renovate[bot]"}:
        return False
    files = paged(f"pulls/{pr_number}/files")
    if not files:
        return False
    allowed_root = {
        "pyproject.toml",
        "poetry.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
    }
    return all(item["filename"].startswith("requirements/") or item["filename"] in allowed_root for item in files)


def validate_metadata(pr_number: int, body: str, author: str) -> str | None:
    if dependency_only_pr(pr_number, author):
        return None
    rows = []
    in_table = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and "Acceptance criterion" in stripped:
            in_table = True
            continue
        if not stripped.startswith("|"):
            in_table = False
            continue
        if not in_table:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) >= 2 and cells[1].lower() in {"pass", "fail", "blocked", "not applicable"}:
            rows.append((cells[0], cells[1].lower()))
    if not rows:
        return "Acceptance-criteria matrix is missing or unparseable."
    unresolved = [name for name, status in rows if status in {"fail", "blocked"}]
    if unresolved:
        return "FAIL/BLOCKED acceptance criteria remain: " + ", ".join(unresolved)

    checked = []
    allowed = ("ready for review", "changes required", "blocked")
    for match in re.finditer(r"(?im)^\s*[-*+]\s*\[([ xX])\]\s*(.+)", body):
        marker, text = match.group(1), match.group(2).strip().lower()
        if marker.lower() != "x":
            continue
        normalized = text.replace("`", "").replace("*", "")
        for declaration in allowed:
            if normalized.startswith(declaration):
                checked.append(declaration)
                break
    if len(checked) != 1:
        return "Exactly one implementer readiness declaration must be checked."
    if checked[0] != "ready for review":
        return f"Implementer readiness is {checked[0].upper()}, not READY FOR REVIEW."
    return None


def unresolved_review_thread_count(pr_number: int) -> int:
    owner, name = repo.split("/", 1)
    query = """
    query($owner: String!, $name: String!, $number: Int!, $after: String) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
          reviewThreads(first: 100, after: $after) {
            nodes { isResolved }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    }
    """
    count = 0
    cursor = None
    while True:
        data = graphql_json(
            query,
            {
                "owner": owner,
                "name": name,
                "number": int(pr_number),
                "after": cursor,
            },
        )
        pull = (data.get("repository") or {}).get("pullRequest") or {}
        threads = pull.get("reviewThreads") or {}
        count += sum(1 for node in threads.get("nodes") or [] if not node.get("isResolved"))
        page_info = threads.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return count
        cursor = page_info.get("endCursor")


def current_changes_requested_reviewers(pr_number: int) -> list[str]:
    reviews = paged(f"pulls/{pr_number}/reviews")
    latest_terminal = {}
    for review in reviews:
        login = ((review.get("user") or {}).get("login") or "").strip()
        state = (review.get("state") or "").upper()
        if not login or state not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            continue
        previous = latest_terminal.get(login)
        if previous is None or int(review.get("id", 0)) > int(previous.get("id", 0)):
            latest_terminal[login] = review
    return sorted(
        login for login, review in latest_terminal.items() if (review.get("state") or "").upper() == "CHANGES_REQUESTED"
    )


def owner_acknowledged_comment(comment: dict[str, Any]) -> bool:
    comment_id = int(comment["id"])
    updated_at = parse_time(comment.get("updated_at") or comment.get("created_at"))
    reactions = paged(f"issues/comments/{comment_id}/reactions")
    for reaction in reactions:
        login = ((reaction.get("user") or {}).get("login") or "").strip()
        if login != repo_owner or reaction.get("content") != "+1":
            continue
        reaction_at = parse_time(reaction.get("created_at"))
        # Fail closed if GitHub omits reaction time; an acknowledgement must
        # prove it applies to the current version of the comment.
        if reaction_at is not None and updated_at is not None and reaction_at >= updated_at:
            return True
    return False


def unacknowledged_top_level_comments(pr_number: int) -> list[int]:
    comments = paged(f"issues/{pr_number}/comments")
    return [int(comment["id"]) for comment in comments if not owner_acknowledged_comment(comment)]


def review_feedback_error(pr_number: int) -> str | None:
    unresolved_threads = unresolved_review_thread_count(pr_number)
    if unresolved_threads:
        return f"Unresolved review threads remain: {unresolved_threads}."
    changes_requested = current_changes_requested_reviewers(pr_number)
    if changes_requested:
        return "Changes requested by: " + ", ".join(changes_requested)
    comments = unacknowledged_top_level_comments(pr_number)
    if comments:
        preview = ", ".join(str(item) for item in comments[:4])
        suffix = "…" if len(comments) > 4 else ""
        return f"Unacknowledged PR comments need owner 👍: {preview}{suffix}"
    return None


def all_check_runs(sha: str) -> list[dict[str, Any]]:
    runs = []
    page = 1
    while True:
        payload = request_json("GET", f"commits/{sha}/check-runs?per_page=100&page={page}&filter=all")
        batch = payload["check_runs"]
        runs.extend(batch)
        if len(batch) < 100:
            return runs
        page += 1


def all_commit_statuses(sha: str) -> list[dict[str, Any]]:
    """Fetch every status ever posted for this exact SHA, unfiltered.

    Mirrors all_check_runs()/latest_check(): this fetches the full,
    paginated, unfiltered evidence; callers that need more than "the single
    latest-by-id match for one context" (e.g. computing a monotonic maximum
    across every same-context marker) must use this directly rather than
    latest_commit_status(), which collapses to one status and is unsafe for
    that purpose -- persistence order is not semantic order.
    """
    return paged(f"commits/{sha}/statuses")


def latest_commit_status(sha: str, status_context: str) -> dict[str, Any] | None:
    statuses = all_commit_statuses(sha)
    matches = [status for status in statuses if status.get("context") == status_context]
    if not matches:
        return None
    return max(matches, key=lambda status: int(status.get("id", 0)))


def latest_check(runs: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    matches = [run for run in runs if run.get("name") == name]
    if not matches:
        return None
    return max(matches, key=lambda run: int(run.get("id", 0)))


def get_latest_invalidation_time(pr_number: int, pr: dict[str, Any]) -> datetime:
    """Computes a deterministic invalidation timestamp based on the latest relevant

    GitHub pull request updates, comments, reviews, or review comments.
    This provides a temporal boundary to reject stale governance runs.
    """
    timestamps = []

    # 1. PR updated_at (covers body edits, ready_for_review transitions, synchronize, etc.)
    pr_updated = parse_time(pr.get("updated_at"))
    if pr_updated:
        timestamps.append(pr_updated)

    # 2. Top-level comments (reactions are intentionally excluded from governance
    # invalidation freshness: an owner +1 acknowledgment must resolve the comment
    # blocker without forcing an otherwise-fresh governance review to be rerun)
    comments = paged(f"issues/{pr_number}/comments")
    for comment in comments:
        if isinstance(comment, dict):
            c_time = parse_time(comment.get("updated_at") or comment.get("created_at"))
            if c_time:
                timestamps.append(c_time)

    # 3. Reviews
    reviews = paged(f"pulls/{pr_number}/reviews")
    for review in reviews:
        if isinstance(review, dict):
            r_time = parse_time(review.get("submitted_at"))
            if r_time:
                timestamps.append(r_time)

    # 4. Review comments (threads)
    review_comments = paged(f"pulls/{pr_number}/comments")
    for rc in review_comments:
        if isinstance(rc, dict):
            rc_time = parse_time(rc.get("updated_at") or rc.get("created_at"))
            if rc_time:
                timestamps.append(rc_time)

    if not timestamps:
        pr_created = parse_time(pr.get("created_at"))
        if pr_created:
            return pr_created
        return datetime.fromtimestamp(0, tz=UTC)

    return max(timestamps)


def _is_transient_http_error(exc: BaseException) -> bool:
    """Transient: worth retrying. Anything else (4xx other than 429, malformed
    request, auth failure) is permanent and must propagate immediately.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code == 429
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError))


def retry_transient(
    operation, *, attempts: int = RETRY_ATTEMPTS, base_delay: float = RETRY_BASE_DELAY_SECONDS, sleep_fn=time.sleep
):
    """Calls operation() with bounded exponential backoff on transient failures.

    Non-transient exceptions propagate immediately without retry. After the
    final attempt, the last transient exception propagates too -- callers
    must treat that as a hard, fail-closed failure, not a silent no-op.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if not _is_transient_http_error(exc):
                raise
            last_exc = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            print(
                f"Transient error on attempt {attempt}/{attempts}: {type(exc).__name__}: {exc}; retrying in {delay:.1f}s"
            )
            sleep_fn(delay)
    assert last_exc is not None
    raise last_exc


def acquire_pr_lock(
    pr_number: int,
    sha: str,
    *,
    attempts: int = LOCK_ACQUIRE_ATTEMPTS,
    base_delay: float = LOCK_BASE_DELAY_SECONDS,
    sleep_fn=time.sleep,
) -> str | None:
    """Attempts to atomically acquire a per-PR reconciliation lock.

    Returns the acquired lock ref name, or None if acquisition could not be
    completed. Callers MUST treat None as fail-closed: do not proceed with an
    irreversible success publish or a semantic invalidation write.

    Bounded retry with backoff on contention. If still contended after the
    full budget, the existing lock is treated as abandoned (e.g. a crashed
    run that never reached its `finally` release) and is force-cleared once,
    then acquisition is retried a final time -- this bounds how long a single
    stuck lock can block this PR's reconciliation without ever allowing two
    holders to believe they both hold the lock at once.
    """
    ref_name = f"{LOCK_REF_NAMESPACE}{pr_number}"
    for attempt in range(1, attempts + 1):
        try:
            request_json("POST", "git/refs", {"ref": ref_name, "sha": sha})
            return ref_name
        except urllib.error.HTTPError as exc:
            if exc.code != 422:
                if not _is_transient_http_error(exc) or attempt == attempts:
                    print(f"Could not acquire PR #{pr_number} reconciliation lock: HTTP {exc.code}.")
                    return None
                sleep_fn(base_delay * (2 ** (attempt - 1)))
                continue
            if attempt == attempts:
                break
            sleep_fn(base_delay * (2 ** (attempt - 1)))
    print(f"PR #{pr_number} reconciliation lock still contended after {attempts} attempts; treating as abandoned.")
    try:
        release_pr_lock(ref_name)
    except Exception as exc:
        print(f"Could not clear presumed-abandoned lock for PR #{pr_number}: {type(exc).__name__}: {exc}")
        return None
    try:
        request_json("POST", "git/refs", {"ref": ref_name, "sha": sha})
        return ref_name
    except Exception as exc:
        print(f"Final PR #{pr_number} reconciliation lock acquisition attempt failed: {type(exc).__name__}: {exc}")
        return None


def release_pr_lock(lock_ref: str) -> None:
    # DELETE /repos/{repo}/git/refs/{ref} takes {ref} WITHOUT the "refs/" prefix
    # (e.g. "heads/my-branch"), unlike the "ref" field used to CREATE it, which
    # requires the full "refs/heads/my-branch" form.
    ref_path = lock_ref[len("refs/") :] if lock_ref.startswith("refs/") else lock_ref
    try:
        request_json("DELETE", f"git/refs/{ref_path}")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise


def _confirm_still_fresh_before_success(pr_number: int, sha: str, governance_started_at: datetime) -> bool:
    """Final compare-and-check gate immediately before an irreversible success publish.

    Holds the per-PR reconciliation lock while re-fetching the PR and
    recomputing the invalidation boundary one more time. This closes the race
    where a concurrent real PR edit's semantic invalidation write lands after
    this evaluation began computing but before it published -- without this
    gate, a scheduled sweep could publish a stale "success" using
    information read just before that edit's invalidation was durably
    recorded. Returns False (fail closed: do not publish success) if the
    lock cannot be acquired, or if a fresh recomputation shows this
    governance run is no longer fresh.
    """
    lock_ref = acquire_pr_lock(pr_number, sha)
    if lock_ref is None:
        print(f"PR #{pr_number}: could not acquire reconciliation lock before publishing success; staying pending.")
        return False
    try:
        fresh_pr = request_json("GET", f"pulls/{pr_number}")
        if ((fresh_pr.get("head") or {}).get("sha") or "").strip() != sha:
            print(f"PR #{pr_number}: head SHA changed while confirming freshness; staying pending.")
            return False
        invalidation_time = get_latest_invalidation_time(pr_number, fresh_pr)
        if governance_started_at < invalidation_time:
            print(
                f"PR #{pr_number}: concurrent invalidation at {invalidation_time.isoformat()} detected "
                f"after governance run started at {governance_started_at.isoformat()}; staying pending."
            )
            return False
        return True
    except Exception as exc:
        print(
            f"PR #{pr_number}: could not confirm freshness before success ({type(exc).__name__}: {exc}); staying pending."
        )
        return False
    finally:
        release_pr_lock(lock_ref)


def evaluate(
    pr_number: int,
    governance_started_at: datetime | None = None,
    governance_conclusion: str | None = None,
    trigger_head_sha: str | None = None,
    poll: bool = False,
) -> None:
    global active_sha, latest_readiness
    pr = request_json("GET", f"pulls/{pr_number}")
    if pr.get("state") != "open":
        return
    sha = ((pr.get("head") or {}).get("sha") or "").strip()
    if not sha:
        raise RuntimeError(f"PR #{pr_number} head SHA unavailable")
    active_sha = sha
    if trigger_head_sha and trigger_head_sha != sha:
        print(f"Ignoring stale Governance Review completion for {trigger_head_sha}; PR #{pr_number} is at {sha}.")
        return

    # Cache latest_readiness status to avoid redundant API writes
    latest_readiness = latest_commit_status(sha, context)

    # P1-2 Invalidate Green Immediately: Publish pending before querying any potentially
    # lengthy metadata, feedback, or governance state to avoid concurrency race conditions.
    if event_name not in RECONCILIATION_EVENT_NAMES and latest_readiness and latest_readiness.get("state") == "success":
        print(
            f"Lifecycle event '{event_name}' received on already-green PR #{pr_number}. Invalidating green status immediately."
        )
        publish(sha, "pending", "Validating current feedback and exact-head prerequisites.")

    body = pr.get("body") or ""
    draft = bool(pr.get("draft"))
    author = (pr.get("user") or {}).get("login") or ""

    if draft:
        publish(sha, "pending", "Waiting for Ready for Review (PR is Draft).")
        return

    # In the self-healing scheduler, we evaluate all open PRs, including missing,
    # pending, success, and failures. We no longer return early here for scheduled runs.

    metadata_error = validate_metadata(pr_number, body, author)
    if metadata_error:
        publish(sha, "failure", metadata_error)
        return
    feedback_error = review_feedback_error(pr_number)
    if feedback_error:
        publish(sha, "failure", feedback_error)
        return

    # PR/review/comment webhooks invalidate stale green immediately.
    # They do not consume an older same-SHA governance status.
    # Final success is produced by Governance completion or by the periodic reconciler.
    if governance_started_at is None and event_name not in RECONCILIATION_EVENT_NAMES:
        publish(sha, "pending", "Waiting for current Hunter Governance Review.")
        return

    # Detected once per evaluation, not per poll attempt: the PR's changed-file
    # set cannot meaningfully change within a single evaluate() invocation, so
    # re-checking it on every one of up to 45 poll iterations would only add
    # redundant API calls.
    is_upgrade_candidate = admission.is_controller_upgrade_candidate(pr_number)

    attempts = 45 if poll else 1
    for attempt in range(1, attempts + 1):
        runs = all_check_runs(sha)
        if event_name in RECONCILIATION_EVENT_NAMES:
            governance_run = latest_check(runs, governance_context)
            if governance_run is None:
                publish(sha, "pending", "Waiting for current Hunter Governance Review.")
                return
            if governance_run.get("status") != "completed":
                publish(sha, "pending", "Waiting for current Hunter Governance Review.")
                return
            governance_conclusion = governance_run.get("conclusion")
            governance_started_at = parse_time(governance_run.get("started_at"))

        if governance_conclusion != "success":
            publish(
                sha,
                "failure",
                f"Current Hunter Governance Review workflow ended {governance_conclusion or 'without a conclusion'}.",
            )
            return
        if governance_started_at is None:
            publish(sha, "failure", "Current Governance Review run has no trustworthy start time.")
            return

        # Controller-upgrade admission: a PR that touches controller-owned
        # files (see hunter_controller_admission.CONTROLLER_OWNED_PATHS) is
        # independently re-verified with fresh, uncached evidence instead of
        # the ordinary steady-state invalidation-time comparison below. This
        # is a strict superset of the ordinary gates (same required checks,
        # same Governance freshness primitive, same feedback checks, plus an
        # exact-head re-fetch) -- never a relaxation. It exists so a PR that
        # upgrades this very controller can still be safely evaluated and
        # merged without executing any of that PR's own (untrusted, not-yet-
        # resident) code. See hunter_controller_admission.py for the full
        # trust-boundary rationale and its documented bootstrap limitation.
        if is_upgrade_candidate:
            result = admission.evaluate_admission(pr_number, pr, sha, runs, governance_started_at)
            if not result.admitted:
                publish(sha, "pending", f"Controller-upgrade admission pending: {result.reason}")
                return
        else:
            # P1-1: Reject governance runs that started before the latest invalidation.
            # This prevents accepting stale same-SHA governance after feedback/body changes.
            invalidation_time = get_latest_invalidation_time(pr_number, pr)
            if governance_started_at < invalidation_time:
                print(
                    f"Rejecting governance run from {governance_started_at.isoformat()} because it is older than latest invalidation time {invalidation_time.isoformat()}"
                )
                publish(
                    sha,
                    "pending",
                    f"Waiting for a fresh Hunter Governance Review (last run was older than latest invalidation at {invalidation_time.isoformat()}).",
                )
                return

        missing, pending, failed, succeeded = [], [], [], []
        for name in required_checks:
            run = latest_check(runs, name)
            if run is None:
                missing.append(name)
            elif run.get("status") != "completed":
                pending.append(name)
            elif run.get("conclusion") == "success":
                succeeded.append(name)
            elif run.get("conclusion") in hard_failures:
                failed.append(f"{name}={run.get('conclusion')}")
            else:
                pending.append(name)

        governance = latest_commit_status(sha, governance_context)
        governance_success = False
        if governance is None:
            missing.append(governance_context)
        else:
            status_created_at = parse_time(governance.get("created_at"))
            if status_created_at is None or status_created_at < governance_started_at:
                pending.append(f"{governance_context} (fresh evaluation)")
            else:
                state = governance.get("state") or "pending"
                if state == "success":
                    governance_success = True
                elif state in {"failure", "error"}:
                    failed.append(f"{governance_context}={state}")
                else:
                    pending.append(governance_context)

        if failed:
            publish(sha, "failure", "Exact-head prerequisite failed: " + ", ".join(failed))
            return
        if governance_success and not missing and not pending and len(succeeded) == len(required_checks):
            if not _confirm_still_fresh_before_success(pr_number, sha, governance_started_at):
                publish(
                    sha,
                    "pending",
                    "Waiting for a fresh Hunter Governance Review "
                    "(concurrent semantic invalidation detected while confirming readiness).",
                )
                return
            success_description = "Ready to merge: fresh checks passed and every PR comment is resolved/acknowledged."
            if is_upgrade_candidate:
                success_description = (
                    "Ready to merge: controller-upgrade PR admitted by the current trusted generation. "
                    + success_description
                )
            publish(
                sha,
                "success",
                success_description,
            )
            return

        waiting = missing + pending
        publish(sha, "pending", "Waiting for exact-head checks: " + ", ".join(waiting))
        if attempt < attempts:
            time.sleep(20)

    if poll:
        publish(sha, "failure", "Timed out waiting for fresh exact-head prerequisites.")


def main() -> None:
    global active_sha
    init_globals()

    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)

    try:
        if event_name == "workflow_run":
            workflow_run = event.get("workflow_run") or {}
            pulls = workflow_run.get("pull_requests") or []
            if not pulls:
                print("Governance workflow completion is not attached to a pull request; skipping.")
                raise SystemExit(0)
            evaluate(
                int(pulls[0]["number"]),
                governance_started_at=parse_time(workflow_run.get("run_started_at")),
                governance_conclusion=workflow_run.get("conclusion"),
                trigger_head_sha=((pulls[0].get("head") or {}).get("sha") or "").strip() or None,
                poll=True,
            )
        elif event_name == "schedule":
            page = 1
            while True:
                prs = request_json("GET", f"pulls?state=open&base=main&per_page=100&page={page}")
                for pr in prs:
                    active_sha = None
                    evaluate(int(pr["number"]), poll=False)
                    active_sha = None
                if len(prs) < 100:
                    break
                page += 1
        elif event_name == "issue_comment":
            issue = event.get("issue") or {}
            if not issue.get("pull_request"):
                print("Issue comment is not on a pull request; skipping.")
            else:
                evaluate(int(issue["number"]), poll=False)
        elif event_name == "workflow_dispatch":
            pr_number = (event.get("inputs") or {}).get("pr_number")
            if not pr_number:
                print("workflow_dispatch requires a pr_number input; skipping.")
                raise SystemExit(1)
            evaluate(int(pr_number), poll=False)
        else:
            pull_request = event.get("pull_request") or {}
            pr_number = pull_request.get("number") or ((event.get("issue") or {}).get("number"))
            if not pr_number:
                print("Event has no pull request number; skipping.")
            else:
                evaluate(int(pr_number), poll=False)
    except Exception as exc:
        print(f"Readiness controller error: {type(exc).__name__}: {exc}")
        if active_sha:
            try:
                publish(active_sha, "failure", f"Readiness controller error: {type(exc).__name__}.")
            except Exception as publish_exc:
                print(f"Could not publish terminal controller failure: {type(publish_exc).__name__}: {publish_exc}")
        raise


if __name__ == "__main__":
    main()
