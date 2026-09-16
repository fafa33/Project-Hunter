"""Trusted exact-head lifecycle state for Hunter PR review orchestration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import hunter_github_transport as transport

CONTEXT_PREFIX = "Hunter Review Orchestration / PR #"
COLLECTOR_CONTEXT_PREFIX = "Hunter Reviewer Collector / PR #"
COLLECTOR_WORKFLOW_PATH = ".github/workflows/hunter-reviewer-collector.yml"
TRUSTED_STATUS_CREATOR = "github-actions[bot]"
ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "docs" / "CODE_WRITE_POLICY.json"
COLLECTOR_WORKFLOW = "hunter-reviewer-collector.yml"
TRUSTED_WORKFLOWS = frozenset(
    {
        ".github/workflows/hunter-governance-review.yml",
        ".github/workflows/hunter-governance-reconcile.yml",
    }
)
PENDING_STATES = frozenset({"WAITING_FOR_REVIEWER", "REVIEW_IN_PROGRESS", "FAILOVER_IN_PROGRESS", "POOL_EXHAUSTED"})


@dataclass(frozen=True)
class ReviewCycle:
    pr_number: int
    head_sha: str
    state: str
    provider_id: str
    trigger_id: int | None
    started_at: str
    config_digest: str


@dataclass(frozen=True)
class ProviderDecision:
    state: str
    next_provider: str
    reason: str


def runner_state(repository: str, token: str, label: str = "hunter-reviewer") -> str:
    payload = request_json(repository, token, "GET", "actions/runners?per_page=100")
    runners = payload.get("runners", []) if isinstance(payload, dict) else []
    matches = []
    for runner in runners if isinstance(runners, list) else []:
        if not isinstance(runner, dict):
            continue
        labels = {str(item.get("name") or "") for item in runner.get("labels", []) if isinstance(item, dict)}
        if label in labels:
            matches.append(runner)
    if not matches:
        return "missing"
    online = [runner for runner in matches if str(runner.get("status") or "") == "online"]
    if not online:
        return "offline"
    return "busy" if all(bool(runner.get("busy")) for runner in online) else "online"


def select_provider(repository: str, token: str) -> ProviderDecision:
    state = runner_state(repository, token)
    if state in {"offline", "missing"}:
        return ProviderDecision("FAILOVER_IN_PROGRESS", "opencode", state)
    return ProviderDecision("REVIEW_IN_PROGRESS", "local-ollama", state)


def wait_for_local_ack(backend: Any, *, timeout: int = 30) -> ProviderDecision:
    deadline = backend.now() + timeout
    while backend.now() < deadline:
        if backend.started():
            return ProviderDecision("REVIEW_IN_PROGRESS", "local-ollama", "started")
        backend.sleep(min(2, deadline - backend.now()))
    return ProviderDecision("FAILOVER_IN_PROGRESS", "opencode", "unresponsive")


def classify_cycle(cycle: ReviewCycle, current_head: str) -> str:
    return "SUPERSEDED" if cycle.head_sha != current_head else cycle.state


def governance_projection(cycle: ReviewCycle) -> tuple[str, str]:
    if cycle.state in PENDING_STATES:
        return "pending", cycle.state
    if cycle.state == "REVIEW_CLEAR":
        return "success", cycle.state
    if cycle.state == "FINDINGS_OPEN":
        return "failure", cycle.state
    return "pending", cycle.state


def request_json(
    repository: str,
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    suffix = f"/{path}" if path else ""
    return transport.request_rest_json(
        url=f"https://api.github.com/repos/{repository}{suffix}",
        method=method,
        headers={},
        data=data,
        token=token,
        what=f"{method} {path or repository}",
    )


def _run_id(target_url: str) -> int | None:
    path = urlparse(target_url).path.rstrip("/").split("/")
    if len(path) < 3 or path[-2] != "runs" or not path[-1].isdigit():
        return None
    return int(path[-1])


def _parse_cycle(status: dict[str, Any], pr_number: int, head_sha: str) -> ReviewCycle | None:
    if str((status.get("creator") or {}).get("login") or "") != TRUSTED_STATUS_CREATOR:
        return None
    if str(status.get("context") or "") != f"{CONTEXT_PREFIX}{pr_number}":
        return None
    parts = str(status.get("description") or "").split("|")
    if len(parts) != 4 or len(parts[3]) != 64:
        return None
    try:
        trigger = int(parts[2]) or None
    except ValueError:
        return None
    return ReviewCycle(
        pr_number=pr_number,
        head_sha=head_sha,
        state=parts[0],
        provider_id=parts[1],
        trigger_id=trigger,
        started_at=str(status.get("created_at") or ""),
        config_digest=parts[3],
    )


def read_cycle(
    repository: str, token: str, pr_number: int, head_sha: str
) -> tuple[str, ReviewCycle | None, str | None]:
    pr = request_json(repository, token, "GET", f"pulls/{pr_number}")
    if not isinstance(pr, dict) or str(pr.get("state") or "") != "open":
        return "absent", None, "pull request is not open"
    current_head = str((pr.get("head") or {}).get("sha") or "")
    if current_head != head_sha:
        return "superseded", None, f"current head is {current_head or 'unavailable'}"

    combined = request_json(repository, token, "GET", f"commits/{head_sha}/status")
    statuses = combined.get("statuses", []) if isinstance(combined, dict) else []
    repository_info = request_json(repository, token, "GET", "")
    default_branch = str((repository_info or {}).get("default_branch") or "main")
    for status in statuses:
        if not isinstance(status, dict):
            continue
        cycle = _parse_cycle(status, pr_number, head_sha)
        if cycle is None:
            continue
        run_id = _run_id(str(status.get("target_url") or ""))
        if run_id is None:
            continue
        run = request_json(repository, token, "GET", f"actions/runs/{run_id}")
        if not isinstance(run, dict):
            continue
        if str(run.get("head_branch") or "") != default_branch:
            continue
        if str(run.get("path") or "") not in TRUSTED_WORKFLOWS:
            continue
        return "present", cycle, None
    return "absent", None, None


def publish_collector_completion(repository: str, token: str, pr_number: int, head_sha: str, run_id: int) -> None:
    server = os.environ.get("GITHUB_SERVER_URL") or "https://github.com"
    request_json(
        repository,
        token,
        "POST",
        f"statuses/{head_sha}",
        {
            "state": "success",
            "context": f"{COLLECTOR_CONTEXT_PREFIX}{pr_number}",
            "description": f"collector_run_id={run_id}",
            "target_url": f"{server}/{repository}/actions/runs/{run_id}",
        },
    )


def read_collector_completion(
    repository: str, token: str, pr_number: int, head_sha: str
) -> tuple[str, int | None, str | None]:
    try:
        statuses = request_json(repository, token, "GET", f"commits/{head_sha}/statuses?per_page=100")
        if not isinstance(statuses, list):
            return "absent", None, "collector status payload is malformed"
        repository_info = request_json(repository, token, "GET", "")
        default_branch = str((repository_info or {}).get("default_branch") or "main")
        revision = request_json(repository, token, "GET", f"commits/{default_branch}")
        trusted_revision = str((revision or {}).get("sha") or "")
        candidates = []
        for status in statuses:
            if not isinstance(status, dict):
                continue
            if status.get("state") != "success" or status.get("context") != f"{COLLECTOR_CONTEXT_PREFIX}{pr_number}":
                continue
            if str((status.get("creator") or {}).get("login") or "") != TRUSTED_STATUS_CREATOR:
                continue
            description = str(status.get("description") or "")
            if not description.startswith("collector_run_id=") or not description.split("=", 1)[1].isdigit():
                continue
            run_id = int(description.split("=", 1)[1])
            run = request_json(repository, token, "GET", f"actions/runs/{run_id}")
            if not isinstance(run, dict):
                continue
            run_revision = str(run.get("head_sha") or "")
            revision_trusted = run_revision == trusted_revision
            if run_revision and trusted_revision and not revision_trusted:
                comparison = request_json(repository, token, "GET", f"compare/{run_revision}...{trusted_revision}")
                revision_trusted = isinstance(comparison, dict) and comparison.get("status") in {"ahead", "identical"}
            if (
                run.get("id") == run_id
                and run.get("head_branch") == default_branch
                and revision_trusted
                and run.get("path") == COLLECTOR_WORKFLOW_PATH
                and run.get("event") == "workflow_dispatch"
                and run.get("status") == "completed"
                and run.get("conclusion") == "success"
            ):
                candidates.append(run_id)
        return ("present", max(candidates), None) if candidates else ("absent", None, None)
    except Exception as exc:
        return "absent", None, f"collector completion evidence unavailable: {type(exc).__name__}: {exc}"


def reviewer_pool_config_digest() -> str:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    pool = policy["review_progression"]["review_authority"]["reviewer_pool"]
    encoded = json.dumps(pool, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def current_run_id() -> int | None:
    raw = os.environ.get("GITHUB_RUN_ID") or ""
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def dispatch_collector(repository: str, token: str, pr_number: int, head_sha: str) -> None:
    request_json(
        repository,
        token,
        "POST",
        f"actions/workflows/{COLLECTOR_WORKFLOW}/dispatches",
        {"ref": "main", "inputs": {"pr_number": str(pr_number), "head_sha": head_sha}},
    )


def publish_cycle(
    repository: str,
    token: str,
    head_sha: str,
    *,
    cycle: ReviewCycle,
) -> None:
    trigger = cycle.trigger_id or 0
    description = f"{cycle.state}|{cycle.provider_id}|{trigger}|{cycle.config_digest}"
    run_id = current_run_id()
    server = os.environ.get("GITHUB_SERVER_URL") or "https://github.com"
    target_url = f"{server}/{repository}/actions/runs/{run_id}" if run_id else ""
    request_json(
        repository,
        token,
        "POST",
        f"statuses/{head_sha}",
        {
            "state": "pending" if cycle.state != "REVIEW_CLEAR" else "success",
            "context": f"{CONTEXT_PREFIX}{cycle.pr_number}",
            "description": description,
            "target_url": target_url,
        },
    )


def ensure_collector(repository: str, token: str, pr_number: int, head_sha: str) -> ReviewCycle:
    digest = reviewer_pool_config_digest()
    state, existing, _error = read_cycle(repository, token, pr_number, head_sha)
    if state == "present" and existing is not None and existing.config_digest == digest:
        if existing.trigger_id is not None or existing.state in {"REVIEW_CLEAR", "FINDINGS_OPEN", "POOL_EXHAUSTED"}:
            return existing

    cycle = ReviewCycle(
        pr_number=pr_number,
        head_sha=head_sha,
        state="WAITING_FOR_REVIEWER",
        provider_id="",
        trigger_id=None,
        started_at="",
        config_digest=digest,
    )
    publish_cycle(repository, token, head_sha, cycle=cycle)
    dispatch_collector(repository, token, pr_number, head_sha)
    run_id = current_run_id()
    if run_id is None:
        raise RuntimeError("trusted orchestration requires GITHUB_RUN_ID")
    cycle = replace(cycle, trigger_id=run_id)
    publish_cycle(repository, token, head_sha, cycle=cycle)
    return cycle


def review_prerequisites_ready(repository: str, token: str, pr_number: int, head_sha: str) -> bool:
    import hunter_governance_review_v2 as governance

    preflight_state, _ = governance.read_trusted_upgrade_status(repository, token, head_sha, pr_number)
    if preflight_state != "success":
        return False
    request_state, document, _ = governance.read_head_pre_ready_review(repository, token, head_sha)
    if request_state != "present" or not isinstance(document, dict):
        return False
    request = document.get("review_request")
    return (
        isinstance(request, dict)
        and request.get("schema") == "hunter.review-request.v1"
        and isinstance(request.get("claims_id"), str)
        and len(request["claims_id"]) == 64
    )


def ensure_current(repository: str, token: str, pr_number: int) -> ReviewCycle | None:
    pr = request_json(repository, token, "GET", f"pulls/{pr_number}")
    if not isinstance(pr, dict) or str(pr.get("state") or "") != "open":
        return None
    head_sha = str((pr.get("head") or {}).get("sha") or "")
    if not head_sha:
        raise RuntimeError("current pull-request head is unavailable")
    if not review_prerequisites_ready(repository, token, pr_number, head_sha):
        return None
    return ensure_collector(repository, token, pr_number, head_sha)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Hunter trusted review orchestrator")
    sub = result.add_subparsers(dest="command", required=True)
    ensure = sub.add_parser("ensure")
    ensure.add_argument("--repository", required=True)
    ensure.add_argument("--pr", type=int, required=True)
    complete = sub.add_parser("collector-complete")
    complete.add_argument("--repository", required=True)
    complete.add_argument("--pr", type=int, required=True)
    complete.add_argument("--head", required=True)
    complete.add_argument("--run-id", type=int, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    try:
        if args.command == "collector-complete":
            publish_collector_completion(args.repository, token, args.pr, args.head, args.run_id)
        else:
            ensure_current(args.repository, token, args.pr)
        return 0
    except transport.GitHubUnavailable as exc:
        print(f"Review orchestration infrastructure unavailable; remaining pending: {exc}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"Review orchestration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
