"""Low-friction Hunter Merge Readiness controller.

This controller intentionally evaluates only current merge risk. Process history,
PR prose, branch naming, top-level comments, reactions, and metadata edits are
not merge authority.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import hunter_github_transport as transport

CONTEXT = "Hunter Merge Readiness"
GOVERNANCE_CONTEXT = "Hunter Governance Review"
REQUIRED_CHECKS = ("Quality Gates", "dependency-review", "CodeQL")
HARD_FAILURES = {"failure", "timed_out", "action_required", "startup_failure"}

REPO = os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY") or ""
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
RUN_URL = os.environ.get("RUN_URL") or ""


@dataclass(frozen=True)
class Decision:
    state: str
    description: str


def request_json(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    if not REPO:
        raise RuntimeError("GH_REPO or GITHUB_REPOSITORY is required")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    return transport.request_rest_json(
        url=f"https://api.github.com/repos/{REPO}/{path}",
        method=method,
        headers={},
        data=data,
        token=TOKEN,
        what=f"{method} {path}",
    )


def graphql_json(query: str, variables: dict[str, Any]) -> Any:
    return transport.request_graphql_json(
        url="https://api.github.com/graphql",
        headers={},
        query=query,
        variables=variables,
        token=TOKEN,
        what="GraphQL query",
    )


def paged(path: str) -> list[Any]:
    items: list[Any] = []
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        batch = request_json("GET", f"{path}{separator}per_page=100&page={page}")
        if not isinstance(batch, list):
            raise RuntimeError(f"Expected a list from {path}")
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def all_check_runs(sha: str) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = request_json("GET", f"commits/{sha}/check-runs?per_page=100&page={page}&filter=all")
        batch = list((payload or {}).get("check_runs") or [])
        runs.extend(run for run in batch if isinstance(run, dict))
        if len(batch) < 100:
            return runs
        page += 1


def latest_check(runs: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    matches = [run for run in runs if run.get("name") == name]
    if not matches:
        return None
    return max(matches, key=lambda run: int(run.get("id") or 0))


def latest_status(sha: str, context: str) -> dict[str, Any] | None:
    statuses = paged(f"commits/{sha}/statuses")
    matches = [status for status in statuses if isinstance(status, dict) and status.get("context") == context]
    if not matches:
        return None
    return max(matches, key=lambda status: int(status.get("id") or 0))


_REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        nodes { id isResolved }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


def unresolved_review_threads(pr_number: int) -> tuple[str, ...]:
    owner, name = REPO.split("/", 1)
    cursor = None
    unresolved: list[str] = []
    while True:
        data = graphql_json(
            _REVIEW_THREADS_QUERY,
            {"owner": owner, "name": name, "number": int(pr_number), "after": cursor},
        )
        threads = (((data or {}).get("repository") or {}).get("pullRequest") or {}).get("reviewThreads") or {}
        for index, node in enumerate(threads.get("nodes") or []):
            if not node.get("isResolved"):
                unresolved.append(str(node.get("id") or f"thread:{index}"))
        page_info = threads.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return tuple(unresolved)
        cursor = page_info.get("endCursor")


def changes_requested_reviewers(pr_number: int) -> tuple[str, ...]:
    latest: dict[str, dict[str, Any]] = {}
    for review in paged(f"pulls/{pr_number}/reviews"):
        if not isinstance(review, dict):
            continue
        login = str((review.get("user") or {}).get("login") or "").strip()
        state = str(review.get("state") or "").upper()
        if not login or state not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            continue
        previous = latest.get(login)
        if previous is None or int(review.get("id") or 0) > int(previous.get("id") or 0):
            latest[login] = review
    return tuple(
        sorted(
            login for login, review in latest.items() if str(review.get("state") or "").upper() == "CHANGES_REQUESTED"
        )
    )


def open_prs_for_head(sha: str) -> tuple[int, ...]:
    if not sha:
        return ()
    numbers = []
    for pr in paged(f"commits/{sha}/pulls"):
        if not isinstance(pr, dict):
            continue
        if str(pr.get("state") or "") != "open":
            continue
        if str((pr.get("head") or {}).get("sha") or "") != sha:
            continue
        numbers.append(int(pr["number"]))
    return tuple(sorted(set(numbers)))


def decide(pr_number: int) -> tuple[str, Decision] | None:
    pr = request_json("GET", f"pulls/{pr_number}")
    if not isinstance(pr, dict) or pr.get("state") != "open":
        return None

    head_sha = str((pr.get("head") or {}).get("sha") or "").strip()
    if not head_sha:
        return "", Decision("pending", "Waiting: current PR head SHA is unavailable.")

    if bool(pr.get("draft")):
        return head_sha, Decision("pending", "Waiting for Ready for Review (PR is Draft).")

    mergeable = pr.get("mergeable")
    if mergeable is False:
        return head_sha, Decision("failure", "Merge conflict detected; update or resolve the branch.")
    if mergeable is None:
        return head_sha, Decision("pending", "Waiting for GitHub to resolve current mergeability.")

    threads = unresolved_review_threads(pr_number)
    if threads:
        return head_sha, Decision("failure", f"Unresolved review threads remain: {len(threads)}.")

    reviewers = changes_requested_reviewers(pr_number)
    if reviewers:
        return head_sha, Decision("failure", "Changes requested by: " + ", ".join(reviewers))

    runs = all_check_runs(head_sha)
    missing: list[str] = []
    pending: list[str] = []
    failed: list[str] = []
    for name in REQUIRED_CHECKS:
        run = latest_check(runs, name)
        if run is None:
            missing.append(name)
            continue
        if run.get("status") != "completed":
            pending.append(name)
            continue
        conclusion = str(run.get("conclusion") or "")
        if conclusion == "success":
            continue
        if conclusion in HARD_FAILURES:
            failed.append(f"{name}={conclusion}")
        else:
            pending.append(name)

    governance = latest_status(head_sha, GOVERNANCE_CONTEXT)
    if governance is None:
        missing.append(GOVERNANCE_CONTEXT)
    else:
        state = str(governance.get("state") or "pending")
        if state == "success":
            pass
        elif state in {"failure", "error"}:
            failed.append(f"{GOVERNANCE_CONTEXT}={state}")
        elif state == "pending" and mergeable is True:
            # The lightweight governance controller can publish pending only while
            # GitHub mergeability is unresolved. We already re-read the live PR
            # above and observed mergeable=True, so an older pending status is
            # stale and must not become a permanent merge lock.
            pass
        else:
            pending.append(GOVERNANCE_CONTEXT)

    if failed:
        return head_sha, Decision("failure", "Merge prerequisite failed: " + ", ".join(failed))
    if missing or pending:
        return head_sha, Decision("pending", "Waiting for current-head checks: " + ", ".join(missing + pending))

    shared = tuple(number for number in open_prs_for_head(head_sha) if number != int(pr_number))
    if shared:
        others = ", ".join(f"#{number}" for number in shared)
        return head_sha, Decision("pending", f"Head is shared with open PR {others}; waiting for unique attribution.")

    return head_sha, Decision(
        "success",
        "Ready to merge: code/security checks pass and no active review blocker remains.",
    )


def publish(sha: str, decision: Decision) -> None:
    if not sha:
        return
    description = decision.description[:140]
    request_json(
        "POST",
        f"statuses/{sha}",
        {
            "state": decision.state,
            "context": CONTEXT,
            "description": description,
            "target_url": RUN_URL,
        },
    )
    print(f"{sha[:10]} {CONTEXT}: {decision.state} — {description}")


def open_pull_requests() -> tuple[int, ...]:
    numbers: list[int] = []
    page = 1
    while True:
        batch = request_json("GET", f"pulls?state=open&base=main&per_page=100&page={page}")
        if not isinstance(batch, list):
            raise RuntimeError("Open PR listing did not return a list")
        numbers.extend(int(pr["number"]) for pr in batch if isinstance(pr, dict) and pr.get("number"))
        if len(batch) < 100:
            return tuple(sorted(set(numbers)))
        page += 1


def event_payload() -> dict[str, Any]:
    path = os.environ.get("GITHUB_EVENT_PATH") or ""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def candidate_prs() -> tuple[int, ...]:
    event = event_payload()
    pull = event.get("pull_request") or {}
    if isinstance(pull, dict) and pull.get("number"):
        return (int(pull["number"]),)

    issue = event.get("issue") or {}
    if isinstance(issue, dict) and issue.get("number") and issue.get("pull_request"):
        return (int(issue["number"]),)

    workflow_run = event.get("workflow_run") or {}
    if isinstance(workflow_run, dict):
        numbers = [
            int(item["number"])
            for item in workflow_run.get("pull_requests") or []
            if isinstance(item, dict) and item.get("number")
        ]
        if numbers:
            return tuple(sorted(set(numbers)))
        head_sha = str(workflow_run.get("head_sha") or "").strip()
        if head_sha:
            return open_prs_for_head(head_sha)

    inputs = event.get("inputs") or {}
    if isinstance(inputs, dict) and inputs.get("pr_number"):
        return (int(inputs["pr_number"]),)

    return open_pull_requests()


def main() -> int:
    try:
        numbers = candidate_prs()
    except transport.GitHubUnavailable as exc:
        print(f"Readiness infrastructure unavailable: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Readiness controller failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"Reconciling PRs: {list(numbers)}")
    failures = 0
    for number in numbers:
        try:
            result = decide(number)
            if result is None:
                continue
            sha, decision = result
            publish(sha, decision)
        except transport.GitHubUnavailable as exc:
            failures += 1
            print(f"PR #{number} readiness infrastructure unavailable: {exc}", file=sys.stderr)
        except Exception as exc:
            failures += 1
            print(f"PR #{number} readiness failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
