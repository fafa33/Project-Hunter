"""Trusted exact-head lifecycle state for Hunter PR review orchestration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import hunter_github_transport as transport
import hunter_pre_ready_review as pre_ready

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
#: Run states GitHub reports for a dispatched run that has been accepted and has
#: not finished. A collector in one of these is alive, so re-dispatching it would
#: only duplicate work.
ACTIVE_RUN_STATES = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})
#: A dead collector is re-dispatched, but never without bound: a cycle that keeps
#: failing must settle into a blocked pending state rather than dispatch forever.
MAX_COLLECTOR_DISPATCHES = 3
#: A dispatch GitHub has accepted is not listed instantly. Liveness is judged
#: only once the cycle is older than this, so an ordinary listing lag cannot be
#: mistaken for a dead collector and duplicate the dispatch.
COLLECTOR_LIVENESS_GRACE_SECONDS = 180
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
    """Self-hosted runner availability, or ``unknown`` when it cannot be read.

    The repository runner endpoint needs Administration:read, which the Actions
    ``GITHUB_TOKEN`` is never granted, so a trusted caller can legitimately get
    403/404 here. Crashing on that answer would fail the whole collector run for
    a probe that is only an optimisation, and treating it as ``offline`` would
    let an unreadable probe skip a reviewer that is actually available. Neither
    is governance evidence: ``unknown`` proceeds to the real authenticated
    invocation, whose acknowledgement budget still fails closed if the runner is
    genuinely absent.
    """

    try:
        payload = request_json(repository, token, "GET", "actions/runners?per_page=100")
    except transport.GitHubRequestError as exc:
        if exc.status_code in {401, 403, 404}:
            return "unknown"
        raise
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


def first_pool_provider() -> str:
    """The first enabled reviewer the trusted pool orders, authority or triage."""

    pool, error = pre_ready.load_reviewer_pool()
    if pool is None or error:
        raise RuntimeError(f"reviewer pool unavailable: {error}")
    reviewers = pre_ready.enabled_pool_reviewers(pool)
    return str(reviewers[0]["id"]) if reviewers else str(pool["last_resort"])


def next_authority_provider(after: str | None = None) -> str:
    """The next hop the trusted pool actually declares, never a retired name.

    Failover order is pool configuration, so it is read from the pool instead of
    being spelled out here: a hard-coded provider silently survives the provider
    leaving the pool and sends the cycle to a reviewer that no longer exists,
    skipping the hosted authority that replaced it. Only authority-eligible
    reviewers can terminate the authority search, so triage-only reviewers are
    never a failover destination; when none remain, the declared last-resort
    guard closes the pool.
    """

    pool, error = pre_ready.load_reviewer_pool()
    if pool is None or error:
        raise RuntimeError(f"reviewer pool unavailable: {error}")
    candidates = [str(agent["id"]) for agent in pre_ready.authority_pool_reviewers(pool)]
    if after is not None and after in candidates:
        candidates = candidates[candidates.index(after) + 1 :]
    return candidates[0] if candidates else str(pool["last_resort"])


def select_provider(repository: str, token: str) -> ProviderDecision:
    state = runner_state(repository, token)
    if state in {"offline", "missing"}:
        return ProviderDecision("FAILOVER_IN_PROGRESS", next_authority_provider(), state)
    return ProviderDecision("REVIEW_IN_PROGRESS", first_pool_provider(), state)


def wait_for_local_ack(backend: Any, *, timeout: int = 30) -> ProviderDecision:
    deadline = backend.now() + timeout
    while backend.now() < deadline:
        if backend.started():
            return ProviderDecision("REVIEW_IN_PROGRESS", first_pool_provider(), "started")
        backend.sleep(min(2, deadline - backend.now()))
    return ProviderDecision("FAILOVER_IN_PROGRESS", next_authority_provider(), "unresponsive")


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


def collector_run_name(pr_number: int, head_sha: str) -> str:
    """The run name the collector workflow renders for this exact dispatch.

    The collector workflow derives its ``run-name`` from the same two inputs, so
    a run carrying this name is a run dispatched for this pull request at this
    exact head. GitHub does not report workflow-dispatch inputs on the run, and
    timestamps or event kind alone would also match an unrelated dispatch.
    """

    return f"Hunter Reviewer Collector PR {pr_number} HEAD {head_sha}"


def collector_runs(repository: str, token: str, pr_number: int, head_sha: str) -> list[dict[str, Any]]:
    payload = request_json(
        repository, token, "GET", f"actions/workflows/{COLLECTOR_WORKFLOW}/runs?event=workflow_dispatch&per_page=50"
    )
    runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
    expected = collector_run_name(pr_number, head_sha)
    return [
        run
        for run in runs
        if isinstance(run, dict)
        and str(run.get("display_title") or run.get("name") or "") == expected
        and str(run.get("path") or "") == COLLECTOR_WORKFLOW_PATH
    ]


def collector_liveness(repository: str, token: str, pr_number: int, head_sha: str) -> tuple[str, int]:
    """Whether a collector for this exact cycle is running or already succeeded."""

    runs = collector_runs(repository, token, pr_number, head_sha)
    for run in runs:
        status = str(run.get("status") or "")
        if status in ACTIVE_RUN_STATES:
            return "active", len(runs)
        if status == "completed" and str(run.get("conclusion") or "") == "success":
            return "completed", len(runs)
    return ("dead" if runs else "missing"), len(runs)


def _older_than(started_at: str, seconds: int) -> bool:
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return (datetime.now(UTC) - started).total_seconds() >= seconds


def collector_needs_dispatch(repository: str, token: str, cycle: ReviewCycle) -> bool:
    """Whether a pending cycle has no live collector and may be re-dispatched.

    A recorded trigger id is only proof that this orchestrator asked for a
    collector; it is not proof that the collector exists, is running, or
    finished. A dispatch that failed, was cancelled, or never produced a run
    would otherwise park the cycle in a pending state forever, because the
    trigger id alone suppressed every later dispatch. Liveness is therefore read
    from the collector runs correlated to this exact pull request and head.

    Re-dispatch stays bounded on both sides: nothing is re-dispatched inside the
    listing grace period, so an accepted-but-unlisted run is not duplicated, and
    a cycle that has already consumed its dispatch budget stays pending rather
    than dispatching without end.
    """

    if not _older_than(cycle.started_at, COLLECTOR_LIVENESS_GRACE_SECONDS):
        return False
    try:
        liveness, count = collector_liveness(repository, token, cycle.pr_number, cycle.head_sha)
    except transport.GitHubRequestError as exc:
        # Unreadable liveness evidence is not evidence of a dead collector.
        print(f"Collector liveness evidence unavailable; not re-dispatching: {exc}", file=sys.stderr)
        return False
    if liveness in {"active", "completed"}:
        return False
    if count >= MAX_COLLECTOR_DISPATCHES:
        print(
            f"Collector dispatch budget exhausted for PR #{cycle.pr_number} at {cycle.head_sha[:10]} "
            f"after {count} runs; review remains pending.",
            file=sys.stderr,
        )
        return False
    return True


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
        if existing.state in {"REVIEW_CLEAR", "FINDINGS_OPEN", "POOL_EXHAUSTED"}:
            return existing
        if existing.trigger_id is not None and not collector_needs_dispatch(repository, token, existing):
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
    if not (
        isinstance(request, dict)
        and request.get("schema") == "hunter.review-request.v1"
        and isinstance(request.get("claims_id"), str)
        and len(request["claims_id"]) == 64
    ):
        return False
    valid, _reason = governance.valid_current_review_request(repository, token, pr_number, head_sha, document)
    return valid


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
