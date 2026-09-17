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
EXTERNAL_PROMPT_LIMIT = 350_000


def external_verdict(payload: dict[str, Any]) -> str:
    """Map a provider JSON verdict to Hunter states; ambiguity blocks."""
    verdict = str(payload.get("verdict") or "").strip().lower()
    summary = str(payload.get("summary") or "").strip()
    if not summary or verdict not in {"clear", "blocking"}:
        return "blocking"
    if re.search(r"(?<!no )\bblocking (?:finding|defect|issue)s?\b", summary.lower()):
        return "blocking"
    return verdict


def configuration_digest(pool: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(pool, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Backend(Protocol):
    def head(self) -> str: ...
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...
    def trigger(self, agent: dict[str, Any], number: int) -> dict[str, Any]: ...
    def responded(self, agent: dict[str, Any], trigger: dict[str, Any]) -> bool: ...
    def response_state(self, agent: dict[str, Any], trigger: dict[str, Any]) -> str: ...


def collect_attempts(pool: dict[str, Any], head: str, backend: Backend) -> list[dict[str, Any]]:
    records = []
    agents = sorted(review.enabled_pool_reviewers(pool), key=lambda a: a["priority"])
    for agent in agents:
        count = 1 + (pool["timeout_policy"]["retries_per_agent"] if agent["retryable"] else 0)
        for number in range(1, count + 1):
            if backend.head() != head:
                raise ValueError("HEAD changed before reviewer invocation")
            trigger = backend.trigger(agent, number)
            start = backend.now()
            deadline = start + agent["timeout_seconds"]
            state = "waiting"
            while True:
                if backend.head() != head:
                    raise ValueError("HEAD changed during reviewer invocation")
                state = backend.response_state(agent, trigger)
                if state in {"clear", "blocking", "unavailable"}:
                    break
                if state != "waiting":
                    raise ValueError(f"unsupported reviewer response state: {state}")
                if backend.now() >= deadline:
                    state = "timed_out"
                    break
                backend.sleep(min(15, deadline - backend.now()))
            records.append(
                {
                    "agent_id": agent["id"],
                    "priority": agent["priority"],
                    "timeout_seconds": agent["timeout_seconds"],
                    "trigger_method": agent["trigger_method"],
                    "evidence_parser": agent["evidence_parser"],
                    "retryable": agent["retryable"],
                    "attempt_number": number,
                    "trigger_id": trigger["id"],
                    "trigger_created_at": trigger["created_at"],
                    "elapsed_seconds": backend.now() - start,
                    "outcome": state,
                    **({k: trigger[k] for k in ("provider", "response_digest", "head_sha") if k in trigger}),
                }
            )
            if state in {"clear", "blocking"}:
                return records
            if state == "unavailable":
                break
    return records


def valid_run(run: dict[str, Any], run_id: int, branch: str, revision: str) -> bool:
    return (
        run.get("id") == run_id
        and type(run.get("run_attempt")) is int
        and run["run_attempt"] >= 1
        and run.get("head_branch") == branch
        and run.get("head_sha") == revision
        and run.get("path") == WORKFLOW
        and run.get("event") in {"workflow_dispatch", "pull_request_target"}
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

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def _candidate_diff(self) -> str:
        url = f"https://api.github.com/repos/{self.repository}/pulls/{self.pr}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github.v3.diff"}
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read(EXTERNAL_PROMPT_LIMIT + 1)
        if len(data) > EXTERNAL_PROMPT_LIMIT:
            raise ValueError("candidate diff exceeds external reviewer context budget")
        return data.decode("utf-8", errors="strict")

    def _invoke_external(self, agent: dict[str, Any], number: int) -> dict[str, Any]:
        provider = str(agent["trigger_method"]).split(":", 1)[1]
        secret_name = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY"}.get(provider)
        key = os.environ.get(secret_name or "", "")
        if not key:
            return {"verdict": "unavailable", "summary": f"{provider} API key unavailable"}
        prompt = (
            "You are an independent hostile code reviewer. Review the COMPLETE exact-head diff below. "
            f"Repository={self.repository} PR={self.pr} HEAD={self.expected_head} claims_id={self.claims_id}. "
            'Return JSON only: {"verdict":"clear|blocking","summary":"..."}. '
            "Use clear only when no substantive correctness, security, governance, exact-head, or fail-closed defect remains. "
            "Any finding must use blocking.\n\nDIFF:\n" + self._candidate_diff()
        )
        if provider == "gemini":
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
            headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
            body: dict[str, Any] = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            }
        elif provider == "groq":
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            body = {
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            }
        else:
            raise ValueError(f"unsupported external reviewer: {provider}")
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=int(agent["timeout_seconds"])) as response:
                raw = response.read(LIMIT + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {408, 413, 429, 500, 502, 503, 504}:
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

    def trigger(self, agent: dict[str, Any], number: int) -> dict[str, Any]:
        method = str(agent["trigger_method"])
        if method.startswith("api:"):
            provider = method.split(":", 1)[1]
            payload = self._invoke_external(agent, number)
            state = "unavailable" if payload.get("verdict") == "unavailable" else external_verdict(payload)
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            identity = int(
                hashlib.sha256(
                    f"{self.run_id}:{self.run_attempt}:{provider}:{number}:{self.expected_head}".encode()
                ).hexdigest()[:15],
                16,
            )
            return {
                "id": identity,
                "created_at": str(time.time()),
                "provider": provider,
                "response_digest": digest,
                "head_sha": self.expected_head,
                "state": state,
            }
        body = trigger_body(self.expected_head, self.claims_id, agent, self.run_id, self.run_attempt, number)
        result = governance.request_json(
            self.repository, self.token, "POST", f"issues/{self.pr}/comments", {"body": body}
        )
        if not isinstance(result, dict) or type(result.get("id")) is not int or not result.get("created_at"):
            raise ValueError("GitHub did not confirm actual reviewer invocation")
        return result

    @staticmethod
    def _native_clear(body: str, head: str) -> bool:
        raw = body.strip()
        match = re.match(
            r"Codex Review(?:\s*:\s*|\s+)(?:\n+)?Didn't find any major issues\.[^\n]*\n+"
            r"\*\*Reviewed commit:\*\*\s*`([0-9a-f]{7,40})`(?:\s*<details>[\s\S]*?</details>)?\s*$",
            raw,
        )
        return bool(match and head.lower().startswith(match.group(1).lower()))

    @staticmethod
    def _unavailable(body: str) -> bool:
        text = body.lower()
        markers = (
            "usage limit reached",
            "temporarily unavailable",
            "create a codex account and connect to github",
        )
        return any(marker in text for marker in markers)

    def response_state(self, agent: dict[str, Any], trigger: dict[str, Any]) -> str:
        if str(agent["trigger_method"]).startswith("api:"):
            if trigger.get("head_sha") != self.expected_head:
                raise ValueError("external reviewer response is not exact-head bound")
            return str(trigger.get("state") or "blocking")
        login = governance.reviewer_login(agent)
        created = str(trigger["created_at"])
        for item in _pages(self.repository, self.token, f"pulls/{self.pr}/reviews"):
            if (item.get("user") or {}).get("login", "").lower() != login:
                continue
            if str(item.get("submitted_at") or "") < created or item.get("commit_id") != self.expected_head:
                continue
            state = str(item.get("state") or "").upper()
            body = str(item.get("body") or "")
            if state == "CHANGES_REQUESTED":
                return "blocking"
            if state == "APPROVED":
                return "clear"
            if state == "COMMENTED" and governance._substantive_review_body(body):
                if agent.get("id") == "codex" and self._native_clear(body, self.expected_head):
                    return "clear"
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
                and ack.get("collector_run_id") == self.run_id
            ):
                return "clear"
            if agent.get("id") == "codex" and self._native_clear(body, self.expected_head):
                return "clear"
            if self._unavailable(body):
                return "unavailable"
        return "waiting"

    def responded(self, agent: dict[str, Any], trigger: dict[str, Any]) -> bool:
        return self.response_state(agent, trigger) in {"clear", "blocking"}


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
    agents = review.enabled_pool_reviewers(pool)
    own = next((a["priority"] for a in agents if a["id"] == authority_type), float("inf"))
    required = sorted((a for a in agents if a["priority"] < own), key=lambda a: a["priority"])
    records = receipt.get("attempts")
    if not isinstance(records, list):
        raise ValueError("collector attempts are missing")
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
                k: agent[k] for k in ("priority", "timeout_seconds", "trigger_method", "evidence_parser", "retryable")
            }
            expected_attempt.update(agent_id=agent["id"], attempt_number=number)
            elapsed = record.get("elapsed_seconds")
            outcome = record.get("outcome")
            elapsed_valid = (
                type(elapsed) in (int, float)
                and 0 <= elapsed < 7200
                and (outcome == "unavailable" or elapsed >= agent["timeout_seconds"])
            )
            if (
                any(record.get(k) != v for k, v in expected_attempt.items())
                or outcome not in {"timed_out", "unavailable"}
                or type(record.get("priority")) is not int
                or type(record.get("timeout_seconds")) is not int
                or type(record.get("retryable")) is not bool
                or type(record.get("attempt_number")) is not int
                or not elapsed_valid
            ):
                raise ValueError("trusted timeout/configuration/retry result mismatch")
            trigger_id = record.get("trigger_id")
            if type(trigger_id) is not int or trigger_id in seen:
                raise ValueError("duplicate or invalid invocation identity")
            seen.add(trigger_id)
            try:
                trigger = governance.request_json(repository, token, "GET", f"issues/comments/{trigger_id}")
            except Exception as exc:
                raise ValueError("trusted reviewer trigger evidence is unavailable") from exc
            expected_body = trigger_body(head, receipt["claims_id"], agent, run_id, run["run_attempt"], number)
            if (
                trigger.get("body") != expected_body
                or trigger.get("created_at") != record.get("trigger_created_at")
                or (trigger.get("user") or {}).get("login") != "github-actions[bot]"
                or trigger.get("issue_url") != f"https://api.github.com/repos/{repository}/issues/{pr}"
            ):
                raise ValueError("actual trusted reviewer trigger mismatch")
            live_state = GitHubBackend(
                repository, token, pr, head, receipt["claims_id"], run_id, run["run_attempt"]
            ).response_state(agent, trigger)
            if live_state in {"clear", "blocking"}:
                raise ValueError("higher-priority reviewer is available; exhaustion cannot be reused")
            if outcome == "unavailable" and live_state != "unavailable":
                raise ValueError("recorded reviewer unavailability is no longer verifiable")
            if outcome == "timed_out" and live_state == "unavailable":
                raise ValueError("collector timeout receipt disagrees with explicit reviewer unavailability")
        terminal = records[offset - 1].get("outcome") if count else "timed_out"
        result.append(
            {
                "agent_id": agent["id"],
                "status": "exhausted",
                "reason": (
                    "trusted reviewer reported explicit unavailability"
                    if terminal == "unavailable"
                    else "trusted collector timed out every configured invocation"
                ),
                "timeout_seconds": agent["timeout_seconds"],
                "failure_class": "permanent" if terminal == "unavailable" else "transient",
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
