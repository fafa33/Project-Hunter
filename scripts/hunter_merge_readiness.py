import json
import os
import re
import time
import urllib.request
from datetime import UTC, datetime
from typing import Any

# Configuration
context = "Hunter Merge Readiness"
governance_context = "Hunter Governance Review"
required_checks = ("Quality Gates", "dependency-review", "CodeQL")
hard_failures = {"failure", "timed_out", "action_required", "startup_failure"}

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
            return json.loads(response.read().decode("utf-8"))
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
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


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


def latest_commit_status(sha: str, status_context: str) -> dict[str, Any] | None:
    statuses = paged(f"commits/{sha}/statuses")
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

    GitHub pull request updates, comments, reviews, review comments, or reactions.
    This provides a temporal boundary to reject stale governance runs.
    """
    timestamps = []

    # 1. PR updated_at (covers body edits, ready_for_review transitions, synchronize, etc.)
    pr_updated = parse_time(pr.get("updated_at"))
    if pr_updated:
        timestamps.append(pr_updated)

    # 2. Top-level comments (reactions are intentionally excluded from governance invalidation freshness
    # to avoid unnecessary invalidation cycles when owner acknowledges PR comments)
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
    if event_name != "schedule" and latest_readiness and latest_readiness.get("state") == "success":
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
    if governance_started_at is None and event_name != "schedule":
        publish(sha, "pending", "Waiting for current Hunter Governance Review.")
        return

    attempts = 45 if poll else 1
    for attempt in range(1, attempts + 1):
        runs = all_check_runs(sha)
        if event_name == "schedule":
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
            publish(
                sha,
                "success",
                "Ready to merge: fresh checks passed and every PR comment is resolved/acknowledged.",
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
