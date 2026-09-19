"""Trusted default-branch collector for ordered GitHub reviewer invocation evidence.

Never executes candidate code. A collector on a candidate ref cannot attest to
its own trust. GitHub's immutable artifact digest binds the receipt to the
verified default-branch workflow run and attempt.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

import hunter_governance_review_v2 as governance
import hunter_pre_ready_review as review
import hunter_review_orchestrator as orchestration

BASE_GENERATION = orchestration.BASE_GENERATION_ID

WORKFLOW = ".github/workflows/hunter-reviewer-collector.yml"
SCHEMA = "hunter.reviewer-collection.v1"
LIMIT = 2_000_000
EXTERNAL_PROMPT_LIMIT = 350_000
#: Structured result schema a workflow-dispatched reviewer publishes as its
#: run artifact. Nothing else in that artifact is admissible evidence.
LOCAL_REVIEW_SCHEMA = "hunter.local-review.v1"
#: The run name a dispatched reviewer workflow renders from its correlation
#: input. GitHub does not report workflow_dispatch inputs on a run, so the run
#: name is how this collector recognises the run its own dispatch produced.
LOCAL_REVIEW_RUN_NAME_PREFIX = "Hunter Local Reviewer "
#: A dispatched run has acknowledged the invocation only once a runner has
#: actually picked it up: a queued run proves nothing about runner availability,
#: which is exactly what the acknowledgement budget exists to decide.
STARTED_RUN_STATES = frozenset({"in_progress", "completed"})
#: Trigger schemes this collector can actually perform. A reviewer may not be
#: enabled in the pool with a trigger method outside this set -- see
#: ``unsupported_pool_triggers`` -- because an unperformable trigger aborts the
#: whole ordered collection before any later reviewer is attempted.
TRIGGER_SCHEMES = frozenset({"api", "github-pr-comment", "github-review-request", "github-workflow"})
#: The dispatch rejections that mean "this reviewer cannot be driven from the
#: trusted branch right now" rather than "the candidate is defective": the
#: workflow is not on the branch yet (404), this token may not drive it (403),
#: or the branch will not accept the dispatch (422). This policy already governed
#: the dispatch call itself; it is declared here because every step that reaches
#: GitHub on a reviewer's behalf has to apply the same classification, otherwise
#: one reviewer's unavailability aborts the whole pool and the candidate is left
#: with no authority at all. Every other failure -- notably GitHubUnavailable
#: from exhausted bounded retries -- stays fail-closed and propagates.
REVIEWER_UNAVAILABLE_DISPATCH_STATUS = frozenset({403, 404, 422})


def reviewer_dispatch_unavailable(exc: governance.transport.GitHubRequestError) -> bool:
    """Is this failure "that reviewer cannot be driven", rather than fail-closed?

    Only a direct rejection by GitHub qualifies. A :class:`GitHubUnavailable` is
    excluded even when it carries one of these codes, because bounded retries are
    exhausted only for retryable categories -- including the node-resolution 404,
    which the transport documents as an infrastructure inconsistency that must
    never be read as absence. Treating that as reviewer unavailability would turn
    a fail-closed infrastructure outcome into a silently skipped reviewer.
    """

    if isinstance(exc, governance.transport.GitHubUnavailable):
        return False
    return exc.status_code in REVIEWER_UNAVAILABLE_DISPATCH_STATUS


class ReviewerDispatchRefused(RuntimeError):
    """A reviewer the trusted branch cannot drive before any durable trigger exists.

    This exception is the only signal the outer collection loop may catch and
    convert into a zero-id unavailable record. Any other failure -- including
    every failure after a durable trigger or result comment has been created --
    must fail closed rather than being rewritten as a pre-trigger refusal.
    """

    def __init__(self, request_error: governance.transport.GitHubRequestError) -> None:
        self.request_error = request_error
        super().__init__(f"reviewer dispatch refused: {request_error}")


#: A workflow trigger names a workflow file, never a path: the value is
#: interpolated into an Actions API route, so a separator or traversal segment
#: would address a different resource entirely.
WORKFLOW_FILE_PATTERN = re.compile(r"[A-Za-z0-9._-]+\.ya?ml")


def external_verdict(payload: dict[str, Any]) -> str:
    """Map a provider JSON verdict to Hunter states; ambiguity blocks."""
    verdict_value = payload.get("verdict")
    summary_value = payload.get("summary")
    if not isinstance(verdict_value, str) or not isinstance(summary_value, str):
        return "blocking"
    verdict = verdict_value.strip().lower()
    summary = summary_value.strip()
    if not summary or verdict not in {"clear", "blocking"}:
        return "blocking"
    if verdict == "clear":
        lower = summary.lower()
        safe_clear = re.search(
            r"\bno (?:substantive |remaining )?(?:blocking )?(?:findings|defects|issues|blockers)\b", lower
        ) or re.search(
            r"\bfound no (?:substantive |remaining )?(?:blocking )?(?:findings|defects|issues|blockers)\b", lower
        )
        dangerous = re.search(
            r"\b(?:critical|unsafe|vulnerabilit|blocking (?:finding|defect|issue)|must fix|exploit)\b", lower
        )
        if not safe_clear or dangerous:
            return "blocking"
    return verdict


def configuration_digest(pool: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(pool, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def trigger_scheme(agent: Mapping[str, Any]) -> str:
    """The ``scheme`` half of a reviewer's ``scheme:target`` trigger method."""

    scheme, separator, _ = str(agent.get("trigger_method") or "").partition(":")
    return scheme if separator else ""


def workflow_trigger_file(agent: Mapping[str, Any]) -> str:
    """The workflow file a ``github-workflow:`` reviewer dispatches."""

    if trigger_scheme(agent) != "github-workflow":
        raise ValueError("reviewer has no supported workflow trigger")
    workflow = str(agent["trigger_method"]).split(":", 1)[1].strip()
    if WORKFLOW_FILE_PATTERN.fullmatch(workflow) is None:
        raise ValueError("reviewer workflow trigger must name a bare workflow file")
    return workflow


def unsupported_pool_triggers(pool: Mapping[str, Any]) -> tuple[str, ...]:
    """Enabled reviewers whose declared trigger this collector cannot perform.

    Collection is ordered and fail-closed, so a reviewer enabled with a trigger
    nothing implements does not merely skip itself: its invocation raises, and
    every lower-priority reviewer behind it is never attempted. The pool guard
    therefore refuses the configuration rather than discovering it at run time.
    """

    return tuple(
        str(agent["id"])
        for agent in review.enabled_pool_reviewers(pool)
        if trigger_scheme(agent) not in TRIGGER_SCHEMES
    )


def local_review_state(result: Any, head: str, claims_id: str) -> str:
    """Map a published ``hunter.local-review.v1`` artifact to a reviewer state.

    Anything that is not an exact-head, claims-bound, internally consistent
    review is unavailability rather than a verdict: a reviewer that produced no
    usable evidence has not reviewed this candidate, and treating its silence as
    a result would either clear a candidate nothing reviewed or block one on a
    defect nobody found.
    """

    if not isinstance(result, dict) or result.get("schema") != LOCAL_REVIEW_SCHEMA:
        return "unavailable"
    if result.get("head_sha") != head or result.get("claims_id") != claims_id:
        return "unavailable"
    findings = result.get("findings")
    summary = result.get("summary")
    if not isinstance(findings, list) or not isinstance(summary, str) or not summary.strip():
        return "unavailable"
    verdict = result.get("verdict")
    if verdict == "findings":
        # A blocking verdict carrying no finding is self-contradictory; there is
        # no defect to act on, so it is unusable evidence rather than a blocker.
        return "blocking" if findings else "unavailable"
    if verdict == "clear":
        # A clear verdict that still lists findings fails closed to the findings.
        return "blocking" if findings else "clear"
    return "unavailable"


class Backend(Protocol):
    def head(self) -> str: ...
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...
    def trigger(self, agent: dict[str, Any], number: int) -> dict[str, Any]: ...
    def responded(self, agent: dict[str, Any], trigger: dict[str, Any]) -> bool: ...
    def response_state(self, agent: dict[str, Any], trigger: dict[str, Any]) -> str: ...
    def acknowledged(self, agent: dict[str, Any], trigger: dict[str, Any]) -> bool: ...


def collect_attempts(pool: dict[str, Any], head: str, backend: Backend) -> list[dict[str, Any]]:
    """Invoke every enabled reviewer in priority order and record what happened.

    Each invocation is budgeted twice, exactly as the pool declares it. The
    acknowledgement budget answers only "did this reviewer actually receive and
    start this exact-head job?", so a provider whose runner is absent yields to
    the next reviewer in seconds instead of parking the candidate for the whole
    review budget. The review budget is separate and only starts mattering once
    the invocation was acknowledged, so ordinary review execution time is never
    read as unavailability. Both budgets are recorded on every attempt, because
    the trusted receipt verifier re-derives them from the pool.

    A reviewer the pool marks triage-only (``authority_eligible`` false) has its
    result recorded but cannot end the search: it is never review authority, so
    stopping on its verdict would leave the candidate with no authority at all.
    """

    records: list[dict[str, Any]] = []
    agents = sorted(review.enabled_pool_reviewers(pool), key=lambda a: a["priority"])
    for agent in agents:
        count = 1 + (pool["timeout_policy"]["retries_per_agent"] if agent["retryable"] else 0)
        for number in range(1, count + 1):
            if backend.head() != head:
                raise ValueError("HEAD changed before reviewer invocation")
            try:
                trigger = backend.trigger(agent, number)
            except ReviewerDispatchRefused:
                # A reviewer the trusted branch cannot drive *before* a durable
                # trigger existed is per-reviewer unavailability. Aborting the
                # loop for this category would leave every later reviewer
                # un-invoked and the candidate with no authority at all --
                # surfaced as MISSING_REVIEW_AUTHORITY, a candidate defect, when
                # nothing about the candidate was wrong. Anything else that
                # escapes backend.trigger, including every post-trigger failure,
                # must stay fail-closed and is deliberately not caught here.
                records.append(
                    {
                        "agent_id": agent["id"],
                        "priority": agent["priority"],
                        "ack_timeout_seconds": agent["ack_timeout_seconds"],
                        "review_timeout_seconds": agent["review_timeout_seconds"],
                        "trigger_method": agent["trigger_method"],
                        "evidence_parser": agent["evidence_parser"],
                        "retryable": agent["retryable"],
                        "attempt_number": number,
                        "trigger_id": 0,
                        "trigger_created_at": "",
                        "collector_run_id": int(getattr(backend, "run_id", 0)),
                        "collector_run_attempt": int(getattr(backend, "run_attempt", 0)),
                        "ack_elapsed_seconds": 0.0,
                        "elapsed_seconds": 0.0,
                        "outcome": "unavailable",
                    }
                )
                break
            start = backend.now()
            ack_deadline = start + agent["ack_timeout_seconds"]
            review_deadline = start + agent["review_timeout_seconds"]
            acknowledged = False
            ack_elapsed: float | None = None
            state = "waiting"
            while True:
                if backend.head() != head:
                    raise ValueError("HEAD changed during reviewer invocation")
                state = backend.response_state(agent, trigger)
                if state in {"clear", "blocking", "unavailable"}:
                    break
                if state != "waiting":
                    raise ValueError(f"unsupported reviewer response state: {state}")
                if not acknowledged and backend.acknowledged(agent, trigger):
                    acknowledged = True
                    ack_elapsed = backend.now() - start
                now = backend.now()
                if not acknowledged and now >= ack_deadline:
                    state = "unacknowledged"
                    break
                if now >= review_deadline:
                    state = "timed_out"
                    break
                if acknowledged:
                    backend.sleep(min(15, review_deadline - now))
                else:
                    backend.sleep(min(5, ack_deadline - now))
            if ack_elapsed is None:
                ack_elapsed = backend.now() - start
            records.append(
                {
                    "agent_id": agent["id"],
                    "priority": agent["priority"],
                    "ack_timeout_seconds": agent["ack_timeout_seconds"],
                    "review_timeout_seconds": agent["review_timeout_seconds"],
                    "trigger_method": agent["trigger_method"],
                    "evidence_parser": agent["evidence_parser"],
                    "retryable": agent["retryable"],
                    "attempt_number": number,
                    "trigger_id": trigger["id"],
                    "trigger_created_at": trigger["created_at"],
                    "collector_run_id": int(trigger.get("collector_run_id") or getattr(backend, "run_id", 0)),
                    "collector_run_attempt": int(
                        trigger.get("collector_run_attempt") or getattr(backend, "run_attempt", 0)
                    ),
                    "ack_elapsed_seconds": ack_elapsed,
                    "elapsed_seconds": backend.now() - start,
                    "outcome": state,
                    **({k: trigger[k] for k in ("provider", "response_digest", "head_sha") if k in trigger}),
                }
            )
            if state in {"clear", "blocking"}:
                if agent.get("authority_eligible") is not False:
                    return records
                break
            if state in {"unavailable", "unacknowledged"}:
                break
    return records


def valid_run(run: dict[str, Any], run_id: int, branch: str, revision: str) -> bool:
    return (
        run.get("id") == run_id
        and type(run.get("run_attempt")) is int
        and run["run_attempt"] >= 1
        and run.get("head_branch") == branch
        and governance._is_commit_sha(str(run.get("head_sha") or ""))
        and run.get("head_sha") == revision
        and run.get("path") == WORKFLOW
        and run.get("event") in {"workflow_dispatch", "pull_request_target"}
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
    )


def _run_on_default_branch_history(repository: str, token: str, branch: str, revision: str) -> bool:
    tip = governance.request_json(repository, token, "GET", f"commits/{branch}")
    if not isinstance(tip, dict) or not governance._is_commit_sha(str(tip.get("sha") or "")):
        raise ValueError("trusted default-branch tip is unavailable")
    payload = governance.request_json(repository, token, "GET", f"compare/{revision}...{tip['sha']}")
    if not isinstance(payload, dict):
        raise ValueError("default-branch ancestry comparison payload is malformed")
    return str(payload.get("status") or "") in {"ahead", "identical"}


def _pages(repository: str, token: str, path: str, key: str | None = None) -> list[dict[str, Any]]:
    results = []
    page = 1
    while True:
        payload = governance.request_json(repository, token, "GET", f"{path}?per_page=100&page={page}")
        batch = payload.get(key) if key and isinstance(payload, dict) else payload
        if not isinstance(batch, list) or not all(isinstance(item, dict) for item in batch):
            raise ValueError("malformed GitHub collection evidence")
        results.extend(batch)
        if len(batch) < 100:
            return results
        page += 1


def invocation_key(head: str, claims_id: str, agent_id: str, number: int, generation_id: str = BASE_GENERATION) -> str:
    """The identity that makes one reviewer invocation reusable, not repeatable.

    It deliberately excludes the collector run, so re-running the collector for
    an unchanged cycle adopts the invocation it already made instead of asking
    the reviewer twice. A remediation generation is the one thing that must make
    it a *different* invocation: once the blocking findings of the previous
    generation were resolved, the same reviewer has to be asked again about the
    same head. The base generation reproduces the original material exactly, so
    trigger comments and exhaustion receipts minted before remediation
    generations existed stay verifiable byte-for-byte.
    """

    material = f"{head}:{claims_id}:{agent_id}:{number}"
    if generation_id != BASE_GENERATION:
        material = f"{material}:{generation_id}"
    return hashlib.sha256(material.encode()).hexdigest()


def workflow_correlation_id(
    head: str, claims_id: str, agent_id: str, run_id: int, run_attempt: int, number: int
) -> str:
    """The identity a dispatched reviewer run renders as its run name.

    It binds the collector run and attempt as well as the candidate head, claims
    digest, reviewer and attempt number, so no two dispatches -- across retries,
    collector re-runs, concurrent candidates, or a manual dispatch -- can share
    one. Without that, an unrelated run of the same workflow on the same branch
    would be indistinguishable from this attempt's own acknowledgement and
    result.
    """

    material = f"{SCHEMA}:{head}:{claims_id}:{agent_id}:{run_id}:{run_attempt}:{number}"
    return hashlib.sha256(material.encode()).hexdigest()


def api_trigger_payload(
    head: str,
    claims_id: str,
    agent: dict[str, Any],
    run_id: int,
    run_attempt: int,
    number: int,
    generation_id: str = BASE_GENERATION,
) -> dict[str, Any]:
    if not str(agent["trigger_method"]).startswith("api:"):
        raise ValueError("reviewer has no supported API trigger")
    payload = {
        "schema": "hunter.reviewer-trigger.v1",
        "head_sha": head,
        "claims_id": claims_id,
        "reviewer_agent": str(agent["id"]),
        "collector_run_id": run_id,
        "collector_run_attempt": run_attempt,
        "attempt_number": number,
        "invocation_key": invocation_key(head, claims_id, str(agent["id"]), number, generation_id),
    }
    # The base generation is the absence of the field, never a field carrying a
    # base value, so a pre-remediation trigger comment stays byte-identical and
    # no two encodings of the same generation can exist.
    if generation_id != BASE_GENERATION:
        payload["remediation_generation_id"] = generation_id
    return payload


def api_trigger_body(
    head: str,
    claims_id: str,
    agent: dict[str, Any],
    run_id: int,
    run_attempt: int,
    number: int,
    generation_id: str = BASE_GENERATION,
) -> str:
    payload = api_trigger_payload(head, claims_id, agent, run_id, run_attempt, number, generation_id)
    return (
        json.dumps(payload, sort_keys=True)
        + f'\nInvocation key: {payload["invocation_key"]}.'
        + f'\nCollector invocation: {run_id}/{run_attempt}/{agent["id"]}/{number}.'
    )


def parse_api_trigger(body: str) -> dict[str, Any] | None:
    raw = body.strip()
    match = re.fullmatch(
        r"(?s)(\{.*\})\nInvocation key: ([0-9a-f]{64})\.\nCollector invocation: (\d+)/(\d+)/([a-z0-9_-]+)/(\d+)\.",
        raw,
    )
    if match is None:
        return None
    try:
        value = json.loads(match.group(1))
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    allowed = {
        "schema",
        "head_sha",
        "claims_id",
        "reviewer_agent",
        "collector_run_id",
        "collector_run_attempt",
        "attempt_number",
        "invocation_key",
    }
    if set(value) - {"remediation_generation_id"} != allowed:
        return None
    generation_id = value.get("remediation_generation_id", BASE_GENERATION)
    # A base generation must be encoded by omission, so the same generation can
    # never be spelled two ways and adopt two distinct invocation identities.
    if not isinstance(generation_id, str) or not orchestration.GENERATION_ID_PATTERN.fullmatch(generation_id):
        if "remediation_generation_id" in value:
            return None
        generation_id = BASE_GENERATION
    if value.get("schema") != "hunter.reviewer-trigger.v1":
        return None
    if not re.fullmatch(r"[0-9a-f]{40}", str(value.get("head_sha") or "")):
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", str(value.get("claims_id") or "")):
        return None
    reviewer_agent = str(value.get("reviewer_agent") or "")
    if reviewer_agent != match.group(5):
        return None
    if any(
        type(value.get(field)) is not int or int(value[field]) <= 0
        for field in ("collector_run_id", "collector_run_attempt")
    ):
        return None
    if type(value.get("attempt_number")) is not int or value["attempt_number"] <= 0:
        return None
    if (
        value["collector_run_id"] != int(match.group(3))
        or value["collector_run_attempt"] != int(match.group(4))
        or value["attempt_number"] != int(match.group(6))
        or value.get("invocation_key") != match.group(2)
    ):
        return None
    if value["invocation_key"] != invocation_key(
        value["head_sha"], value["claims_id"], reviewer_agent, value["attempt_number"], generation_id
    ):
        return None
    return value


def api_result_body(
    head: str,
    claims_id: str,
    agent: dict[str, Any],
    run_id: int,
    trigger_id: int,
    verdict: str,
    summary: str,
    digest: str,
) -> str:
    return json.dumps(
        {
            "schema": "hunter.reviewer-result.v1",
            "head_sha": head,
            "claims_id": claims_id,
            "reviewer_agent": str(agent["id"]),
            "collector_run_id": run_id,
            "trigger_id": trigger_id,
            "verdict": verdict,
            "summary": summary,
            "response_digest": digest,
        },
        sort_keys=True,
    )


def parse_api_result(body: str) -> dict[str, Any] | None:
    raw = body.strip()
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    allowed = {
        "schema",
        "head_sha",
        "claims_id",
        "reviewer_agent",
        "collector_run_id",
        "trigger_id",
        "verdict",
        "summary",
        "response_digest",
    }
    if set(value) != allowed:
        return None
    if value.get("schema") != "hunter.reviewer-result.v1":
        return None
    if not re.fullmatch(r"[0-9a-f]{40}", str(value.get("head_sha") or "")):
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", str(value.get("claims_id") or "")):
        return None
    if str(value.get("reviewer_agent") or "") not in {"gemini", "groq"}:
        return None
    if type(value.get("collector_run_id")) is not int or value["collector_run_id"] <= 0:
        return None
    if type(value.get("trigger_id")) is not int or value["trigger_id"] <= 0:
        return None
    if value.get("verdict") not in {"clear", "blocking", "unavailable"}:
        return None
    if not isinstance(value.get("summary"), str):
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", str(value.get("response_digest") or "")):
        return None
    return value


def trigger_body(
    head: str,
    claims_id: str,
    agent: dict[str, Any],
    run_id: int,
    run_attempt: int,
    number: int,
    generation_id: str = BASE_GENERATION,
) -> str:
    method = str(agent["trigger_method"])
    if not (
        method.startswith("github-pr-comment:") or method.startswith("github-review-request:")
    ) or not governance.reviewer_login(agent):
        raise ValueError("reviewer has no supported authenticated GitHub trigger")
    ack = {
        "schema": "hunter.review-ack.v1",
        "head_sha": head,
        "claims_id": claims_id,
        "verdict": "clear",
        "summary": "<your substantive review summary>",
        "collector_run_id": run_id,
    }
    # As in the API payload, the base generation is the absence of the line.
    generation_line = "" if generation_id == BASE_GENERATION else f"\nRemediation generation: {generation_id}."
    return (
        (method.split(":", 1)[1] if method.startswith("github-pr-comment:") else "Trusted GitHub review request")
        + f"\nReview exact HEAD {head} and the complete review request in "
        f"{review.REVIEW_RELATIVE_PATH}. Report any blocking findings; do not issue a clear verdict if any remain. "
        "After completing the substantive review, if all requested claims are satisfied and no blockers remain, "
        "reply with only this JSON result, filling in your own substantive summary: "
        + json.dumps(ack)
        + f'\nCollector invocation: {run_id}/{run_attempt}/{agent["id"]}/{number}.'
        + generation_line
        + f'\nInvocation key: {invocation_key(head, claims_id, str(agent["id"]), number, generation_id)}.'
    )


def parse_native_trigger(body: str) -> dict[str, Any] | None:
    """Parse Hunter's canonical authenticated GitHub reviewer trigger."""
    match = re.search(
        r"Review exact HEAD ([0-9a-f]{40}).*?\"claims_id\": \"([0-9a-f]{64})\".*?"
        r"Collector invocation: (\d+)/(\d+)/([a-z0-9_-]+)/(\d+)\.\n"
        r"(?:Remediation generation: ([0-9a-f]{16})\.\n)?"
        r"Invocation key: ([0-9a-f]{64})\.",
        body,
        re.S,
    )
    if match is None:
        return None
    head, claims_id, run_id, run_attempt, agent_id, attempt, generation, key = match.groups()
    number = int(attempt)
    generation_id = generation or BASE_GENERATION
    if key != invocation_key(head, claims_id, agent_id, number, generation_id):
        return None
    return {
        "head_sha": head,
        "claims_id": claims_id,
        "reviewer_agent": agent_id,
        "collector_run_id": int(run_id),
        "collector_run_attempt": int(run_attempt),
        "attempt_number": number,
        "remediation_generation_id": generation_id,
    }


class GitHubBackend:
    def __init__(
        self,
        repository: str,
        token: str,
        pr: int,
        head: str,
        claims_id: str,
        run_id: int,
        run_attempt: int,
        generation_id: str = BASE_GENERATION,
    ):
        self.repository, self.token, self.pr = repository, token, pr
        self.expected_head, self.claims_id = head, claims_id
        self.run_id, self.run_attempt = run_id, run_attempt
        self.generation_id = generation_id
        self._default_branch: str | None = None

    def default_branch(self) -> str:
        """The trusted default branch, which is the only ref a reviewer may run from."""

        if self._default_branch is None:
            # Resolved from the repository itself, never from the run context.
            # `GITHUB_REF_NAME` is the ref the *current* workflow is running on,
            # which is the merge ref under `pull_request` and only incidentally
            # the default branch under the collector's own dispatch. Reading it
            # here would silently query reviewer runs on the wrong branch and
            # find none, which reads as reviewer unavailability rather than as
            # the configuration error it is.
            repo = governance.request_json(self.repository, self.token, "GET", "")
            branch = str(repo.get("default_branch") or "") if isinstance(repo, dict) else ""
            if not branch:
                raise ValueError("trusted default branch is unavailable")
            self._default_branch = branch
        return self._default_branch

    def head(self) -> str:
        pr = governance.request_json(self.repository, self.token, "GET", f"pulls/{self.pr}")
        if not isinstance(pr, dict) or pr.get("state") != "open":
            raise ValueError("pull request is unavailable or closed")
        return str(pr["head"]["sha"])

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def _candidate_diff(self) -> str | None:
        pr = governance.request_json(self.repository, self.token, "GET", f"pulls/{self.pr}")
        if not isinstance(pr, dict) or not isinstance(pr.get("base"), dict):
            raise ValueError("pull request base ref is unavailable")
        if str((pr.get("head") or {}).get("sha") or "") != self.expected_head:
            raise ValueError("HEAD changed before immutable diff acquisition")
        base_sha = str(pr["base"].get("sha") or "")
        if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
            raise ValueError("pull request base SHA is invalid")
        url = f"https://api.github.com/repos/{self.repository}/compare/{base_sha}...{self.expected_head}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github.v3.diff"}
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read(EXTERNAL_PROMPT_LIMIT + 1)
        if len(data) > EXTERNAL_PROMPT_LIMIT:
            return None
        return data.decode("utf-8", errors="strict")

    def _invoke_external(self, agent: dict[str, Any], number: int) -> dict[str, Any]:
        provider = str(agent["trigger_method"]).split(":", 1)[1]
        secret_name = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY"}.get(provider)
        key = os.environ.get(secret_name or "", "")
        if not key:
            return {"verdict": "unavailable", "summary": f"{provider} API key unavailable"}
        candidate_diff = self._candidate_diff()
        if candidate_diff is None:
            return {
                "verdict": "unavailable",
                "summary": f"{provider} unavailable: exact-head diff exceeds bounded external reviewer context budget",
            }
        prompt = (
            "You are an independent hostile code reviewer. Review the COMPLETE exact-head diff below. "
            f"Repository={self.repository} PR={self.pr} HEAD={self.expected_head} claims_id={self.claims_id}. "
            'Return JSON only: {"verdict":"clear|blocking","summary":"..."}. '
            "Use clear only when no substantive correctness, security, governance, exact-head, or fail-closed defect remains. "
            "Any finding must use blocking.\n\nDIFF:\n" + candidate_diff
        )
        if provider == "gemini":
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.7-flash:generateContent"
            headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
            body: dict[str, Any] = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            }
        elif provider == "groq":
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            body = {
                "model": "openai/gpt-oss-120b",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            }
        else:
            raise ValueError(f"unsupported external reviewer: {provider}")
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            # The provider call is the review execution itself, so it is bounded
            # by the reviewer's review budget, not by its acknowledgement budget.
            with urllib.request.urlopen(req, timeout=int(agent["review_timeout_seconds"])) as response:
                raw = response.read(LIMIT + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403, 408, 413, 429, 500, 502, 503, 504}:
                return {"verdict": "unavailable", "summary": f"{provider} HTTP {exc.code}"}
            raise
        except (TimeoutError, urllib.error.URLError):
            return {"verdict": "unavailable", "summary": f"{provider} transport unavailable"}
        if len(raw) > LIMIT:
            raise ValueError("external reviewer response too large")
        envelope = json.loads(raw)
        if provider == "gemini":
            text = envelope["candidates"][0]["content"]["parts"][0]["text"]
        else:
            text = envelope["choices"][0]["message"]["content"]
        result = json.loads(text) if isinstance(text, str) else text
        if not isinstance(result, dict):
            raise ValueError("external reviewer returned malformed JSON")
        return result

    def _post_comment(self, body: str) -> dict[str, Any]:
        result = governance.request_json(
            self.repository, self.token, "POST", f"issues/{self.pr}/comments", {"body": body}
        )
        if not isinstance(result, dict) or type(result.get("id")) is not int or not result.get("created_at"):
            raise ValueError("GitHub did not confirm trusted collector evidence")
        return result

    def _post_trigger_comment(self, body: str) -> dict[str, Any]:
        """Create a durable trigger comment, raising a refusal into the outer loop.

        A failure here means no durable trigger identity exists yet, so the
        outer collection loop is allowed to record per-reviewer unavailability
        by catching the deliberate :class:`ReviewerDispatchRefused` exception.
        Any other exception propagates and fails closed.
        """

        try:
            return self._post_comment(body)
        except governance.transport.GitHubRequestError as exc:
            if not reviewer_dispatch_unavailable(exc):
                raise
            raise ReviewerDispatchRefused(exc) from exc

    def _post_result_comment(self, body: str, trigger_id: int) -> dict[str, Any]:
        """Persist a result/ack comment after a durable trigger already exists.

        This is a post-trigger persistence path: a failure must never be turned
        into a zero-id unavailable record. Wrap it so the caller sees the real
        trigger identity and a meaningful failure, while still allowing a direct
        trusted-branch refusal (the repository is gone, permissions revoked) to
        classify as reviewer unavailability without losing the trigger id.
        """

        try:
            return self._post_comment(body)
        except governance.transport.GitHubRequestError as exc:
            if not reviewer_dispatch_unavailable(exc):
                raise
            # The trigger comment in `trigger_id` already exists. Return a
            # synthetic record that preserves the real identity; the outer
            # collector will see this as an unavailable attempt with durable
            # evidence, not as a pre-trigger refusal.
            return {
                "id": 0,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "durable_trigger_id": trigger_id,
                "post_trigger_refusal": type(exc).__name__,
                "dispatch_status": exc.status_code,
            }

    def _api_result(self, agent: dict[str, Any], trigger: dict[str, Any]) -> dict[str, Any] | None:
        trigger_record = parse_api_trigger(str(trigger.get("body") or ""))
        if trigger_record is None:
            raise ValueError("external reviewer trigger evidence is malformed")
        if (
            trigger_record["head_sha"] != self.expected_head
            or trigger_record["claims_id"] != self.claims_id
            or trigger_record["reviewer_agent"] != str(agent["id"])
        ):
            raise ValueError("external reviewer response is not exact-head bound")
        matches = []
        for item in _pages(self.repository, self.token, f"issues/{self.pr}/comments"):
            if (item.get("user") or {}).get("login", "").lower() != "github-actions[bot]":
                continue
            result = parse_api_result(str(item.get("body") or ""))
            if result is None:
                continue
            if (
                result["head_sha"] == trigger_record["head_sha"]
                and result["claims_id"] == trigger_record["claims_id"]
                and result["reviewer_agent"] == trigger_record["reviewer_agent"]
                and result["collector_run_id"] == trigger_record["collector_run_id"]
                and result["trigger_id"] == int(trigger.get("id") or 0)
            ):
                matches.append({**result, "comment_id": int(item.get("id") or 0)})
        if not matches:
            return None
        return min(matches, key=lambda item: int(item.get("comment_id") or 0))

    def _workflow_run(self, trigger: dict[str, Any]) -> dict[str, Any] | None:
        """The trusted default-branch run this exact dispatch produced, or nothing.

        Selection is by the dispatch's own correlation identity, which the
        reviewer workflow renders as its run name. Event, branch, workflow path
        and timestamp are shared by every dispatch of this workflow, so without
        the correlation a concurrent candidate's run, an earlier attempt's run,
        or a manual dispatch could be read as this attempt's acknowledgement and
        result.
        """

        correlation = str(trigger.get("correlation_id") or "")
        workflow = str(trigger.get("workflow") or "")
        if not correlation or not workflow:
            return None
        branch = self.default_branch()
        payload = governance.request_json(
            self.repository,
            self.token,
            "GET",
            f"actions/workflows/{workflow}/runs?event=workflow_dispatch&branch={branch}&per_page=100",
        )
        runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
        expected_name = LOCAL_REVIEW_RUN_NAME_PREFIX + correlation
        matches = [
            run
            for run in (runs if isinstance(runs, list) else [])
            if isinstance(run, dict)
            and str(run.get("event") or "") == "workflow_dispatch"
            and str(run.get("head_branch") or "") == branch
            and str(run.get("path") or "") == f".github/workflows/{workflow}"
            and expected_name in {str(run.get("name") or ""), str(run.get("display_title") or "")}
        ]
        return min(matches, key=lambda run: int(run.get("id") or 0), default=None)

    def _adopt_workflow_run(self, trigger: dict[str, Any]) -> dict[str, Any] | None:
        """Bind the trigger record to the run its dispatch produced."""

        run = self._workflow_run(trigger)
        if run is None:
            return None
        trigger["id"] = int(run.get("id") or 0)
        trigger["created_at"] = str(run.get("created_at") or trigger.get("created_at") or "")
        return run

    def _dispatch_workflow_reviewer(self, agent: dict[str, Any], number: int) -> dict[str, Any]:
        """Dispatch a reviewer workflow from the trusted default branch.

        The dispatch ref is the default branch, never the candidate head, so the
        reviewer that runs is the trusted one; the candidate is passed to it as
        data. A dispatch the trusted branch cannot accept -- the workflow is not
        on it yet, or this token may not drive it -- is reviewer unavailability,
        not a candidate defect, so the ordered pool continues to the next
        reviewer instead of aborting the whole collection.
        """

        workflow = workflow_trigger_file(agent)
        correlation = workflow_correlation_id(
            self.expected_head, self.claims_id, str(agent["id"]), self.run_id, self.run_attempt, number
        )
        pending = {
            "id": 0,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "kind": "github-workflow",
            "workflow": workflow,
            "correlation_id": correlation,
            "collector_run_id": self.run_id,
            "collector_run_attempt": self.run_attempt,
        }
        try:
            if self._adopt_workflow_run(pending) is not None:
                return pending
        except governance.transport.GitHubRequestError as exc:
            # The probe reaches the same trusted-branch resources the dispatch
            # does, so a rejection here means the same thing the docstring
            # promises: this reviewer is unavailable, not that the collection
            # must be abandoned before the rest of the pool is tried.
            if reviewer_dispatch_unavailable(exc):
                return {**pending, "state": "unavailable", "dispatch_status": exc.status_code}
            raise
        try:
            governance.request_json(
                self.repository,
                self.token,
                "POST",
                f"actions/workflows/{workflow}/dispatches",
                {
                    "ref": self.default_branch(),
                    "inputs": {
                        "pr_number": str(self.pr),
                        "head_sha": self.expected_head,
                        "claims_id": self.claims_id,
                        "correlation_id": correlation,
                    },
                },
            )
        except governance.transport.GitHubRequestError as exc:
            if reviewer_dispatch_unavailable(exc):
                return {**pending, "state": "unavailable", "dispatch_status": exc.status_code}
            raise
        return pending

    def _workflow_review_state(self, trigger: dict[str, Any]) -> str:
        """The reviewer state a dispatched run has actually established."""

        if str(trigger.get("state") or "") == "unavailable":
            return "unavailable"
        try:
            run = self._adopt_workflow_run(trigger)
        except governance.transport.GitHubRequestError as exc:
            if reviewer_dispatch_unavailable(exc):
                return "unavailable"
            raise
        if run is None or str(run.get("status") or "") != "completed":
            return "waiting"
        if str(run.get("conclusion") or "") != "success":
            return "unavailable"
        result = local_review_result(self.repository, self.token, int(run["id"]), self.pr, self.expected_head)
        return local_review_state(result, self.expected_head, self.claims_id)

    def invocation_marker(self, agent: dict[str, Any], number: int) -> str:
        """The comment marker that identifies this collector's own invocation.

        It is the single seam through which an already-posted reviewer trigger is
        recognised and adopted, which is why it has to carry the remediation
        generation: without it a superseded generation's trigger would be adopted
        forever and the remediated head could never be reviewed again.
        """

        key = invocation_key(self.expected_head, self.claims_id, str(agent["id"]), number, self.generation_id)
        return f"Invocation key: {key}."

    def _existing_trigger(self, agent: dict[str, Any], number: int) -> dict[str, Any] | None:
        marker = self.invocation_marker(agent, number)
        matches = [
            item
            for item in _pages(self.repository, self.token, f"issues/{self.pr}/comments")
            if (item.get("user") or {}).get("login", "").lower() == "github-actions[bot]"
            and marker in str(item.get("body") or "")
        ]
        return min(matches, key=lambda x: int(x.get("id") or 0), default=None)

    def trigger(self, agent: dict[str, Any], number: int) -> dict[str, Any]:
        method = str(agent["trigger_method"])
        scheme = trigger_scheme(agent)
        if scheme not in TRIGGER_SCHEMES:
            raise ValueError("reviewer has no supported authenticated GitHub trigger")
        if scheme == "github-workflow":
            return self._dispatch_workflow_reviewer(agent, number)
        existing = self._existing_trigger(agent, number)
        if existing is not None:
            m = re.search(r"Collector invocation: (\d+)/(\d+)/", str(existing.get("body") or ""))
            existing["collector_run_id"] = int(m.group(1)) if m else self.run_id
            existing["collector_run_attempt"] = int(m.group(2)) if m else self.run_attempt
            if method.startswith("api:"):
                try:
                    result = self._api_result(agent, existing)
                except governance.transport.GitHubRequestError as exc:
                    # Reading the durable result for an existing trigger is
                    # post-trigger. Preserving the real id is mandatory; a
                    # dispatch-unavailable error marks the attempt unavailable,
                    # anything else fails closed.
                    if not reviewer_dispatch_unavailable(exc):
                        raise
                    existing.update(
                        provider=method.split(":", 1)[1],
                        head_sha=self.expected_head,
                        state="unavailable",
                        request_error=type(exc).__name__,
                        dispatch_status=exc.status_code,
                    )
                    return existing
                if result is not None:
                    existing.update(
                        provider=method.split(":", 1)[1],
                        response_digest=result.get("response_digest"),
                        head_sha=self.expected_head,
                        state=result.get("verdict"),
                        result_comment_id=result.get("comment_id"),
                    )
            return existing
        if method.startswith("github-review-request:"):
            trigger = self._post_trigger_comment(
                trigger_body(
                    self.expected_head, self.claims_id, agent, self.run_id, self.run_attempt, number, self.generation_id
                )
            )
            reviewer = method.split(":", 1)[1]
            try:
                governance.request_json(
                    self.repository,
                    self.token,
                    "POST",
                    f"pulls/{self.pr}/requested_reviewers",
                    {"reviewers": [reviewer]},
                )
            except governance.transport.GitHubRequestError as exc:
                if not reviewer_dispatch_unavailable(exc):
                    raise
                # The durable trigger comment exists; preserve its identity while
                # converting the reviewer-request refusal to an unavailable state.
                trigger["collector_run_id"] = self.run_id
                trigger["state"] = "unavailable"
                trigger["request_error"] = type(exc).__name__
                trigger["dispatch_status"] = exc.status_code
                return trigger
            trigger["collector_run_id"] = self.run_id
            return trigger
        if method.startswith("api:"):
            provider = method.split(":", 1)[1]
            trigger = self._post_trigger_comment(
                api_trigger_body(
                    self.expected_head,
                    self.claims_id,
                    agent,
                    self.run_id,
                    self.run_attempt,
                    number,
                    self.generation_id,
                )
            )
            # A durable trigger comment now exists. Any later reviewer/API
            # rejection must preserve that identity; letting it escape to the
            # outer pre-trigger handler would incorrectly record trigger_id=0.
            try:
                payload = self._invoke_external(agent, number)
            except governance.transport.GitHubRequestError as exc:
                if not reviewer_dispatch_unavailable(exc):
                    raise
                # The trigger comment already exists, so zero-id is no longer
                # truthful. Persist the provider refusal as the API result for
                # that real trigger; exhaustion can then verify both identity
                # and unavailability from durable evidence.
                payload = {
                    "verdict": "unavailable",
                    "summary": f"provider dispatch refused: HTTP {exc.status_code}",
                }
            state = "unavailable" if payload.get("verdict") == "unavailable" else external_verdict(payload)
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            # Once provider execution has returned, losing the result or ack
            # comment is an evidence-persistence failure, not reviewer
            # unavailability. Use _post_result_comment so the durable trigger
            # identity is preserved if GitHub refuses the post.
            result_comment = self._post_result_comment(
                api_result_body(
                    self.expected_head,
                    self.claims_id,
                    agent,
                    self.run_id,
                    int(trigger["id"]),
                    state,
                    str(payload.get("summary") or ""),
                    digest,
                ),
                int(trigger["id"]),
            )
            result_comment_id = result_comment.get("id") or result_comment.get("durable_trigger_id")
            ack_comment_id: int | None = None
            if state == "clear":
                ack_comment = self._post_result_comment(
                    json.dumps(
                        {
                            "schema": "hunter.review-ack.v1",
                            "head_sha": self.expected_head,
                            "claims_id": self.claims_id,
                            "verdict": "clear",
                            "summary": str(
                                payload.get("summary") or "Independent API review found no blocking defects."
                            ),
                            "collector_run_id": self.run_id,
                            "trigger_id": int(trigger["id"]),
                            "reviewer_agent": str(agent["id"]),
                            "response_digest": digest,
                        },
                        sort_keys=True,
                    ),
                    int(trigger["id"]),
                )
                if ack_comment.get("id"):
                    ack_comment_id = int(ack_comment["id"])
                else:
                    ack_comment_id = None
            return_state = (
                state if (result_comment.get("id") and (state != "clear" or ack_comment_id)) else "unavailable"
            )
            return {
                **trigger,
                "provider": provider,
                "response_digest": digest,
                "head_sha": self.expected_head,
                "state": return_state,
                "result_comment_id": result_comment_id,
                "collector_run_id": self.run_id,
                "post_trigger_refusal": result_comment.get("post_trigger_refusal"),
                "dispatch_status": result_comment.get("dispatch_status"),
            }
        body = trigger_body(
            self.expected_head, self.claims_id, agent, self.run_id, self.run_attempt, number, self.generation_id
        )
        trigger = self._post_trigger_comment(body)
        trigger["collector_run_id"] = self.run_id
        return trigger

    @staticmethod
    def _native_clear(body: str, head: str) -> bool:
        return governance.native_codex_clear_review(body, head)

    @staticmethod
    def _unavailable(body: str) -> bool:
        text = body.lower()
        normalized = " ".join(text.split())
        return bool(
            re.fullmatch(r"(?:codex )?usage limit reached\.?(?: try again later\.?)?", normalized)
            or re.fullmatch(r"codex is temporarily unavailable\. please try again later\.?", normalized)
            or re.fullmatch(r"to use codex(?: here)?, create a codex account and connect to github\.?", normalized)
        )

    def response_state(self, agent: dict[str, Any], trigger: dict[str, Any]) -> str:
        if trigger.get("state") == "unavailable":
            return "unavailable"
        if trigger_scheme(agent) == "github-workflow":
            return self._workflow_review_state(trigger)
        if str(agent["trigger_method"]).startswith("api:"):
            result = self._api_result(agent, trigger)
            if result is None:
                return "waiting"
            return str(result["verdict"])
        login = governance.reviewer_login(agent)
        created = str(trigger["created_at"])
        matching_reviews = [
            item
            for item in _pages(self.repository, self.token, f"pulls/{self.pr}/reviews")
            if (item.get("user") or {}).get("login", "").lower() == login
            and str(item.get("submitted_at") or "") >= created
            and item.get("commit_id") == self.expected_head
        ]
        if matching_reviews:
            item = max(matching_reviews, key=lambda value: int(value.get("id") or 0))
            state = str(item.get("state") or "").upper()
            body = str(item.get("body") or "")
            if state == "CHANGES_REQUESTED":
                return "blocking"
            if state == "APPROVED":
                return "clear"
            if state == "COMMENTED":
                if agent.get("id") == "codex" and governance._substantive_review_body(body):
                    return "clear" if self._native_clear(body, self.expected_head) else "blocking"
                if agent.get("id") == "copilot":
                    comments = _pages(self.repository, self.token, f"pulls/{self.pr}/reviews/{item['id']}/comments")
                    verdict = governance.native_copilot_verdict(body, len(comments))
                    return verdict if verdict != "unknown" else "blocking"
                if governance._substantive_review_body(body):
                    return "blocking"
        for item in _pages(self.repository, self.token, f"issues/{self.pr}/comments"):
            if (item.get("user") or {}).get("login", "").lower() != login:
                continue
            if str(item.get("created_at") or "") < created:
                continue
            body = str(item.get("body") or "")
            ack = governance.review_acknowledgement(body)
            if (
                ack is not None
                and ack["head_sha"] == self.expected_head
                and ack["claims_id"] == self.claims_id
                and ack.get("collector_run_id") == int(trigger.get("collector_run_id") or self.run_id)
                and ack.get("trigger_id") == int(trigger.get("id") or 0)
            ):
                return "clear"
            if agent.get("id") == "codex" and self._native_clear(body, self.expected_head):
                return "clear"
            if self._unavailable(body):
                return "unavailable"
            if governance._substantive_review_body(body):
                return "blocking"
        return "waiting"

    def responded(self, agent: dict[str, Any], trigger: dict[str, Any]) -> bool:
        return self.response_state(agent, trigger) in {"clear", "blocking"}

    def acknowledged(self, agent: dict[str, Any], trigger: dict[str, Any]) -> bool:
        """Whether this reviewer has demonstrably received and started the job.

        Acknowledgement is the strongest delivery signal each trigger scheme
        actually exposes, and it is deliberately not the same question as
        "has it answered?". A dispatched workflow is acknowledged only once a
        runner picked the run up, because a queued run is exactly the offline-Mac
        case the short budget exists to fail over from. For the comment and API
        schemes the invocation is delivered synchronously -- GitHub confirms the
        trigger comment it created, and the provider call returns -- so delivery
        is settled the moment the trigger exists, and the reviewer then gets its
        full review budget rather than being abandoned for being slow.
        """

        if trigger_scheme(agent) == "github-workflow":
            if str(trigger.get("state") or "") == "unavailable":
                return False
            run = self._adopt_workflow_run(trigger)
            return run is not None and str(run.get("status") or "") in STARTED_RUN_STATES
        return type(trigger.get("id")) is int and int(trigger["id"]) > 0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_artifact(repository: str, token: str, artifact_id: int) -> bytes:
    url = f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}/zip"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=30) as response:
            data = response.read(LIMIT + 1)
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location", "")
        if exc.code != 302 or not location.startswith("https://"):
            raise
        # The signed download URL is issued by GitHub. Never forward the API bearer token.
        with urllib.request.urlopen(location, timeout=30) as response:
            data = response.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("review receipt archive is too large")
    return data


def local_review_result(repository: str, token: str, run_id: int, pr: int, head: str) -> dict[str, Any] | None:
    """The structured review a dispatched reviewer run published, or nothing.

    The artifact is addressed by the run that produced it and by the exact head
    it was dispatched for, and its immutable GitHub digest is re-derived from the
    bytes that were actually downloaded, so a result cannot be substituted from
    another run, another candidate, or a rewritten archive. Anything unreadable
    is no result at all; the caller reads that as unavailability rather than as
    a verdict.
    """

    name = f"hunter-local-review-{pr}-{head}"
    artifacts = [
        item
        for item in _pages(repository, token, f"actions/runs/{run_id}/artifacts", "artifacts")
        if item.get("name") == name
    ]
    if len(artifacts) != 1 or artifacts[0].get("expired") is not False:
        return None
    artifact = artifacts[0]
    if (artifact.get("workflow_run") or {}).get("id") != run_id:
        return None
    archive = download_artifact(repository, token, int(artifact["id"]))
    if artifact.get("digest") != "sha256:" + hashlib.sha256(archive).hexdigest():
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            if bundle.namelist() != ["reviewer-result.json"]:
                return None
            if bundle.getinfo("reviewer-result.json").file_size > LIMIT:
                return None
            payload = json.loads(bundle.read("reviewer-result.json"))
    except (zipfile.BadZipFile, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _verify_workflow_trigger(
    repository: str,
    token: str,
    agent: Mapping[str, Any],
    record: Mapping[str, Any],
    number: int,
    *,
    head: str,
    claims_id: str,
    branch: str,
    run_id: int,
    run_attempt: int,
    seen: set[tuple[str, int]],
) -> None:
    """Re-derive a recorded workflow-dispatched invocation from GitHub itself.

    The recorded identity is the reviewer run, so the run is fetched and every
    property that makes it *this* invocation is recomputed rather than trusted:
    the workflow the pool declares, the trusted default branch, the dispatch
    event, and the correlation identity this exact collector run, attempt,
    candidate head, claims digest and attempt number produce. A dispatch the
    trusted branch never accepted has no run at all, which is admissible only as
    recorded unavailability.
    """

    trigger_id = int(record["trigger_id"])
    if trigger_id == 0:
        # No run was ever observed for this dispatch: the trusted branch refused
        # it, or no runner picked it up inside the acknowledgement budget. Those
        # are the only two outcomes such an attempt may claim -- a verdict or a
        # spent review budget would assert a run that demonstrably never ran.
        if record.get("outcome") not in {"unavailable", "unacknowledged"}:
            raise ValueError("a workflow reviewer with no trusted run must record unavailability")
        return
    seen.add(("github-workflow", trigger_id))
    workflow = workflow_trigger_file(agent)
    expected_name = LOCAL_REVIEW_RUN_NAME_PREFIX + workflow_correlation_id(
        head,
        claims_id,
        str(agent["id"]),
        int(record.get("collector_run_id") or run_id),
        int(record.get("collector_run_attempt") or run_attempt),
        number,
    )
    try:
        payload = governance.request_json(repository, token, "GET", f"actions/runs/{trigger_id}")
    except Exception as exc:
        raise ValueError("trusted reviewer trigger evidence is unavailable") from exc
    if (
        not isinstance(payload, dict)
        or str(payload.get("path") or "") != f".github/workflows/{workflow}"
        or str(payload.get("event") or "") != "workflow_dispatch"
        or str(payload.get("head_branch") or "") != branch
        or expected_name not in {str(payload.get("name") or ""), str(payload.get("display_title") or "")}
        or str(payload.get("created_at") or "") != str(record.get("trigger_created_at") or "")
    ):
        raise ValueError("actual trusted reviewer trigger mismatch")


def load_exhaustion(
    repository: str, token: str, pr: int, head: str, pool: dict[str, Any], run_id: Any, authority_type: str
) -> dict[str, Any]:
    """Verify a trusted collector receipt for `authority_type` at this exact head.

    `authority_type` carries no default. The last-resort guard is the only
    authority that has to clear the extra live snapshot gates below, and it is
    recognised by comparing this argument against the pool's declared
    `last_resort`; a default here would let a caller that forgot to pass it be
    read as some other reviewer and skip those gates entirely.
    """
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("a trusted collector run id is required")
    backend = GitHubBackend(repository, token, pr, head, "", run_id, 1)
    if backend.head() != head:
        raise ValueError("collector receipt does not match the current pull-request HEAD")
    repo = governance.request_json(repository, token, "GET", "")
    branch = repo["default_branch"]
    run = governance.request_json(repository, token, "GET", f"actions/runs/{run_id}")
    revision = str(run.get("head_sha") or "") if isinstance(run, dict) else ""
    if not isinstance(run, dict) or not valid_run(run, run_id, branch, revision):
        raise ValueError("collector did not execute the trusted default-branch revision")
    if not _run_on_default_branch_history(repository, token, branch, revision):
        raise ValueError("collector run is no longer on trusted default-branch history")
    name = f'hunter-reviewer-results-{head}-{run["run_attempt"]}'
    artifacts = [
        a for a in _pages(repository, token, f"actions/runs/{run_id}/artifacts", "artifacts") if a.get("name") == name
    ]
    if len(artifacts) != 1 or artifacts[0].get("expired") is not False:
        raise ValueError("unique immutable collector artifact is unavailable")
    artifact = artifacts[0]
    if (artifact.get("workflow_run") or {}).get("id") != run_id:
        raise ValueError("artifact belongs to another run")
    archive = download_artifact(repository, token, int(artifact["id"]))
    if artifact.get("digest") != "sha256:" + hashlib.sha256(archive).hexdigest():
        raise ValueError("collector artifact digest mismatch")
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        if bundle.namelist() != ["reviewer-results.json"] or bundle.getinfo("reviewer-results.json").file_size > LIMIT:
            raise ValueError("malformed collector archive")
        receipt = json.loads(bundle.read("reviewer-results.json"))
    expected = {
        "schema": SCHEMA,
        "repository": repository,
        "pr_number": pr,
        "head_sha": head,
        "run_id": run_id,
        "run_attempt": run["run_attempt"],
        "configuration_digest": configuration_digest(pool),
    }
    if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in expected.items()):
        raise ValueError("collector identity/configuration mismatch")
    if (
        type(receipt.get("pr_number")) is not int
        or type(receipt.get("run_id")) is not int
        or type(receipt.get("run_attempt")) is not int
    ):
        raise ValueError("collector numeric identity fields must be integers")
    if not re.fullmatch("[0-9a-f]{64}", str(receipt.get("claims_id") or "")):
        raise ValueError("collector claims digest is malformed")
    receipt_generation = receipt.get("remediation_generation_id", BASE_GENERATION)
    if not isinstance(receipt_generation, str) or (
        receipt_generation != BASE_GENERATION and not orchestration.GENERATION_ID_PATTERN.fullmatch(receipt_generation)
    ):
        raise ValueError("collector remediation generation is malformed")
    # Exhaustion is generation-scoped evidence, not a permanent property of the
    # head: once the blocking findings of that generation were resolved, every
    # reviewer it recorded as exhausted is owed another invocation, so reusing
    # the receipt would authorise a lower-priority reviewer over one that has not
    # actually been asked about the remediated state.
    if receipt_generation != orchestration.current_remediation_generation(
        repository, token, pr, head, receipt["claims_id"]
    ):
        raise ValueError("collector receipt predates the current remediation generation")
    agents = review.enabled_pool_reviewers(pool)
    own = next((a["priority"] for a in agents if a["id"] == authority_type), float("inf"))
    # Ordering evidence covers every *enabled* reviewer above this authority, so
    # a reviewer the collector skipped is still caught positionally. Exhaustion
    # evidence is emitted only for the *authority-eligible* ones: a triage-only
    # reviewer is never review authority, so there is nothing for it to exhaust,
    # and naming it in reviewer_attempts would be rejected by the shared
    # authority verifier as an unknown agent.
    required = sorted((a for a in agents if a["priority"] < own), key=lambda a: a["priority"])
    records = receipt.get("attempts")
    if not isinstance(records, list):
        raise ValueError("collector attempts are missing")
    result = []
    offset = 0
    seen: set[tuple[str, int]] = set()
    for agent in required:
        authority_eligible = agent.get("authority_eligible") is not False
        scheme = trigger_scheme(agent)
        count = 1 + (pool["timeout_policy"]["retries_per_agent"] if agent["retryable"] else 0)
        # How many invocations this reviewer actually cost. Only a silent
        # timeout is retried, so a reviewer that answered or declared itself
        # unavailable settles on its first attempt; claiming the full retry
        # budget for it would assert invocations that never happened.
        spent = 0
        for number in range(1, count + 1):
            if offset >= len(records) or not isinstance(records[offset], dict):
                raise ValueError("enabled reviewer was skipped or not fully retried")
            record = records[offset]
            offset += 1
            spent = number
            expected_attempt = {
                k: agent[k]
                for k in (
                    "priority",
                    "ack_timeout_seconds",
                    "review_timeout_seconds",
                    "trigger_method",
                    "evidence_parser",
                    "retryable",
                )
            }
            expected_attempt.update(agent_id=agent["id"], attempt_number=number)
            elapsed = record.get("elapsed_seconds")
            outcome = record.get("outcome")
            # A triage-only reviewer may legitimately have answered; only an
            # authority-eligible reviewer has to have been exhausted.
            allowed_outcomes = (
                {"timed_out", "unavailable", "unacknowledged"}
                if authority_eligible
                else {"timed_out", "unavailable", "unacknowledged", "clear", "blocking"}
            )
            elapsed_valid = (
                type(elapsed) in (int, float)
                and 0 <= elapsed < 7200
                and (outcome != "timed_out" or elapsed >= agent["review_timeout_seconds"])
                and (outcome != "unacknowledged" or elapsed >= agent["ack_timeout_seconds"])
            )
            if (
                any(record.get(k) != v for k, v in expected_attempt.items())
                or outcome not in allowed_outcomes
                or type(record.get("priority")) is not int
                or type(record.get("ack_timeout_seconds")) is not int
                or type(record.get("review_timeout_seconds")) is not int
                or type(record.get("retryable")) is not bool
                or type(record.get("attempt_number")) is not int
                or not elapsed_valid
            ):
                raise ValueError("trusted timeout/configuration/retry result mismatch")
            trigger_id = record.get("trigger_id")
            if type(trigger_id) is not int or trigger_id < 0 or (scheme, trigger_id) in seen:
                raise ValueError("duplicate or invalid invocation identity")
            if scheme == "github-workflow":
                _verify_workflow_trigger(
                    repository,
                    token,
                    agent,
                    record,
                    number,
                    head=head,
                    claims_id=receipt["claims_id"],
                    branch=branch,
                    run_id=run_id,
                    run_attempt=run["run_attempt"],
                    seen=seen,
                )
                if outcome != "timed_out":
                    break
                continue
            if trigger_id == 0:
                # The trigger was refused before it could exist: no comment was
                # posted, so there is no id any evidence could be verified
                # against. Admissible only as recorded unavailability, exactly
                # as a workflow dispatch the trusted branch never accepted is --
                # a verdict or a spent review budget would assert a trigger that
                # demonstrably never existed. Absence is not an identity, so it
                # is never added to `seen`: two undrivable reviewers must not
                # collide as a duplicate invocation.
                if outcome not in {"unavailable", "unacknowledged"}:
                    raise ValueError("a reviewer with no trusted trigger must record unavailability")
                if outcome != "timed_out":
                    break
                continue
            seen.add((scheme, trigger_id))
            try:
                trigger = governance.request_json(repository, token, "GET", f"issues/comments/{trigger_id}")
            except Exception as exc:
                raise ValueError("trusted reviewer trigger evidence is unavailable") from exc
            if str(agent["trigger_method"]).startswith("api:"):
                parsed_trigger = parse_api_trigger(str(trigger.get("body") or ""))
                expected_trigger = api_trigger_payload(
                    head,
                    receipt["claims_id"],
                    agent,
                    int(record.get("collector_run_id") or run_id),
                    int(record.get("collector_run_attempt") or run["run_attempt"]),
                    number,
                    receipt_generation,
                )
                body_matches = parsed_trigger == expected_trigger
            else:
                expected_body = trigger_body(
                    head,
                    receipt["claims_id"],
                    agent,
                    int(record.get("collector_run_id") or run_id),
                    int(record.get("collector_run_attempt") or run["run_attempt"]),
                    number,
                    receipt_generation,
                )
                body_matches = trigger.get("body") == expected_body
            if (
                not body_matches
                or trigger.get("created_at") != record.get("trigger_created_at")
                or (trigger.get("user") or {}).get("login") != "github-actions[bot]"
                or trigger.get("issue_url") != f"https://api.github.com/repos/{repository}/issues/{pr}"
            ):
                raise ValueError("actual trusted reviewer trigger mismatch")
            live_state = GitHubBackend(
                repository,
                token,
                pr,
                head,
                receipt["claims_id"],
                int(record.get("collector_run_id") or run_id),
                int(record.get("collector_run_attempt") or run["run_attempt"]),
                receipt_generation,
            ).response_state(agent, trigger)
            if live_state in {"clear", "blocking"}:
                raise ValueError("higher-priority reviewer is available; exhaustion cannot be reused")
            if outcome == "unavailable" and live_state != "unavailable":
                raise ValueError("recorded reviewer unavailability is no longer verifiable")
            if outcome == "timed_out" and live_state == "unavailable":
                raise ValueError("collector timeout receipt disagrees with explicit reviewer unavailability")
            if str(agent["trigger_method"]).startswith("api:"):
                api_result = GitHubBackend(
                    repository,
                    token,
                    pr,
                    head,
                    receipt["claims_id"],
                    int(record.get("collector_run_id") or run_id),
                    int(record.get("collector_run_attempt") or run["run_attempt"]),
                    receipt_generation,
                )._api_result(agent, trigger)
                if api_result is None:
                    raise ValueError("trusted API reviewer result evidence is unavailable")
                if api_result["response_digest"] != record.get("response_digest"):
                    raise ValueError("trusted API reviewer result digest mismatch")
                if record.get("provider") != str(agent["trigger_method"]).split(":", 1)[1]:
                    raise ValueError("trusted API reviewer provider mismatch")
                if record.get("head_sha") != head:
                    raise ValueError("trusted API reviewer head binding mismatch")
            if outcome != "timed_out":
                break
        if not authority_eligible:
            continue
        terminal = records[offset - 1].get("outcome") if spent else "timed_out"
        result.append(
            {
                "agent_id": agent["id"],
                "status": "exhausted",
                "reason": (
                    "trusted reviewer reported explicit unavailability"
                    if terminal == "unavailable"
                    else "trusted collector timed out every configured invocation"
                ),
                "ack_timeout_seconds": agent["ack_timeout_seconds"],
                "review_timeout_seconds": agent["review_timeout_seconds"],
                "failure_class": "permanent" if terminal == "unavailable" else "transient",
                "attempt_count": spent,
                "invocation_reference": f"actions/runs/{run_id}",
            }
        )
    if backend.head() != head:
        raise ValueError("pull-request HEAD changed while collector evidence was verified")
    evidence: dict[str, Any] = {"reviewer_attempts": result, "collector_run_id": run_id}
    if authority_type == str(pool["last_resort"]):
        threads, thread_error = governance.read_unresolved_review_threads(repository, token, pr)
        if thread_error or threads:
            raise ValueError(thread_error or "last-resort review requires zero unresolved threads")
        trusted_state, trusted_detail = governance.read_trusted_upgrade_status(repository, token, head, pr)
        if trusted_state != "success":
            raise ValueError(trusted_detail or "last-resort review requires successful trusted preflight")
        checks = _pages(repository, token, f"commits/{head}/check-runs", "check_runs")
        preflights = [item for item in checks if item.get("name") == "Governance Agent Preflight"]
        latest = max(preflights, key=lambda item: int(item.get("id") or 0), default=None)
        if not latest or latest.get("status") != "completed" or latest.get("conclusion") != "success":
            raise ValueError("last-resort review requires successful independent Governance Agent Preflight")
        evidence.update(
            {
                "fallback_reason": "trusted collector exhausted every configured higher-priority reviewer",
                "unresolved_thread_count": 0,
                "governance_state": "success",
                "trusted_preflight_state": "success",
                "structured_evidence_status": "complete",
            }
        )
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument(
        "--generation",
        default=BASE_GENERATION,
        help="Remediation generation this run was dispatched for; empty to use whatever the evidence derives.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{40}", args.head):
        raise ValueError("exact HEAD is required")
    declared_generation = str(args.generation or BASE_GENERATION).strip()
    if declared_generation != BASE_GENERATION and not orchestration.GENERATION_ID_PATTERN.fullmatch(
        declared_generation
    ):
        raise ValueError("remediation generation identity is malformed")
    repository, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    run_id, run_attempt = int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"])
    pool, error = review.load_reviewer_pool()
    if pool is None or error:
        raise ValueError(error)
    state, document, error = governance.read_head_pre_ready_review(repository, token, args.head)
    if state != "present" or not isinstance(document, dict):
        raise ValueError(f"review request unavailable: {error}")
    claims_id = review.review_id(document["claims"])
    if document.get("review_request") != {"schema": "hunter.review-request.v1", "claims_id": claims_id}:
        raise ValueError("an explicit current review request is required")
    # The dispatched generation correlates the run; it never authorises it. This
    # trusted default-branch collector derives the generation itself from the
    # authenticated review-thread state, so the generation a reviewer invocation
    # is spent against is always the evidence's and never the dispatcher's. An
    # input is therefore only ever checked *against* that derivation: a
    # generation the evidence does not support fails closed instead of being
    # used. The pull-request-target entry point supplies none at all -- it has no
    # run name to correlate -- and reviews the generation the evidence derives.
    generation = orchestration.current_remediation_generation(repository, token, args.pr, args.head, claims_id)
    if declared_generation and declared_generation != generation:
        raise ValueError("dispatched remediation generation is not the one trusted review-thread state derives")
    backend = GitHubBackend(repository, token, args.pr, args.head, claims_id, run_id, run_attempt, generation)
    attempts = collect_attempts(pool, args.head, backend)
    if backend.head() != args.head:
        raise ValueError("HEAD changed before receipt publication")
    receipt = {
        "schema": SCHEMA,
        "repository": repository,
        "pr_number": args.pr,
        "head_sha": args.head,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "claims_id": claims_id,
        "remediation_generation_id": generation,
        "configuration_digest": configuration_digest(pool),
        "attempts": attempts,
    }
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
