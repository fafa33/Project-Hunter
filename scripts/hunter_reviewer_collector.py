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
                    "collector_run_id": int(trigger.get("collector_run_id") or getattr(backend, "run_id", 0)),
                    "collector_run_attempt": int(
                        trigger.get("collector_run_attempt") or getattr(backend, "run_attempt", 0)
                    ),
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


def invocation_key(head: str, claims_id: str, agent_id: str, number: int) -> str:
    return hashlib.sha256(f"{head}:{claims_id}:{agent_id}:{number}".encode()).hexdigest()


def api_trigger_payload(
    head: str, claims_id: str, agent: dict[str, Any], run_id: int, run_attempt: int, number: int
) -> dict[str, Any]:
    if not str(agent["trigger_method"]).startswith("api:"):
        raise ValueError("reviewer has no supported API trigger")
    return {
        "schema": "hunter.reviewer-trigger.v1",
        "head_sha": head,
        "claims_id": claims_id,
        "reviewer_agent": str(agent["id"]),
        "collector_run_id": run_id,
        "collector_run_attempt": run_attempt,
        "attempt_number": number,
        "invocation_key": invocation_key(head, claims_id, str(agent["id"]), number),
    }


def api_trigger_body(
    head: str, claims_id: str, agent: dict[str, Any], run_id: int, run_attempt: int, number: int
) -> str:
    payload = api_trigger_payload(head, claims_id, agent, run_id, run_attempt, number)
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
    if set(value) != allowed:
        return None
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
        value["head_sha"], value["claims_id"], reviewer_agent, value["attempt_number"]
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
        + f'\nInvocation key: {invocation_key(head, claims_id, str(agent["id"]), number)}.'
    )


def parse_native_trigger(body: str) -> dict[str, Any] | None:
    """Parse Hunter's canonical authenticated GitHub reviewer trigger."""
    match = re.search(
        r"Review exact HEAD ([0-9a-f]{40}).*?\"claims_id\": \"([0-9a-f]{64})\".*?"
        r"Collector invocation: (\d+)/(\d+)/([a-z0-9_-]+)/(\d+)\.\n"
        r"Invocation key: ([0-9a-f]{64})\.",
        body,
        re.S,
    )
    if match is None:
        return None
    head, claims_id, run_id, run_attempt, agent_id, attempt, key = match.groups()
    number = int(attempt)
    if key != invocation_key(head, claims_id, agent_id, number):
        return None
    return {
        "head_sha": head,
        "claims_id": claims_id,
        "reviewer_agent": agent_id,
        "collector_run_id": int(run_id),
        "collector_run_attempt": int(run_attempt),
        "attempt_number": number,
    }


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

    def _existing_trigger(self, agent: dict[str, Any], number: int) -> dict[str, Any] | None:
        marker = f"Invocation key: {invocation_key(self.expected_head, self.claims_id, str(agent['id']), number)}."
        matches = [
            item
            for item in _pages(self.repository, self.token, f"issues/{self.pr}/comments")
            if (item.get("user") or {}).get("login", "").lower() == "github-actions[bot]"
            and marker in str(item.get("body") or "")
        ]
        return min(matches, key=lambda x: int(x.get("id") or 0), default=None)

    def trigger(self, agent: dict[str, Any], number: int) -> dict[str, Any]:
        method = str(agent["trigger_method"])
        existing = self._existing_trigger(agent, number)
        if existing is not None:
            m = re.search(r"Collector invocation: (\d+)/(\d+)/", str(existing.get("body") or ""))
            existing["collector_run_id"] = int(m.group(1)) if m else self.run_id
            existing["collector_run_attempt"] = int(m.group(2)) if m else self.run_attempt
            if method.startswith("api:"):
                result = self._api_result(agent, existing)
                if result is None:
                    raise ValueError("persisted API invocation has no verifiable result")
                existing.update(
                    provider=method.split(":", 1)[1],
                    response_digest=result.get("response_digest"),
                    head_sha=self.expected_head,
                    state=result.get("verdict"),
                    result_comment_id=result.get("comment_id"),
                )
            return existing
        if method.startswith("api:"):
            provider = method.split(":", 1)[1]
            trigger = self._post_comment(
                api_trigger_body(self.expected_head, self.claims_id, agent, self.run_id, self.run_attempt, number)
            )
            payload = self._invoke_external(agent, number)
            state = "unavailable" if payload.get("verdict") == "unavailable" else external_verdict(payload)
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            result_comment = self._post_comment(
                api_result_body(
                    self.expected_head,
                    self.claims_id,
                    agent,
                    self.run_id,
                    int(trigger["id"]),
                    state,
                    str(payload.get("summary") or ""),
                    digest,
                )
            )
            if state == "clear":
                self._post_comment(
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
                    )
                )
            return {
                **trigger,
                "provider": provider,
                "response_digest": digest,
                "head_sha": self.expected_head,
                "state": state,
                "result_comment_id": result_comment["id"],
                "collector_run_id": self.run_id,
            }
        body = trigger_body(self.expected_head, self.claims_id, agent, self.run_id, self.run_attempt, number)
        result = self._post_comment(body)
        result["collector_run_id"] = self.run_id
        return result

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
            if str(agent["trigger_method"]).startswith("api:"):
                parsed_trigger = parse_api_trigger(str(trigger.get("body") or ""))
                expected_trigger = api_trigger_payload(
                    head,
                    receipt["claims_id"],
                    agent,
                    int(record.get("collector_run_id") or run_id),
                    int(record.get("collector_run_attempt") or run["run_attempt"]),
                    number,
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
                )._api_result(agent, trigger)
                if api_result is None:
                    raise ValueError("trusted API reviewer result evidence is unavailable")
                if api_result["response_digest"] != record.get("response_digest"):
                    raise ValueError("trusted API reviewer result digest mismatch")
                if record.get("provider") != str(agent["trigger_method"]).split(":", 1)[1]:
                    raise ValueError("trusted API reviewer provider mismatch")
                if record.get("head_sha") != head:
                    raise ValueError("trusted API reviewer head binding mismatch")
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
