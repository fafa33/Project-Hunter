import json
import os
import re
import urllib.request
from typing import Any

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
                "have passed and implementer readiness metadata is synchronized "
                "to **READY FOR REVIEW**. The operator may now manually mark this "
                "Draft PR **Ready for Review**.\n\n"
                "Hunter Governance Review and Hunter Merge Readiness remain the "
                "merge authorities; review feedback must still be resolved or "
                "explicitly acknowledged under the repository merge policy."
            )
        },
    )


def evaluate(pr: dict[str, Any]) -> None:
    if not pr.get("draft"):
        print(f"PR #{pr.get('number')} is not Draft; skipping.")
        return

    sha = pr["head"]["sha"]
    pr_number = pr["number"]
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

    if waiting:
        publish(sha, "pending", "Waiting for exact-head checks: " + ", ".join(waiting))
        return

    current = request_json("GET", f"pulls/{pr_number}")
    if not current.get("draft") or current.get("head", {}).get("sha") != sha:
        print("PR state/head changed during evaluation; refusing metadata mutation.")
        return

    synchronize_ready_metadata(pr_number, current.get("body") or "")
    publish(sha, "success", "Ready to promote from Draft; metadata synchronized.")
    post_once(pr_number, sha)


def main() -> None:
    init_globals()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")

    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)

    if "pull_request" in event:
        evaluate(event["pull_request"])
        raise SystemExit(0)

    if event_name == "push":
        for pr in open_draft_prs():
            publish(pr["head"]["sha"], "pending", "Target base advanced; waiting for refreshed governance.")
        raise SystemExit(0)

    workflow_run = event.get("workflow_run", {})
    workflow_name = workflow_run.get("name")
    if workflow_name == "Hunter Governance Review Reconcile":
        for pr in open_draft_prs():
            evaluate(pr)
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
