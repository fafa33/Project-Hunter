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
from pathlib import Path
from typing import Any, Protocol

import hunter_governance_review_v2 as governance
import hunter_pre_ready_review as review

WORKFLOW = ".github/workflows/hunter-reviewer-collector.yml"
SCHEMA = "hunter.reviewer-collection.v1"
LIMIT = 2_000_000
#: The run-name prefix the local reviewer workflow renders before the dispatch's
#: correlation identity. The workflow and this selector must agree, so the
#: correlation is asserted by test rather than only by convention.
LOCAL_REVIEW_RUN_NAME_PREFIX = "Hunter Local Reviewer "


def configuration_digest(pool: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(pool, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def authority_attempt_records(pool: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only receipt rows emitted by authority-eligible reviewers."""
    authority_ids = {str(agent["id"]) for agent in review.authority_pool_reviewers(pool)}
    return [record for record in records if isinstance(record, dict) and str(record.get("agent_id")) in authority_ids]


class Backend(Protocol):
    def head(self) -> str: ...
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...
    def trigger(self, agent: dict[str, Any], number: int) -> dict[str, Any]: ...
    def responded(self, agent: dict[str, Any], trigger: dict[str, Any]) -> bool: ...


def collect_attempts(pool: dict[str, Any], head: str, backend: Backend) -> list[dict[str, Any]]:
    records = []
    agents = sorted(review.enabled_pool_reviewers(pool), key=lambda a: a["priority"])
    for agent in agents:
        availability_fn = getattr(backend, "availability", None)
        availability_state = availability_fn(agent) if availability_fn is not None else "online"
        if availability_state in {"offline", "missing"}:
            records.append(
                {
                    "agent_id": agent["id"],
                    "priority": agent["priority"],
                    "ack_timeout_seconds": agent["ack_timeout_seconds"],
                    "review_timeout_seconds": agent["review_timeout_seconds"],
                    "trigger_method": agent["trigger_method"],
                    "evidence_parser": agent["evidence_parser"],
                    "retryable": agent["retryable"],
                    "attempt_number": 1,
                    "trigger_id": 0,
                    "trigger_created_at": "",
                    "ack_elapsed_seconds": 0,
                    "elapsed_seconds": 0,
                    "outcome": "unavailable",
                    "failure_class": "transient",
                    "availability_state": availability_state,
                }
            )
            continue
        count = (
            1
            if agent.get("id") == "local-ollama"
            else 1 + (pool["timeout_policy"]["retries_per_agent"] if agent["retryable"] else 0)
        )
        for number in range(1, count + 1):
            if backend.head() != head:
                raise ValueError("HEAD changed before reviewer invocation")
            try:
                trigger = backend.trigger(agent, number)
            except governance.transport.GitHubRequestError as exc:
                if exc.category == "permanent" and exc.status_code == 403:
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
                            "ack_elapsed_seconds": 0,
                            "elapsed_seconds": 0,
                            "outcome": "unavailable",
                            "failure_class": "permanent",
                            "failure_status": 403,
                            "availability_state": "trigger-denied",
                        }
                    )
                    break
                raise
            start = backend.now()
            ack_deadline = start + agent["ack_timeout_seconds"]
            ack_fn = getattr(backend, "acknowledged", backend.responded)
            complete_fn = getattr(backend, "completed", backend.responded)
            acknowledged = False
            while backend.now() < ack_deadline:
                if backend.head() != head:
                    raise ValueError("HEAD changed during reviewer acknowledgement")
                if ack_fn(agent, trigger):
                    acknowledged = True
                    break
                backend.sleep(min(5, ack_deadline - backend.now()))
            ack_elapsed = backend.now() - start
            if not acknowledged:
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
                        "ack_elapsed_seconds": ack_elapsed,
                        "elapsed_seconds": ack_elapsed,
                        "outcome": "unavailable",
                        "failure_class": "transient",
                    }
                )
                continue
            review_started_at = backend.now()
            review_deadline = review_started_at + agent["review_timeout_seconds"]
            completed = complete_fn(agent, trigger)
            while not completed and backend.now() < review_deadline:
                if backend.head() != head:
                    raise ValueError("HEAD changed during reviewer execution")
                backend.sleep(min(5, review_deadline - backend.now()))
                completed = complete_fn(agent, trigger)
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
                    "ack_elapsed_seconds": ack_elapsed,
                    "elapsed_seconds": backend.now() - start,
                    "outcome": "responded" if completed else "timed_out",
                }
            )
            if completed and agent.get("authority_eligible", True):
                return records
    return records


def valid_run(run: dict[str, Any], run_id: int, branch: str, revision: str) -> bool:
    return (
        run.get("id") == run_id
        and type(run.get("run_attempt")) is int
        and run["run_attempt"] >= 1
        and run.get("head_branch") == branch
        and run.get("head_sha") == revision
        and run.get("path") == WORKFLOW
        and run.get("event") == "workflow_dispatch"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
    )


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


def trigger_body(head: str, claims_id: str, agent: dict[str, Any], run_id: int, run_attempt: int, number: int) -> str:
    method = str(agent["trigger_method"])
    if not method.startswith("github-pr-comment:") or not governance.reviewer_login(agent):
        raise ValueError("reviewer has no supported authenticated GitHub trigger")
    ack = {
        "schema": "hunter.review-ack.v1",
        "head_sha": head,
        "claims_id": claims_id,
        "verdict": "clear",
        "summary": "<your substantive review summary>",
        "collector_run_id": run_id,
    }
    return (
        method.split(":", 1)[1] + f"\nReview exact HEAD {head} and the complete review request in "
        f"{review.REVIEW_RELATIVE_PATH}. Report any blocking findings; do not issue a clear verdict if any remain. "
        "After completing the substantive review, if all requested claims are satisfied and no blockers remain, "
        "reply with only this JSON result, filling in your own substantive summary: "
        + json.dumps(ack)
        + f'\nCollector invocation: {run_id}/{run_attempt}/{agent["id"]}/{number}.'
    )


class GitHubBackend:
    def __init__(self, repository: str, token: str, pr: int, head: str, claims_id: str, run_id: int, run_attempt: int):
        self.repository, self.token, self.pr = repository, token, pr
        self.expected_head, self.claims_id = head, claims_id
        self.run_id, self.run_attempt = run_id, run_attempt

    def head(self) -> str:
        pr = governance.request_json(self.repository, self.token, "GET", f"pulls/{self.pr}")
        if not isinstance(pr, dict) or pr.get("state") != "open":
            raise ValueError("pull request is unavailable or closed")
        return str(pr["head"]["sha"])

    def availability(self, agent: dict[str, Any]) -> str:
        if agent.get("id") != "local-ollama":
            return "online"
        import hunter_review_orchestrator as orchestrator

        return orchestrator.runner_state(self.repository, self.token)

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def correlation_id(self, agent: dict[str, Any], number: int) -> str:
        """A unique identity for this exact dispatch of this exact attempt.

        Bound to the collector run and attempt, the candidate head, the reviewer
        and the attempt number, so no two invocations -- across retries, collector
        re-runs, or candidates -- can share one.
        """

        material = "/".join(
            (
                SCHEMA,
                self.repository,
                str(self.pr),
                self.expected_head,
                str(self.run_id),
                str(self.run_attempt),
                str(agent.get("id") or ""),
                str(number),
            )
        )
        return hashlib.sha256(material.encode()).hexdigest()

    def trigger(self, agent: dict[str, Any], number: int) -> dict[str, Any]:
        method = str(agent.get("trigger_method") or "")
        if method.startswith("github-workflow:"):
            workflow = method.split(":", 1)[1]
            created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            correlation = self.correlation_id(agent, number)
            governance.request_json(
                self.repository,
                self.token,
                "POST",
                f"actions/workflows/{workflow}/dispatches",
                {
                    "ref": "main",
                    "inputs": {
                        "pr_number": str(self.pr),
                        "head_sha": self.expected_head,
                        "claims_id": self.claims_id,
                        "correlation_id": correlation,
                    },
                },
            )
            return {
                "id": 0,
                "created_at": created_at,
                "kind": "local-workflow",
                "workflow": workflow,
                "number": number,
                "correlation_id": correlation,
            }
        body = trigger_body(self.expected_head, self.claims_id, agent, self.run_id, self.run_attempt, number)
        result = governance.request_json(
            self.repository, self.token, "POST", f"issues/{self.pr}/comments", {"body": body}
        )
        if not isinstance(result, dict) or type(result.get("id")) is not int or not result.get("created_at"):
            raise ValueError("GitHub did not confirm actual reviewer invocation")
        return result

    def _local_run(self, trigger: dict[str, Any]) -> dict[str, Any] | None:
        """The workflow run this exact dispatch produced, or nothing.

        Selection is by the dispatch's own correlation identity, which the
        workflow renders as its run name. Timestamp, event and branch are shared
        by every dispatch of this workflow, so a concurrent candidate's run, an
        earlier attempt's run, or a manual dispatch could otherwise be read as
        this attempt's acknowledgement and completion.
        """

        workflow = str(trigger.get("workflow") or "hunter-local-reviewer.yml")
        correlation = str(trigger.get("correlation_id") or "")
        if not correlation:
            return None
        payload = governance.request_json(
            self.repository,
            self.token,
            "GET",
            f"actions/workflows/{workflow}/runs?event=workflow_dispatch&branch=main&per_page=50",
        )
        runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
        expected = LOCAL_REVIEW_RUN_NAME_PREFIX + correlation
        candidates = [
            run
            for run in runs
            if isinstance(run, dict)
            and str(run.get("display_title") or run.get("name") or "") == expected
            and str(run.get("event") or "") == "workflow_dispatch"
            and str(run.get("head_branch") or "") == "main"
        ]
        return max(candidates, key=lambda run: int(run.get("id") or 0), default=None)

    def acknowledged(self, agent: dict[str, Any], trigger: dict[str, Any]) -> bool:
        if trigger.get("kind") == "local-workflow":
            run = self._local_run(trigger)
            if not run:
                return False
            trigger["id"] = int(run.get("id") or 0)
            jobs = governance.request_json(
                self.repository, self.token, "GET", f"actions/runs/{trigger['id']}/jobs?per_page=100"
            )
            rows = jobs.get("jobs", []) if isinstance(jobs, dict) else []
            return any(
                str(job.get("status") or "") in {"in_progress", "completed"} for job in rows if isinstance(job, dict)
            )
        return self.responded(agent, trigger)

    def completed(self, agent: dict[str, Any], trigger: dict[str, Any]) -> bool:
        if trigger.get("kind") == "local-workflow":
            run = self._local_run(trigger)
            if not run:
                return False
            trigger["id"] = int(run.get("id") or 0)
            return str(run.get("status") or "") == "completed" and str(run.get("conclusion") or "") == "success"
        login = governance.reviewer_login(agent)
        for item in _pages(self.repository, self.token, f"pulls/{self.pr}/reviews"):
            if (
                (item.get("user") or {}).get("login", "").lower() == login
                and item.get("commit_id") == self.expected_head
                and item.get("state") in {"APPROVED", "COMMENTED", "CHANGES_REQUESTED"}
                and str(item.get("submitted_at") or item.get("created_at") or "") >= trigger["created_at"]
            ):
                return True
        for item in _pages(self.repository, self.token, f"issues/{self.pr}/comments"):
            if (item.get("user") or {}).get("login", "").lower() != login:
                continue
            if str(item.get("created_at") or "") < trigger["created_at"]:
                continue
            ack = governance.review_acknowledgement(str(item.get("body") or ""))
            if (
                ack
                and ack.get("head_sha") == self.expected_head
                and ack.get("claims_id") == self.claims_id
                and ack.get("collector_run_id") == self.run_id
            ):
                return True
        return False

    def responded(self, agent: dict[str, Any], trigger: dict[str, Any]) -> bool:
        login = governance.reviewer_login(agent)
        for item in _pages(self.repository, self.token, f"pulls/{self.pr}/reviews"):
            if (item.get("user") or {}).get("login", "").lower() == login and item.get(
                "commit_id"
            ) == self.expected_head:
                return True
        for item in _pages(self.repository, self.token, f"issues/{self.pr}/comments"):
            if (item.get("user") or {}).get("login", "").lower() == login and str(
                item.get("created_at") or ""
            ) >= trigger["created_at"]:
                return True
        for item in _pages(self.repository, self.token, f'issues/comments/{trigger["id"]}/reactions'):
            if (item.get("user") or {}).get("login", "").lower() == login:
                return True
        return False


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


def load_exhaustion(
    repository: str, token: str, pr: int, head: str, pool: dict[str, Any], run_id: Any, authority_type: str = "opencode"
) -> dict[str, Any]:
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("a trusted collector run id is required")
    backend = GitHubBackend(repository, token, pr, head, "", run_id, 1)
    if backend.head() != head:
        raise ValueError("collector receipt does not match the current pull-request HEAD")
    repo = governance.request_json(repository, token, "GET", "")
    branch = repo["default_branch"]
    revision = governance.request_json(repository, token, "GET", f"commits/{branch}")["sha"]
    run = governance.request_json(repository, token, "GET", f"actions/runs/{run_id}")
    if not isinstance(run, dict) or not valid_run(run, run_id, branch, revision):
        raise ValueError("collector did not execute the trusted default-branch revision")
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
    agents = review.authority_pool_reviewers(pool)
    own = next((a["priority"] for a in agents if a["id"] == authority_type), float("inf"))
    required = sorted((a for a in agents if a["priority"] < own), key=lambda a: a["priority"])
    records = receipt.get("attempts")
    if not isinstance(records, list):
        raise ValueError("collector attempts are missing")
    records = authority_attempt_records(pool, records)
    result = []
    offset = 0
    seen = set()
    for agent in required:
        count = 1 + (pool["timeout_policy"]["retries_per_agent"] if agent["retryable"] else 0)
        for number in range(1, count + 1):
            if offset >= len(records) or not isinstance(records[offset], dict):
                raise ValueError("enabled reviewer was skipped or not fully retried")
            record = records[offset]
            offset += 1
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
            outcome = record.get("outcome")
            elapsed = record.get("elapsed_seconds")
            ack_elapsed = record.get("ack_elapsed_seconds")
            if outcome not in {"unavailable", "timed_out"}:
                raise ValueError("trusted timeout/configuration/retry result mismatch")
            trigger_denied = (
                outcome == "unavailable"
                and record.get("failure_class") == "permanent"
                and record.get("failure_status") == 403
                and record.get("availability_state") == "trigger-denied"
                and record.get("trigger_id") == 0
                and record.get("trigger_created_at") == ""
                and elapsed == 0
                and ack_elapsed == 0
            )
            minimum = agent["ack_timeout_seconds"] if outcome == "unavailable" else agent["review_timeout_seconds"]
            if (
                any(record.get(k) != v for k, v in expected_attempt.items())
                or type(record.get("priority")) is not int
                or type(record.get("ack_timeout_seconds")) is not int
                or type(record.get("review_timeout_seconds")) is not int
                or type(record.get("retryable")) is not bool
                or type(record.get("attempt_number")) is not int
                or type(elapsed) not in (int, float)
                or type(ack_elapsed) not in (int, float)
                or (not trigger_denied and not minimum <= elapsed < 7200)
                or not 0 <= ack_elapsed <= elapsed
            ):
                raise ValueError("trusted timeout/configuration/retry result mismatch")
            trigger_id = record.get("trigger_id")
            if type(trigger_id) is not int or trigger_id in seen:
                raise ValueError("duplicate or invalid invocation identity")
            seen.add(trigger_id)
            if trigger_denied:
                continue
            if trigger_id <= 0:
                raise ValueError("missing trusted reviewer trigger identity")
            trigger = governance.request_json(repository, token, "GET", f"issues/comments/{trigger_id}")
            expected_body = trigger_body(head, receipt["claims_id"], agent, run_id, run["run_attempt"], number)
            if (
                trigger.get("body") != expected_body
                or trigger.get("created_at") != record.get("trigger_created_at")
                or (trigger.get("user") or {}).get("login") != "github-actions[bot]"
                or trigger.get("issue_url") != f"https://api.github.com/repos/{repository}/issues/{pr}"
            ):
                raise ValueError("actual trusted reviewer trigger mismatch")
            if GitHubBackend(repository, token, pr, head, receipt["claims_id"], run_id, run["run_attempt"]).responded(
                agent, trigger
            ):
                raise ValueError("higher-priority reviewer is available; exhaustion cannot be reused")
        agent_records = [
            record for record in records if isinstance(record, dict) and record.get("agent_id") == agent["id"]
        ]
        failure_class = (
            "permanent" if any(record.get("failure_class") == "permanent" for record in agent_records) else "transient"
        )
        result.append(
            {
                "agent_id": agent["id"],
                "status": "exhausted",
                "reason": "trusted collector exhausted every configured acknowledgement/review budget",
                "ack_timeout_seconds": agent["ack_timeout_seconds"],
                "review_timeout_seconds": agent["review_timeout_seconds"],
                "failure_class": failure_class,
                "attempt_count": count,
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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{40}", args.head):
        raise ValueError("exact HEAD is required")
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
    backend = GitHubBackend(repository, token, args.pr, args.head, claims_id, run_id, run_attempt)
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
        "configuration_digest": configuration_digest(pool),
        "attempts": attempts,
    }
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
