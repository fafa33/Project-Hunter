import json
import os
import re
import urllib.request
from typing import Any

import hunter_governance_preflight as governance_preflight
import hunter_merge_readiness as merge_readiness

# Configuration
context = "Hunter Draft Promotion"
required_checks = ("Quality Gates", "dependency-review", "CodeQL")
governance_context = "Hunter Governance Review"
DECLARATION_LABELS = ("READY FOR REVIEW", "CHANGES REQUIRED", "BLOCKED")
DECLARATION_PATTERN = re.compile(
    r"(?im)^(?P<prefix>\s*[-*+]\s*)\[(?P<mark>[ xX])\](?P<space>[\s`]*)"
    r"(?P<label>READY FOR REVIEW|CHANGES REQUIRED|BLOCKED)(?P<suffix>.*)$"
)

# Global state to allow test mocking
repo: str = ""
token: str = ""
run_url: str = ""


def init_globals() -> None:
    global repo, token, run_url
    repo = os.environ.get("GH_REPO", "")
    if not repo:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN", "")
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
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def publish(sha: str, state: str, description: str) -> None:
    request_json(
        "POST",
        f"statuses/{sha}",
        {
            "state": state,
            "context": context,
            "description": description[:140],
            "target_url": run_url,
        },
    )
    print(f"{context}: {state} — {description}")


def open_draft_prs() -> list[dict[str, Any]]:
    drafts = []
    page = 1
    while True:
        pulls = request_json("GET", f"pulls?state=open&base=main&per_page=100&page={page}")
        drafts.extend(pr for pr in pulls if pr.get("draft"))
        if len(pulls) < 100:
            return drafts
        page += 1


def open_draft_pr_for_sha(sha: str) -> dict[str, Any] | None:
    for pr in open_draft_prs():
        if pr.get("head", {}).get("sha") == sha:
            return pr
    return None


def exact_head_check_runs(sha: str) -> list[dict[str, Any]]:
    runs = []
    page = 1
    while True:
        payload = request_json("GET", f"commits/{sha}/check-runs?per_page=100&page={page}&filter=all")
        batch = payload.get("check_runs", [])
        runs.extend(batch)
        if len(batch) < 100:
            return runs
        page += 1


def latest_commit_status(sha: str, status_context: str) -> dict[str, Any] | None:
    payload = request_json("GET", f"commits/{sha}/status?per_page=100")
    matches = [status for status in payload.get("statuses", []) if status.get("context") == status_context]
    if not matches:
        return None
    return max(matches, key=lambda status: int(status.get("id", 0)))


def review_feedback_blockers(pr_number: int) -> list[str]:
    """Read the same canonical review blockers used by Hunter Merge Readiness.

    Draft promotion is advisory, but it must never claim a PR is ready for
    review while the authoritative merge-readiness reader can already see
    unresolved review feedback. Reuse the canonical readers rather than
    maintaining a second interpretation of review state here.
    """
    merge_readiness.repo = repo
    merge_readiness.repo_owner = repo.split("/", 1)[0] if "/" in repo else ""
    merge_readiness.token = token

    waiting: list[str] = []
    unresolved = merge_readiness.unresolved_review_thread_ids(pr_number)
    if unresolved:
        waiting.append(f"unresolved review threads={len(unresolved)}")

    changes_requested = merge_readiness.current_changes_requested_reviewers(pr_number)
    if changes_requested:
        waiting.append("changes requested by " + ", ".join(changes_requested))

    unacknowledged = merge_readiness.unacknowledged_top_level_comments(pr_number)
    if unacknowledged:
        waiting.append(f"unacknowledged top-level comments={len(unacknowledged)}")

    return waiting


def parse_readiness_declaration(body: str) -> tuple[list[re.Match], str]:
    """Parses the Implementer Readiness Declaration checkboxes in a PR body.

    Only whichever declaration labels the author actually included as lines
    need to obey the "exactly one checked" invariant; the body is not
    required to carry all three labels. Returns the matches (for rewriting)
    and the single checked label. Raises RuntimeError, fail-closed, when the
    declaration is missing, has a duplicated label, or has zero/multiple
    checked declarations.
    """
    matches = list(DECLARATION_PATTERN.finditer(body))
    if not matches:
        raise RuntimeError("No implementer readiness declaration found; refusing automatic metadata mutation.")

    seen_labels: dict[str, int] = {}
    for match in matches:
        label = match.group("label").upper()
        seen_labels[label] = seen_labels.get(label, 0) + 1
    if any(count > 1 for count in seen_labels.values()):
        raise RuntimeError(
            "Implementer readiness declaration has a duplicated label; refusing automatic metadata mutation."
        )

    checked = [match for match in matches if match.group("mark").lower() == "x"]
    if len(checked) != 1:
        raise RuntimeError(
            "Implementer readiness declaration is missing, ambiguous, or unparseable; refusing automatic metadata mutation."
        )

    return matches, checked[0].group("label").upper()


def synchronize_ready_metadata(pr_number: int, body: str) -> None:
    matches, _checked_label = parse_readiness_declaration(body)
    if not any(match.group("label").upper() == "READY FOR REVIEW" for match in matches):
        raise RuntimeError("READY FOR REVIEW declaration is missing; refusing automatic metadata mutation.")

    def replace(match: re.Match) -> str:
        label = match.group("label").upper()
        mark = "x" if label == "READY FOR REVIEW" else " "
        return f"{match.group('prefix')}[{mark}]{match.group('space')}{match.group('label')}{match.group('suffix')}"

    updated = DECLARATION_PATTERN.sub(replace, body)
    if updated == body:
        print("Implementer readiness metadata is already synchronized to READY FOR REVIEW.")
        return

    request_json("PATCH", f"pulls/{pr_number}", {"body": updated})
    print("Synchronized implementer readiness metadata to READY FOR REVIEW while PR remains Draft.")


def post_once(pr_number: int, sha: str) -> None:
    marker = f"<!-- hunter-draft-promotion:{sha} -->"
    page = 1
    while True:
        comments = request_json("GET", f"issues/{pr_number}/comments?per_page=100&page={page}")
        if any(marker in comment.get("body", "") for comment in comments):
            print("Promotion comment already exists for this exact head.")
            return
        if len(comments) < 100:
            break
        page += 1

    request_json(
        "POST",
        f"issues/{pr_number}/comments",
        {
            "body": (
                f"{marker}\n"
                "✅ **Hunter Draft Promotion:** all exact-head prerequisites "
                "and current review-feedback gates have passed, and implementer "
                "readiness metadata is synchronized to **READY FOR REVIEW**. "
                "The operator may now manually mark this Draft PR **Ready for Review**.\n\n"
                "Hunter Governance Review and Hunter Merge Readiness remain the "
                "merge authorities; any newly opened review feedback invalidates "
                "this advisory signal until reconciled."
            )
        },
    )


def current_promotion_scope(pr_number: int) -> dict[str, Any] | None:
    """Return the live PR only when it is inside Draft-promotion authority.

    Event payloads are hints, never authority. In particular, ``issue_comment``
    cannot be branch-filtered by GitHub, so every path must re-read current PR
    state and reject closed PRs, non-main targets, and non-Draft PRs before any
    readiness status or PR metadata can be mutated.
    """
    current = request_json("GET", f"pulls/{pr_number}")
    if (current.get("state") or "").lower() != "open":
        print(f"PR #{pr_number} is not open; skipping Draft promotion evaluation.")
        return None
    if ((current.get("base") or {}).get("ref") or "") != "main":
        print(f"PR #{pr_number} does not target main; skipping Draft promotion evaluation.")
        return None
    if not current.get("draft"):
        print(f"PR #{pr_number} is not Draft; skipping.")
        return None
    return current


def evaluate(pr: dict[str, Any]) -> None:
    pr_number = int(pr.get("number") or 0)
    if not pr_number:
        print("PR payload has no number; refusing Draft promotion evaluation.")
        return

    current = current_promotion_scope(pr_number)
    if current is None:
        return

    sha = current["head"]["sha"]
    latest: dict[str, dict[str, Any]] = {}
    for run in exact_head_check_runs(sha):
        name = run.get("name")
        if name not in required_checks:
            continue
        previous = latest.get(name)
        if previous is None or int(run.get("id", 0)) > int(previous.get("id", 0)):
            latest[name] = run

    waiting = []
    for name in required_checks:
        run = latest.get(name)
        if run is None:
            waiting.append(name)
            continue
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            conclusion = run.get("conclusion") or run.get("status") or "pending"
            waiting.append(f"{name}={conclusion}")

    governance = latest_commit_status(sha, governance_context)
    if governance is None:
        waiting.append(governance_context)
    elif governance.get("state") != "success":
        waiting.append(f"{governance_context}={governance.get('state') or 'pending'}")

    waiting.extend(review_feedback_blockers(pr_number))

    if waiting:
        publish(sha, "pending", "Waiting for Draft promotion prerequisites: " + ", ".join(waiting))
        return

    current = current_promotion_scope(pr_number)
    if current is None or current.get("head", {}).get("sha") != sha:
        print("PR state/head changed during evaluation; refusing metadata mutation.")
        return

    # Re-read review feedback immediately before mutating metadata. This closes
    # the practical race where any canonical feedback blocker is introduced
    # after the initial prerequisite read but before READY FOR REVIEW is synchronized.
    feedback_waiting = review_feedback_blockers(pr_number)
    if feedback_waiting:
        publish(
            sha,
            "pending",
            "Waiting for Draft promotion prerequisites: " + ", ".join(feedback_waiting),
        )
        return

    try:
        governance_preflight.validate_ready_evidence(current.get("body") or "")
        governance_preflight.require_independent_hostile_review(
            repository=repo,
            pr_number=pr_number,
            pr_author=str((current.get("user") or {}).get("login") or ""),
            head_sha=sha,
            base_sha=str((current.get("base") or {}).get("sha") or ""),
        )
    except governance_preflight.PreflightError as exc:
        publish(sha, "pending", f"Waiting for Draft promotion prerequisites: {exc}")
        return

    synchronize_ready_metadata(pr_number, current.get("body") or "")
    publish(sha, "success", "Ready to promote from Draft; checks and review feedback are clear.")
    post_once(pr_number, sha)


def reconcile_open_draft_prs() -> None:
    """Reconcile every open Draft independently, then fail if any evaluation failed.

    A scheduled sweep is a repository-wide reconciliation pass. One malformed PR
    must fail closed for itself without starving later Draft PRs in the same
    sweep. We therefore isolate each evaluation, continue through the full live
    candidate set, and raise one aggregate error only after every Draft had a
    chance to reconcile.
    """
    failures: list[str] = []
    for pr in open_draft_prs():
        pr_number = int(pr.get("number") or 0)
        try:
            evaluate(pr)
        except Exception as exc:
            label = f"PR #{pr_number}" if pr_number else "Draft PR with missing number"
            failures.append(f"{label}: {type(exc).__name__}: {exc}")
            print(f"Draft promotion reconciliation failed for {label}: {type(exc).__name__}: {exc}")

    if failures:
        raise RuntimeError("Draft promotion sweep completed with isolated failures: " + " | ".join(failures))


def main() -> None:
    init_globals()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")

    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)

    if "pull_request" in event:
        evaluate(event["pull_request"])
        raise SystemExit(0)

    if event_name == "issue_comment":
        issue = event.get("issue") or {}
        if "pull_request" not in issue:
            print("issue_comment event is not for a pull request; nothing to evaluate.")
            raise SystemExit(0)
        pr_number = int(issue.get("number") or 0)
        if not pr_number:
            print("issue_comment event has no pull request number; nothing to evaluate.")
            raise SystemExit(0)
        evaluate({"number": pr_number})
        raise SystemExit(0)

    if event_name == "schedule":
        reconcile_open_draft_prs()
        raise SystemExit(0)

    if event_name == "push":
        for pr in open_draft_prs():
            publish(pr["head"]["sha"], "pending", "Target base advanced; waiting for refreshed governance.")
        raise SystemExit(0)

    workflow_run = event.get("workflow_run", {})
    workflow_name = workflow_run.get("name")
    if workflow_name == "Hunter Governance Review Reconcile":
        reconcile_open_draft_prs()
        raise SystemExit(0)

    sha = workflow_run.get("head_sha")
    if not sha:
        print("workflow_run has no head SHA; nothing to evaluate.")
        raise SystemExit(0)
    pr = open_draft_pr_for_sha(sha)
    if pr is None:
        print("No open Draft PR matches the completed workflow exact head.")
        raise SystemExit(0)
    evaluate(pr)


if __name__ == "__main__":
    main()
