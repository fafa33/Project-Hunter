"""Trusted exact-head lifecycle state for Hunter PR review orchestration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple
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
# One global opportunity budget across the whole reviewer chain. A completed or
# timed-out opportunity must never leave the exact-head status pending forever.
INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS = 15 * 60
TERMINAL_NONBLOCKING_STATES = frozenset({"REVIEW_TIMED_OUT", "REVIEWER_UNAVAILABLE", "POOL_EXHAUSTED"})
PENDING_STATES = frozenset({"WAITING_FOR_REVIEWER", "REVIEW_IN_PROGRESS", "FAILOVER_IN_PROGRESS", "POOL_EXHAUSTED"})

#: Issue #461 / PR #473: an exact-head cycle whose reviewers were all exhausted
#: used to be the end of the line. Remediating the blocking findings it produced
#: could never start another review of the same immutable head, so the candidate
#: stayed permanently MISSING_REVIEW_AUTHORITY. A remediation generation is the
#: bounded, evidence-derived permission to start exactly one more cycle for the
#: same head and claims, and its identity is a digest of trusted GitHub state.
REMEDIATION_GENERATION_SCHEMA = "hunter.remediation-generation.v1"
GENERATION_ID_PATTERN = re.compile(r"[0-9a-f]{16}")
#: The generation of a candidate whose authenticated blocking findings have never
#: been resolved -- the first exact-head cycle. It is the empty string rather than
#: a digest so that every identity derived from it (collector run name, reviewer
#: invocation key, cycle status) stays byte-identical to the pre-remediation form,
#: and an in-flight cycle is therefore never restarted by deploying this change.
BASE_GENERATION_ID = ""
#: How a non-base generation is rendered into the collector run name.
GENERATION_RUN_NAME_SEPARATOR = " GEN "
#: Remediation is bounded exactly like collector dispatch is: one exact head may
#: spend at most this many generations in total (its first cycle plus its
#: review-after-remediation cycles). Past that the candidate stays pending rather
#: than looping reviewers, and a new HEAD -- a real new candidate -- starts over.
MAX_REMEDIATION_GENERATIONS = 3
#: Hunter's own automation identity. It authors the reviewer trigger comments, so
#: a thread it opened is never independent review evidence and must never be able
#: to mint the permission to dispatch another review of its own.
HUNTER_AUTOMATION_LOGIN = "github-actions[bot]"


def _normalized_bot_login(value: str) -> str:
    """Normalize GitHub App logins without weakening configured identity binding."""

    login = str(value or "").strip().lower()
    return login[:-5] if login.endswith("[bot]") else login


def authority_reviewer_logins() -> frozenset[str]:
    """Configured authenticated reviewer identities allowed to mint remediation."""

    pool, error = pre_ready.load_reviewer_pool()
    if pool is None or error:
        raise RuntimeError(f"reviewer pool unavailable: {error}")
    return frozenset(
        _normalized_bot_login(str(agent.get("github_login") or ""))
        for agent in pre_ready.authority_pool_reviewers(pool)
        if str(agent.get("github_login") or "").strip()
    )


_BLOCKING_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      author { login }
      reviewThreads(first: 100, after: $after) {
        nodes {
          id
          isResolved
          comments(first: 1) { nodes { databaseId createdAt author { login __typename } } }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class ReviewCycle:
    pr_number: int
    head_sha: str
    state: str
    provider_id: str
    trigger_id: int | None
    started_at: str
    config_digest: str
    #: Which remediation generation of this exact head/claims the cycle belongs
    #: to. Defaulted to the base generation so a status published before
    #: remediation generations existed reads as the candidate's first cycle.
    generation_id: str = BASE_GENERATION_ID


@dataclass(frozen=True)
class BlockingThread:
    """An authenticated reviewer finding thread and whether it is resolved."""

    thread_id: str
    comment_id: int
    created_at: str
    resolved: bool


@dataclass(frozen=True)
class ProviderDecision:
    state: str
    next_provider: str
    reason: str


def blocking_reviewer_threads(repository: str, token: str, pr_number: int) -> tuple[BlockingThread, ...]:
    """Every authenticated reviewer finding thread on this pull request.

    A thread counts only when an authenticated reviewer *app* opened it, and that
    app is neither Hunter's own automation nor the pull-request author. That is
    what makes a remediation generation unforgeable: a candidate cannot author,
    impersonate or fabricate one of these threads, so candidate prose -- or a
    thread the candidate opened on its own pull request and then resolved --
    can never mint the permission to start another reviewer cycle. Human review
    threads are deliberately excluded for the same reason: a repository member is
    not a machine-checkable reviewer identity here, and under-counting only ever
    withholds an extra review, never grants one.

    Unreadable or malformed thread evidence raises. Unknown thread state must
    fail closed: silently reading it as "nothing was blocking" would collapse the
    generation to the base one and hand the candidate a free dispatch.
    """

    owner, name = repository.split("/", 1)
    trusted_reviewers = authority_reviewer_logins()
    hunter_login = _normalized_bot_login(HUNTER_AUTOMATION_LOGIN)
    cursor: str | None = None
    seen: set[str] = set()
    threads: list[BlockingThread] = []
    while True:
        data = transport.request_graphql_json(
            url="https://api.github.com/graphql",
            headers={},
            token=token,
            query=_BLOCKING_THREADS_QUERY,
            variables={"owner": owner, "name": name, "number": pr_number, "after": cursor},
            what="remediation generation review threads",
        )
        pull_request = data["repository"]["pullRequest"]
        author = str((pull_request.get("author") or {}).get("login") or "").lower()
        page = pull_request["reviewThreads"]["pageInfo"]
        nodes = pull_request["reviewThreads"]["nodes"]
        if not isinstance(nodes, list) or not isinstance(page.get("hasNextPage"), bool):
            raise ValueError("malformed review thread pagination")
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("isResolved"), bool) or not node.get("id"):
                raise ValueError("malformed review thread")
            comments = (node.get("comments") or {}).get("nodes") or []
            if not isinstance(comments, list) or not comments or not isinstance(comments[0], dict):
                raise ValueError("malformed review thread comment evidence")
            opening = comments[0]
            opener = opening.get("author") or {}
            login = _normalized_bot_login(str(opener.get("login") or ""))
            comment_id = opening.get("databaseId")
            created_at = str(opening.get("createdAt") or "")
            if type(comment_id) is not int or comment_id <= 0 or not created_at:
                raise ValueError("malformed review thread comment identity")
            if opener.get("__typename") != "Bot":
                continue
            if (
                not login
                or login == hunter_login
                or login == _normalized_bot_login(author)
                or login not in trusted_reviewers
            ):
                continue
            # Hunter governance treats every unresolved inline review thread as a
            # blocking finding. A resolved thread from a configured authenticated
            # authority reviewer is therefore durable evidence that a real blocker
            # existed and was remediated; unrelated bots cannot mint generations.
            threads.append(BlockingThread(str(node["id"]), comment_id, created_at, bool(node["isResolved"])))
        if not page["hasNextPage"]:
            return tuple(threads)
        cursor = page.get("endCursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise ValueError("invalid review thread cursor")
        seen.add(cursor)


def remediation_generation_id(head_sha: str, claims_id: str, resolved: Iterable[BlockingThread]) -> str:
    """The deterministic identity of a review-after-remediation generation.

    The digest binds the exact head and the claims digest as well as the whole
    resolved blocking-thread set, so a generation minted for one head or one set
    of claims can never be replayed into another, and it advances only when the
    trusted thread set itself actually changes.
    """

    material = json.dumps(
        {
            "schema": REMEDIATION_GENERATION_SCHEMA,
            "head_sha": head_sha,
            "claims_id": claims_id,
            "resolved_blocking_threads": sorted(
                [thread.thread_id, thread.comment_id, thread.created_at] for thread in resolved
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def current_remediation_generation(repository: str, token: str, pr_number: int, head_sha: str, claims_id: str) -> str:
    """The generation this exact head and claims is currently entitled to review.

    It stays the base generation until an authenticated blocking review thread
    has actually been resolved, and from then on it is the digest above. So the
    identity advances exactly once per real remediation of authenticated
    findings, and never on its own, on a re-run, or on candidate assertion.
    """

    resolved = tuple(thread for thread in blocking_reviewer_threads(repository, token, pr_number) if thread.resolved)
    if not resolved:
        return BASE_GENERATION_ID
    return remediation_generation_id(head_sha, claims_id, resolved)


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
    if cycle.state == "REVIEW_CLEAR" or cycle.state in TERMINAL_NONBLOCKING_STATES:
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
    # Provenance of the cycle payload.
    #
    # A commit status is only as trustworthy as the account that posted it, and
    # the context, description and target_url are all caller-controlled, so the
    # publisher must be authenticated before any of them is believed. That check
    # is strictly required: there is no creator-less path, no age-based or
    # status-age authentication, and no caller-controlled target_url standing in
    # for provenance.
    #
    # The strict form used to be impossible, which is why a creator-less fallback
    # was introduced and then hardened twice. The actual cause was the reading
    # endpoint, not the publisher: `read_cycle` read the combined status endpoint,
    # which reports `creator: null` for every status including trusted workflow
    # posts. It now reads the status list endpoint, which carries the real
    # publisher, so the strict check stands on its own and the derivation path is
    # gone. See `read_cycle` for the live evidence.
    #
    # `read_cycle` remains the outer bound: it independently re-fetches the run
    # the status cites and requires it to be on the default branch and to come from
    # a trusted governance workflow.
    if str((status.get("creator") or {}).get("login") or "") != TRUSTED_STATUS_CREATOR:
        return None
    if str(status.get("context") or "") != f"{CONTEXT_PREFIX}{pr_number}":
        return None
    parts = str(status.get("description") or "").split("|")
    # A four-field description predates remediation generations, so it describes
    # the candidate's first cycle: the base generation, by definition.
    if len(parts) == 4:
        parts = [*parts, BASE_GENERATION_ID]
    if len(parts) != 5 or len(parts[3]) != 64:
        return None
    if parts[4] != BASE_GENERATION_ID and not GENERATION_ID_PATTERN.fullmatch(parts[4]):
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
        generation_id=parts[4],
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

    # Provenance-sensitive status reading uses the status LIST endpoint, not the
    # combined endpoint. `GET /commits/{sha}/status` returns every status with
    # `creator: null`, while `GET /commits/{sha}/statuses?per_page=100` returns the
    # same status objects with the real publisher populated. Verified live on this
    # repository: for one identical status id the combined endpoint reported
    # `creator: null` while the list endpoint reported `github-actions[bot]`. The
    # combined endpoint simply does not carry the publisher, so a creator check
    # read from it can never be satisfied -- which is what previously forced a
    # creator-less fallback that trusted a caller-controlled payload. Reading the
    # list endpoint restores a real authenticated publisher and lets the strict
    # check in `_parse_cycle` stand on its own. Only the latest statuses are
    # listed, and the context, description, digest, generation and cited run are
    # all still re-verified below and in `_parse_cycle`.
    listed = request_json(repository, token, "GET", f"commits/{head_sha}/statuses?per_page=100")
    statuses = listed if isinstance(listed, list) else []
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


def dispatch_collector(
    repository: str, token: str, pr_number: int, head_sha: str, generation_id: str = BASE_GENERATION_ID
) -> None:
    request_json(
        repository,
        token,
        "POST",
        f"actions/workflows/{COLLECTOR_WORKFLOW}/dispatches",
        {
            "ref": "main",
            "inputs": {"pr_number": str(pr_number), "head_sha": head_sha, "generation_id": generation_id},
        },
    )


def collector_run_name(pr_number: int, head_sha: str, generation_id: str = BASE_GENERATION_ID) -> str:
    """The run name the collector workflow renders for this exact dispatch.

    The collector workflow derives its ``run-name`` from the same inputs, so a
    run carrying this name is a run dispatched for this pull request at this
    exact head and remediation generation. GitHub does not report
    workflow-dispatch inputs on the run, and timestamps or event kind alone would
    also match an unrelated dispatch. The base generation renders no suffix, so a
    cycle that started before remediation generations existed keeps correlating
    to its own already-listed runs.
    """

    name = f"Hunter Reviewer Collector PR {pr_number} HEAD {head_sha}"
    return name if generation_id == BASE_GENERATION_ID else f"{name}{GENERATION_RUN_NAME_SEPARATOR}{generation_id}"


def _collector_workflow_runs(repository: str, token: str) -> list[dict[str, Any]]:
    payload = request_json(
        repository, token, "GET", f"actions/workflows/{COLLECTOR_WORKFLOW}/runs?event=workflow_dispatch&per_page=50"
    )
    runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
    return [run for run in runs if isinstance(run, dict) and str(run.get("path") or "") == COLLECTOR_WORKFLOW_PATH]


def _run_title(run: dict[str, Any]) -> str:
    return str(run.get("display_title") or run.get("name") or "")


def collector_runs(
    repository: str, token: str, pr_number: int, head_sha: str, generation_id: str = BASE_GENERATION_ID
) -> list[dict[str, Any]]:
    expected = collector_run_name(pr_number, head_sha, generation_id)
    return [run for run in _collector_workflow_runs(repository, token) if _run_title(run) == expected]


def remediation_generations_used(repository: str, token: str, pr_number: int, head_sha: str) -> set[str]:
    """Every generation this exact head has already spent a collector run on.

    The budget is read from the trusted run listing rather than from the cycle
    status, because the status only ever records the newest generation: counting
    it would reset the budget every time a generation advanced, which is exactly
    the unbounded reviewer loop this transition must not become.
    """

    base = collector_run_name(pr_number, head_sha)
    used: set[str] = set()
    for run in _collector_workflow_runs(repository, token):
        title = _run_title(run)
        if title == base:
            used.add(BASE_GENERATION_ID)
            continue
        if not title.startswith(base + GENERATION_RUN_NAME_SEPARATOR):
            continue
        candidate = title[len(base) + len(GENERATION_RUN_NAME_SEPARATOR) :]
        if GENERATION_ID_PATTERN.fullmatch(candidate):
            used.add(candidate)
    return used


def collector_liveness(
    repository: str, token: str, pr_number: int, head_sha: str, generation_id: str = BASE_GENERATION_ID
) -> tuple[str, int]:
    """Whether a collector for this exact cycle is running or already succeeded."""

    runs = collector_runs(repository, token, pr_number, head_sha, generation_id)
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
        liveness, count = collector_liveness(repository, token, cycle.pr_number, cycle.head_sha, cycle.generation_id)
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


def remediation_generation_admissible(repository: str, token: str, cycle: ReviewCycle, generation_id: str) -> bool:
    """Whether a newly observed remediation generation may start one more cycle.

    A material transition is necessary but never sufficient. The previous
    generation's collector has to be finished, or two reviewers would be run
    against the same immutable head at once; a collector must not already exist
    for this generation, so a status that failed to advance cannot be read as a
    second dispatch permit; and the exact head's generation budget must still
    have room, so remediation can never turn into an unbounded reviewer retry.

    Unreadable evidence is not evidence of a transition: it refuses, leaving the
    candidate pending on the generation it already has.
    """

    if generation_id == cycle.generation_id:
        return False
    try:
        prior_state, _prior_count = collector_liveness(
            repository, token, cycle.pr_number, cycle.head_sha, cycle.generation_id
        )
        _state, started = collector_liveness(repository, token, cycle.pr_number, cycle.head_sha, generation_id)
        used = remediation_generations_used(repository, token, cycle.pr_number, cycle.head_sha)
    except (transport.GitHubRequestError, ValueError) as exc:
        print(f"Remediation generation evidence unavailable; not dispatching: {exc}", file=sys.stderr)
        return False
    if prior_state == "active":
        return False
    if started:
        return False
    if len(used | {generation_id}) > MAX_REMEDIATION_GENERATIONS:
        print(
            f"Remediation generation budget exhausted for PR #{cycle.pr_number} at {cycle.head_sha[:10]} "
            f"after {len(used)} generations; review remains pending.",
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
    description = f"{cycle.state}|{cycle.provider_id}|{trigger}|{cycle.config_digest}|{cycle.generation_id}"
    run_id = current_run_id()
    server = os.environ.get("GITHUB_SERVER_URL") or "https://github.com"
    target_url = f"{server}/{repository}/actions/runs/{run_id}" if run_id else ""
    request_json(
        repository,
        token,
        "POST",
        f"statuses/{head_sha}",
        {
            "state": (
                "success" if cycle.state == "REVIEW_CLEAR" or cycle.state in TERMINAL_NONBLOCKING_STATES else "pending"
            ),
            "context": f"{CONTEXT_PREFIX}{cycle.pr_number}",
            "description": description,
            "target_url": target_url,
        },
    )


def ensure_collector(
    repository: str, token: str, pr_number: int, head_sha: str, generation_id: str = BASE_GENERATION_ID
) -> ReviewCycle:
    digest = reviewer_pool_config_digest()
    state, existing, _error = read_cycle(repository, token, pr_number, head_sha)
    if state == "present" and existing is not None and existing.config_digest == digest:
        if existing.generation_id == generation_id:
            # The same generation is idempotent: whatever happened to this head's
            # reviewers already happened for these exact claims and this exact
            # resolved-finding set, so nothing here may invoke them a second time.
            if existing.state in {"REVIEW_CLEAR", "FINDINGS_OPEN"} | TERMINAL_NONBLOCKING_STATES:
                return existing
            if existing.state in PENDING_STATES and _older_than(
                existing.started_at, INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS
            ):
                terminal = ReviewCycle(
                    pr_number=existing.pr_number,
                    head_sha=existing.head_sha,
                    state="REVIEW_TIMED_OUT",
                    provider_id=existing.provider_id,
                    trigger_id=existing.trigger_id,
                    started_at=existing.started_at,
                    config_digest=existing.config_digest,
                    generation_id=existing.generation_id,
                )
                publish_cycle(repository, token, head_sha, cycle=terminal)
                return terminal
            if existing.trigger_id is not None and not collector_needs_dispatch(repository, token, existing):
                return existing
        elif not remediation_generation_admissible(repository, token, existing, generation_id):
            return existing

    run_id = current_run_id()
    if run_id is None:
        raise RuntimeError("trusted orchestration requires GITHUB_RUN_ID")

    # The branch above is not the only idempotency boundary: it only fires when
    # `read_cycle` itself reports "present" with a matching digest. A stale,
    # missing, or racy read of the commit-status cycle -- two reconciles landing
    # close together, or any other reason the status observation disagrees with
    # reality -- must never fall through to a blind duplicate dispatch when a
    # collector run correlated to this exact (PR, head, generation) is already
    # active or has already succeeded. This check is independent of whatever
    # `read_cycle` answered, and unreadable evidence fails closed rather than
    # authorising a dispatch it cannot rule out as a duplicate.
    try:
        liveness, _count = collector_liveness(repository, token, pr_number, head_sha, generation_id)
    except transport.GitHubRequestError as exc:
        raise RuntimeError(
            f"collector correlation evidence unavailable for PR #{pr_number} at {head_sha[:10]}; "
            f"refusing to risk a duplicate dispatch: {exc}"
        ) from exc

    # Persist the dispatch identity and liveness timestamp *before* dispatch.
    # This status is the durable idempotency record: if GitHub accepts the
    # workflow dispatch and this process dies immediately afterwards, the next
    # reconciliation observes a dispatched cycle and checks correlated collector
    # runs instead of blindly issuing a duplicate dispatch. If dispatch itself
    # never produces a run, the bounded liveness grace permits one recovery.
    cycle = ReviewCycle(
        pr_number=pr_number,
        head_sha=head_sha,
        state="WAITING_FOR_REVIEWER",
        provider_id="",
        trigger_id=run_id,
        started_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        config_digest=digest,
        generation_id=generation_id,
    )
    publish_cycle(repository, token, head_sha, cycle=cycle)
    if liveness not in {"active", "completed"}:
        dispatch_collector(repository, token, pr_number, head_sha, generation_id)
    return cycle


class ReviewRequestBlocked(RuntimeError):
    """The exact-head prerequisites are complete but the review request is not.

    This is distinct from "not ready yet". Once the trusted prerequisite has
    succeeded, no further reconcile of the same immutable head can change the
    answer on its own: the pre-ready review request is a committed artifact, so
    a new valid one only appears with a new commit. Silently returning here is
    what let PR #540 report a successful Reconcile while never publishing an
    exact-head orchestration cycle.
    """


class ReviewRequestReadiness(NamedTuple):
    """Why orchestration may or may not begin for one exact head.

    The reason is carried rather than discarded so the reconcile can report
    the precise blocker instead of a bare success, and so a blocked head is
    distinguishable from a prerequisite that is still running.
    """

    ready: bool
    claims_id: str
    reason: str
    prerequisite_state: str


#: One-time, owner-authorized root-of-trust migration identity (Issue #541).
#:
#: The separated orchestrator -- Review Opportunity independent of Candidate
#: Admission -- is contributed by PR #535, but Hunter intentionally executes
#: orchestration from the default branch, so the candidate cannot activate its
#: own trust-boundary change. That is the same irreducible bootstrap limitation
#: ``scripts/hunter_controller_admission.py`` documents, and it is resolved the
#: same way: a human-authorized root-of-trust cutover, structurally identical to
#: the ``bootstrap_external_review_469.py`` bridge that installed the reviewer
#: orchestration controller for PR #473.
#:
#: This grants exactly one thing: for this one trusted migration identity, the
#: Review Opportunity path is no longer gated behind the trusted preflight, so an
#: independently authenticated review can be collected for a candidate that is
#: blocked from admission. It grants NOTHING else. Candidate admission, ingress
#: provenance, verified signatures, the trusted hosted preflight, deterministic
#: gates, governance, merge readiness and owner approval are all read from
#: their own trusted evidence and remain fail-closed; a completed review carries
#: no merge authority.
#:
#: The identity is a literal in trusted default-branch code compared against the
#: pull request the hosted workflow itself derived from GitHub. It is never read
#: from candidate content, so no pull request, commit, file, comment or status
#: can assert or extend it, and no future candidate can opt itself in.
#:
#: It expires by deletion. Once the permanent separated orchestrator is the
#: default-branch norm, this block is removed and nothing widens: the ordinary
#: path returns to consulting the preflight, and the migration identity stops
#: existing. Deleting the block is a strictly narrowing change.
REVIEW_OPPORTUNITY_MIGRATION_IDENTITIES = frozenset({("fafa33/Project-Hunter", 535)})

#: ``read_head_pre_ready_review`` states that mean a review request is committed
#: for this exact head but can never be dispatched as it stands. Both are terminal
#: for that head: the request is a committed artifact, so it cannot resolve
#: itself, and swallowing either one makes reconcile exit successfully with no
#: reviewer ever dispatched. "absent" and "unavailable" are deliberately not here
#: -- nothing is committed yet, or trusted GitHub evidence is transiently
#: unreadable -- so both keep the ordinary retryable path.
TERMINAL_REQUEST_STATES = frozenset({"present", "invalid"})


def _review_opportunity_migration(repository: str, pr_number: int) -> bool:
    """Whether this pull request is the trusted root-of-trust migration identity."""

    return (repository, int(pr_number)) in REVIEW_OPPORTUNITY_MIGRATION_IDENTITIES


def review_request_state(repository: str, token: str, pr_number: int, head_sha: str) -> ReviewRequestReadiness:
    """Whether this head carries a current review request, and which claims it is.

    The claims digest is returned with the readiness answer because the
    remediation generation is bound to it: resolving the request twice would let
    the generation be derived from claims the dispatch decision never saw. The
    blocking reason is returned for the same reason -- a readiness decision
    nobody can explain is a readiness decision nobody can act on.
    """

    import hunter_governance_review_v2 as governance

    migration = _review_opportunity_migration(repository, pr_number)
    if migration:
        # Review Opportunity for the trusted migration identity is not gated on
        # Candidate Admission. Its prerequisite is the exact-head request below,
        # so an absent request stays retryable and a present-but-unusable one
        # stays terminal for this head.
        prerequisite_state = "review-opportunity"
    else:
        preflight_state, preflight_reason = governance.read_trusted_upgrade_status(
            repository, token, head_sha, pr_number
        )
        if preflight_state != "success":
            return ReviewRequestReadiness(
                False, "", f"exact-head trusted prerequisite is {preflight_state}: {preflight_reason}", preflight_state
            )
        prerequisite_state = preflight_state
    request_state, document, request_error = governance.read_head_pre_ready_review(repository, token, head_sha)
    if migration:
        prerequisite_state = request_state
    if request_state != "present" or not isinstance(document, dict):
        return ReviewRequestReadiness(
            False,
            "",
            f"exact-head pre-ready review request is {request_state} at {head_sha[:10]}: {request_error or 'absent'}",
            prerequisite_state,
        )
    request = document.get("review_request")
    if not (
        isinstance(request, dict)
        and request.get("schema") == "hunter.review-request.v1"
        and isinstance(request.get("claims_id"), str)
        and len(request["claims_id"]) == 64
    ):
        if migration:
            prerequisite_state = "present"
        return ReviewRequestReadiness(
            False,
            "",
            f"exact-head pre-ready review request at {head_sha[:10]} carries no usable review_request claims binding",
            prerequisite_state,
        )
    valid, reason = governance.valid_current_review_request(repository, token, pr_number, head_sha, document)
    if not valid:
        if migration:
            prerequisite_state = "present"
        return ReviewRequestReadiness(
            False,
            "",
            f"exact-head pre-ready review request at {head_sha[:10]} is not valid for this head: {reason}",
            prerequisite_state,
        )
    return ReviewRequestReadiness(True, str(request["claims_id"]), "", prerequisite_state)


def review_prerequisites_ready(repository: str, token: str, pr_number: int, head_sha: str) -> bool:
    return review_request_state(repository, token, pr_number, head_sha).ready


def exact_head_codex_clear_exists(repository: str, token: str, pr_number: int, head_sha: str) -> bool:
    """Whether authenticated Codex has a native clear review bound to ``head_sha``."""

    import hunter_governance_review_v2 as governance

    login = governance.reviewer_login({"id": "codex"})
    page = 1
    while True:
        batch = request_json(repository, token, "GET", f"pulls/{pr_number}/reviews?per_page=100&page={page}")
        if not isinstance(batch, list):
            # Unreadable evidence adopts nothing: dispatch proceeds exactly as it
            # did before exact-head adoption existed.
            return False
        for item in batch:
            if (
                isinstance(item, dict)
                and str((item.get("user") or {}).get("login") or "").lower() == login
                and item.get("commit_id") == head_sha
                and str(item.get("state") or "").upper() == "COMMENTED"
                and governance.native_codex_clear_review(str(item.get("body") or ""), head_sha)
            ):
                return True
        if len(batch) < 100:
            return False
        page += 1


def ensure_current(repository: str, token: str, pr_number: int) -> ReviewCycle | None:
    pr = request_json(repository, token, "GET", f"pulls/{pr_number}")
    if not isinstance(pr, dict) or str(pr.get("state") or "") != "open":
        return None
    # Issue #534: a Draft PR must never start a candidate review cycle, even
    # though every other trigger that reaches here (schedule sweep, review
    # events, workflow_run) iterates or fires without first checking draft
    # state itself. This is enforced here, once, rather than relied upon
    # indirectly. The cutover below removes the preflight gate for one trusted
    # migration identity, so this guard is what keeps a Draft head from gaining
    # review authority through it: the ordinary path was incidentally protected
    # by the preflight, the migration path would not be.
    if bool(pr.get("draft")):
        return None
    head_sha = str((pr.get("head") or {}).get("sha") or "")
    if not head_sha:
        raise RuntimeError("current pull-request head is unavailable")
    readiness = review_request_state(repository, token, pr_number, head_sha)
    if not readiness.ready:
        # A prerequisite that is still pending or running retries on its own:
        # the trusted upgrade status and the scheduled sweep both re-enter here.
        # A prerequisite that already succeeded cannot resolve itself, because
        # the request is a committed artifact of the head. That case must not
        # disappear into a successful reconcile with no future orchestration
        # opportunity, so it is reported instead of swallowed.
        if readiness.prerequisite_state == "success":
            raise ReviewRequestBlocked(
                f"exact-head review orchestration cannot start for PR #{pr_number} at {head_sha[:10]}: "
                f"{readiness.reason}. Trusted preflight already passed, so this head needs a pre-ready review "
                f"request committed for it; reconcile will start orchestration as soon as one is."
            )
        # The migration identity's prerequisite is the request itself, so a
        # request that exists for this head but cannot be used is terminal and is
        # reported rather than swallowed. That covers a committed request that is
        # present but unusable AND a request that is present-but-unreadable
        # ("invalid"): both are a committed artifact of this exact head that will
        # never become dispatchable on its own. Reporting only "present" let an
        # "invalid" request return None, so reconcile exited successfully and the
        # head silently stalled with no reviewer ever dispatched. "absent" stays
        # retryable (nothing is committed yet) and "unavailable" stays retryable
        # (trusted GitHub evidence is transiently unreadable); neither is a defect
        # in the head.
        if readiness.prerequisite_state in TERMINAL_REQUEST_STATES:
            raise ReviewRequestBlocked(
                f"exact-head review orchestration cannot start for PR #{pr_number} at {head_sha[:10]}: "
                f"{readiness.reason}. This head carries a pre-ready review request that is not usable for it; "
                f"reconcile will start orchestration as soon as a usable one is committed."
            )
        return None
    # Derived here, never accepted from a dispatch input or from candidate prose:
    # the generation is what authorises one more reviewer invocation, so only
    # trusted GitHub review-thread state may decide it.
    # An authenticated Codex clear of this exact HEAD is already review
    # authority for it, whatever order triggers and reviews happened in, and
    # governance adopts it directly. Asking Codex again about unchanged content
    # would only add a redundant bot request, so none is dispatched. A new HEAD
    # has no such clear and dispatches as usual.
    if exact_head_codex_clear_exists(repository, token, pr_number, head_sha):
        return None
    generation_id = current_remediation_generation(repository, token, pr_number, head_sha, readiness.claims_id)
    return ensure_collector(repository, token, pr_number, head_sha, generation_id)


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
    except ReviewRequestBlocked as exc:
        # Reported, not swallowed: the reconcile step turns this into a visible
        # failure instead of a green run that promised orchestration it never
        # performed. The blocker is the committed pre-ready review request, so
        # the operator action is to commit one for this exact head; the next
        # reconcile then dispatches exactly once.
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    except transport.GitHubUnavailable as exc:
        print(f"Review orchestration infrastructure unavailable; remaining pending: {exc}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"Review orchestration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
