"""Trusted exact-head lifecycle state for Hunter PR review orchestration.

Native model. A reviewer collection is one least-privileged ``pull_request_target`` run of
``hunter-reviewer-collector.yml``: GitHub itself supplies the pull request and head, and the run
holds no status write, so it cannot certify its own evidence. A small trusted default-branch
publisher (``hunter-collector-publisher.yml``) verifies the actual run, its receipt and the current
pull-request state before it publishes anything. A missing terminal result is simply pending: it
fails closed and is recovered with GitHub's own re-run, never by polling or a synthetic completion.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlparse

import hunter_github_transport as transport
import hunter_pre_ready_review as pre_ready

CONTEXT_PREFIX = "Hunter Review Orchestration / PR #"
COLLECTOR_WORKFLOW_PATH = ".github/workflows/hunter-reviewer-collector.yml"
TRUSTED_STATUS_CREATOR = "github-actions[bot]"
ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "docs" / "CODE_WRITE_POLICY.json"
COLLECTOR_WORKFLOW = "hunter-reviewer-collector.yml"
#: The only workflows whose cited run makes a cycle status believable: the trusted default-branch
#: publisher of verified collector results, and the governance status controller.
TRUSTED_WORKFLOWS = frozenset(
    {
        ".github/workflows/hunter-governance-review.yml",
        ".github/workflows/hunter-collector-publisher.yml",
    }
)
#: Terminal states that end the optional exact-head review opportunity without blocking. Nothing
#: synthesizes one: ``REVIEWER_UNAVAILABLE`` is published only from a verified collector receipt that
#: proves every enabled authority reviewer gave no review.
TERMINAL_NONBLOCKING_STATES = frozenset({"REVIEWER_UNAVAILABLE", "POOL_EXHAUSTED"})
#: Cycle states in which the exact-head opportunity is still open.
OPEN_CYCLE_STATES = frozenset({"WAITING_FOR_REVIEWER", "REVIEW_IN_PROGRESS", "FAILOVER_IN_PROGRESS"})
#: Identity of the collector receipt this module reads. Kept equal to ``hunter_reviewer_collector.SCHEMA`` by a test; it
#: cannot be imported here because the collector imports this module.
COLLECTOR_RESULTS_SCHEMA = "hunter.reviewer-collection.v1"
COLLECTOR_RESULTS_FILE = "reviewer-results.json"
COLLECTOR_RESULTS_MAX_BYTES = 1_048_576
#: Outcomes of an invocation that gave no review: the only ones that can exhaust a reviewer.
EXHAUSTED_OUTCOMES = frozenset({"timed_out", "unavailable", "unacknowledged"})
_RECORD_POOL_FIELDS = (
    "priority",
    "ack_timeout_seconds",
    "review_timeout_seconds",
    "trigger_method",
    "evidence_parser",
    "retryable",
)
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
#: a digest so that every identity derived from it (reviewer invocation key, cycle
#: status) stays byte-identical to the pre-remediation form.
BASE_GENERATION_ID = ""
#: One exact head may spend at most this many generations in total (its first cycle plus its
#: review-after-remediation cycles). Past that the candidate stays pending rather than looping
#: reviewers, and a new HEAD -- a real new candidate -- starts over.
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


#: Commit-status history is read in full, but within a bound: GitHub caps statuses
#: per context, so this is generous. Exceeding it is unavailable evidence and fails
#: closed rather than letting one head exhaust a governance job's API/time budget.
COMMIT_STATUS_MAX_PAGES = 20


def _all_commit_statuses(repository: str, token: str, head_sha: str) -> list[dict[str, Any]]:
    """Read the full creator-bearing status history; never lose old proofs."""
    base = f"commits/{head_sha}/statuses?per_page=100"
    collected: list[dict[str, Any]] = []
    for page in range(1, COMMIT_STATUS_MAX_PAGES + 1):
        path = base if page == 1 else f"{base}&page={page}"
        batch = request_json(repository, token, "GET", path)
        if not isinstance(batch, list):
            raise ValueError("commit status history unavailable")
        collected.extend(status for status in batch if isinstance(status, dict))
        if len(batch) < 100:
            return collected
    raise ValueError("commit status history exceeds the bounded inspection window")


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
    # `read_cycle` remains the outer bound: it independently re-fetches the run
    # the status cites and requires it to be on the default branch and to come from
    # a trusted workflow. GitHub cannot tell two Actions workflows apart by status
    # author, so that cited-run check is the whole of the per-workflow binding.
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
    """The published cycle of one exact head, or ``absent`` (which readers treat as pending).

    The cycle is believed only when the current pull request still has this head, the status
    was posted by ``github-actions[bot]`` under this PR's context, and the run it cites is a
    trusted workflow run on the default branch. The status LIST endpoint is read, not the
    combined endpoint: only the list carries the real ``creator``.
    """

    pr = request_json(repository, token, "GET", f"pulls/{pr_number}")
    if not isinstance(pr, dict) or str(pr.get("state") or "") != "open":
        return "absent", None, "pull request is not open"
    current_head = str((pr.get("head") or {}).get("sha") or "")
    if current_head != head_sha:
        return "superseded", None, f"current head is {current_head or 'unavailable'}"
    statuses = _all_commit_statuses(repository, token, head_sha)
    repository_info = request_json(repository, token, "GET", "")
    default_branch = str((repository_info or {}).get("default_branch") or "main")
    for status in statuses:
        cycle = _parse_cycle(status, pr_number, head_sha)
        if cycle is None:
            continue
        run_id = _run_id(str(status.get("target_url") or ""))
        if run_id is None:
            continue
        run = request_json(repository, token, "GET", f"actions/runs/{run_id}")
        if not isinstance(run, dict) or str(run.get("head_branch") or "") != default_branch:
            continue
        if str(run.get("path") or "") not in TRUSTED_WORKFLOWS:
            continue
        return "present", cycle, None
    return "absent", None, None


def reviewer_pool_config_digest() -> str:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    pool = policy["review_progression"]["review_authority"]["reviewer_pool"]
    encoded = json.dumps(pool, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def current_run_id() -> int:
    raw = os.environ.get("GITHUB_RUN_ID") or ""
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError("GITHUB_RUN_ID is not available or invalid")
    return int(raw)


def independent_review_opportunity_seconds() -> int:
    """One global opportunity budget across the whole reviewer chain.

    The budget is derived from the same ``CODE_WRITE_POLICY.json`` reviewer pool that the
    collector workflow's own declared lifetime is validated against (see
    ``test_collector_workflow_lifetime_covers_the_reviewer_chain_budget``), so raising or
    lowering any one reviewer's configured budget can never leave the collector's job timeout
    silently out of sync with what the pool actually configures.
    """

    pool, error = pre_ready.load_reviewer_pool()
    if pool is None or error:
        raise RuntimeError(f"reviewer pool unavailable: {error}")
    return pre_ready.reviewer_chain_worst_case_seconds(pool)


def __getattr__(name: str) -> Any:
    """Derive ``INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS`` on demand.

    The budget is a derivation, not a declared constant, but trusted
    default-branch readers still reach it as a module attribute -- notably the
    trusted orchestrator replay harness, which imports this *candidate's* module
    and reads that name while its own scenario logic is still main's version.
    Resolving it here rather than assigning it at import keeps every such reader
    working across the cutover, keeps the value pool-derived instead of a second
    hand-set number, and surfaces an unreadable reviewer pool at the point of use
    instead of breaking module import for every other caller.
    """

    if name == "INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS":
        return independent_review_opportunity_seconds()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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


class PoolExhaustion(NamedTuple):
    """Whether one collector receipt proves every enabled authority reviewer gave no review."""

    exhausted: bool
    reason: str


def _record_problem(record: Any, agent: dict[str, Any], number: int) -> str | None:
    """Why one recorded invocation cannot be believed, or ``None``; mirrors the collector's own receipt checks."""

    if not isinstance(record, dict):
        return "an invocation record is not an object"
    if record.get("agent_id") != agent["id"] or record.get("attempt_number") != number:
        return "invocation records are out of order or do not match the enabled reviewer pool"
    if any(record.get(name) != agent[name] for name in _RECORD_POOL_FIELDS):
        return f"{agent['id']} record disagrees with the configured pool"
    outcome = record.get("outcome")
    elapsed = record.get("elapsed_seconds")
    trigger_id = record.get("trigger_id")
    if (
        type(record.get("priority")) is not int
        or type(record.get("ack_timeout_seconds")) is not int
        or type(record.get("review_timeout_seconds")) is not int
        or type(record.get("retryable")) is not bool
        or type(record.get("attempt_number")) is not int
        or type(trigger_id) is not int
        or trigger_id < 0
        or type(elapsed) not in (int, float)
        or not 0 <= elapsed < 7200
    ):
        return f"{agent['id']} record has malformed identity or timing fields"
    if outcome == "timed_out" and elapsed < agent["review_timeout_seconds"]:
        return f"{agent['id']} reported a timeout before its review budget elapsed"
    if outcome == "unacknowledged" and elapsed < agent["ack_timeout_seconds"]:
        return f"{agent['id']} reported no acknowledgement before its acknowledgement budget elapsed"
    return None


def pool_exhaustion(
    receipt: Any,
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    run_id: int,
    claims_id: str,
    generation_id: str,
    pool: dict[str, Any],
) -> PoolExhaustion:
    """Decide, from the collector's own receipt, whether the opportunity ended with no review at all.

    The receipt is evidence about *this* collector run and nothing else, so every identity it carries must equal what
    the trusted caller derived independently: pull request, full head, run, claims, generation and pool configuration.
    A result that is missing, malformed, partial, stale or mixed is never exhaustion. A substantive ``clear`` or
    ``blocking`` verdict from an authority-eligible reviewer is never converted into unavailability: it stays with the
    existing authenticated-review path.
    """

    # Imported here: the collector imports this module at import time.
    import hunter_reviewer_collector as collector

    problem = collector_receipt_problem(
        receipt,
        repository=repository,
        pr_number=pr_number,
        head_sha=head_sha,
        run_id=run_id,
        generation_id=generation_id,
    )
    if problem:
        return PoolExhaustion(False, f"collector {problem}")
    expected = {
        "schema": COLLECTOR_RESULTS_SCHEMA,
        "repository": repository,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "run_id": run_id,
        "claims_id": claims_id,
        "remediation_generation_id": generation_id,
        "configuration_digest": collector.configuration_digest(pool),
    }
    for name, value in expected.items():
        if receipt.get(name) != value or type(receipt.get(name)) is not type(value):
            return PoolExhaustion(False, f"collector receipt {name} does not match the trusted identity")
    if type(receipt.get("run_attempt")) is not int or receipt["run_attempt"] < 1:
        return PoolExhaustion(False, "collector receipt run_attempt is malformed")
    records = receipt.get("attempts")
    if not isinstance(records, list):
        return PoolExhaustion(False, "collector attempts are missing")

    agents = sorted(pre_ready.enabled_pool_reviewers(pool), key=lambda agent: agent["priority"])
    if not any(agent.get("authority_eligible") is not False for agent in agents):
        return PoolExhaustion(False, "the pool has no authority-eligible reviewer to exhaust")
    retries = pool["timeout_policy"]["retries_per_agent"]
    seen: set[tuple[str, int]] = set()
    offset = 0
    for agent in agents:
        eligible = agent.get("authority_eligible") is not False
        budget = 1 + (retries if agent["retryable"] else 0)
        for number in range(1, budget + 1):
            if offset >= len(records):
                return PoolExhaustion(False, f"{agent['id']} was skipped or not fully attempted")
            record = records[offset]
            offset += 1
            problem = _record_problem(record, agent, number)
            if problem is not None:
                return PoolExhaustion(False, problem)
            identity = (agent["id"], record["trigger_id"])
            if identity in seen:
                return PoolExhaustion(False, "duplicate invocation identity")
            seen.add(identity)
            outcome = record["outcome"]
            if outcome in {"clear", "blocking"}:
                if eligible:
                    return PoolExhaustion(False, f"{agent['id']} gave a substantive {outcome} verdict")
                break
            if outcome not in EXHAUSTED_OUTCOMES:
                return PoolExhaustion(False, f"{agent['id']} recorded unsupported outcome {outcome!r}")
            if outcome != "timed_out":
                break
            if number == budget:
                break
    if offset != len(records):
        return PoolExhaustion(False, "collector receipt carries extra or mixed invocation records")
    return PoolExhaustion(True, "every enabled authority reviewer recorded no review")


def read_collector_receipt(path: Path) -> tuple[Any, str]:
    """The collector's own receipt from its working directory, bounded; ``(None, reason)`` when unusable."""

    try:
        # Never follow a symlink: the collector owns this fixed-name artifact.
        if path.is_symlink() or not path.is_file() or path.stat().st_size > COLLECTOR_RESULTS_MAX_BYTES:
            return None, f"{path} is missing or too large"
        return json.loads(path.read_text(encoding="utf-8")), ""
    except (OSError, ValueError) as exc:
        return None, f"{path} is unreadable: {type(exc).__name__}"


def collector_receipt_problem(
    receipt: Any,
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    run_id: int,
    run_attempt: Any = None,
    generation_id: str | None = None,
) -> str:
    """The one canonical receipt contract: why ``receipt`` is unusable, or ``""``.

    Shared by the collector (before it writes its receipt), the trusted publisher and the
    governance exhaustion loader, so producer and consumers can never disagree on what a usable
    receipt is. It checks the schema, the exact identity (repository, PR, head, run, attempt,
    remediation generation) and the shape of every remaining field. Whether the receipt proves
    pool exhaustion against the live review state is ``pool_exhaustion``'s question.
    """

    if not isinstance(receipt, dict):
        return "receipt is not an object"
    expected: dict[str, Any] = {
        "schema": COLLECTOR_RESULTS_SCHEMA,
        "repository": repository,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "run_id": run_id,
    }
    if run_attempt is not None:
        expected["run_attempt"] = run_attempt
    for name, value in expected.items():
        if receipt.get(name) != value or type(receipt.get(name)) is not type(value):
            return f"receipt {name} does not match the completed run"
    if type(receipt.get("run_attempt")) is not int or receipt["run_attempt"] < 1:
        return "receipt run_attempt is malformed"
    for name in ("claims_id", "configuration_digest"):
        if not isinstance(receipt.get(name), str) or not re.fullmatch(r"[0-9a-f]{64}", receipt[name]):
            return f"receipt {name} is missing or malformed"
    generation = receipt.get("remediation_generation_id")
    if not isinstance(generation, str) or (
        generation != BASE_GENERATION_ID and not GENERATION_ID_PATTERN.fullmatch(generation)
    ):
        return "receipt remediation_generation_id is missing or malformed"
    if generation_id is not None and generation != generation_id:
        return "receipt remediation_generation_id does not match the completed run"
    attempts = receipt.get("attempts")
    if not isinstance(attempts, list) or not all(isinstance(item, dict) for item in attempts):
        return "receipt attempts are missing or malformed"
    return ""


class ReviewRequestBlocked(RuntimeError):
    """The exact-head prerequisites are complete but the review request is not.

    This is distinct from "not ready yet". Once the trusted prerequisite has
    succeeded, no further run for the same immutable head can change the
    answer on its own: the pre-ready review request is a committed artifact, so
    a new valid one only appears with a new commit.
    """


class ReviewRequestReadiness(NamedTuple):
    """Why orchestration may or may not begin for one exact head.

    The reason is carried rather than discarded so the caller can report the
    precise blocker instead of a bare success, and so a blocked head is
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
#: itself. "absent" and "unavailable" are deliberately not here -- nothing is
#: committed yet, or trusted GitHub evidence is transiently unreadable -- so both
#: keep the ordinary retryable path.
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


def exact_head_codex_clear_exists(repository: str, token: str, pr_number: int, head_sha: str, claims_id: str) -> bool:
    """Whether governance would adopt the latest Codex review of ``head_sha`` as a clear.

    Collection is skipped only when governance's own latest-review selection and
    adoption predicate accept the review. An older clear superseded by a later
    finding, change request or dismissal, a clear that hides findings, and a
    malformed collection (which governance rejects whole) all collect as before.
    """

    import hunter_governance_review_v2 as governance

    reviews: list[Any] = []
    page = 1
    while True:
        batch = request_json(repository, token, "GET", f"pulls/{pr_number}/reviews?per_page=100&page={page}")
        if not isinstance(batch, list):
            return False
        reviews.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    # Raw GitHub reviews carry no Hunter claims provenance.  Do not synthesize
    # it here: only governance's trusted collector-trigger binding may do that.
    # A manual/unbound clear must therefore not suppress collection.
    try:
        return governance.latest_codex_review_is_exact_head_clear(reviews, head_sha, claims_id)
    except ValueError:
        return False
