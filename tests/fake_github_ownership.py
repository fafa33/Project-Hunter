"""Stateful in-memory GitHub model for collector ownership tests.

It models only the semantics the ownership contract relies on:

* ``POST git/refs`` is create-only and atomic (422 when the ref exists);
* statuses carry the creating identity and a creation time;
* repository_dispatch creates a collector run, optionally failing ambiguously;
* Actions run records have a status the tests move through their lifecycle.

Everything runs under one lock so threads genuinely race for the same ref.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import replace
from typing import Any

import hunter_github_transport as transport
import hunter_review_orchestrator as orchestrator

REPO = "owner/repo"
TIP = "d" * 40
RECONCILE_PATH = ".github/workflows/hunter-governance-reconcile.yml"


class Crash(BaseException):
    """A process death: nothing after the raising call executes."""


class FakeGitHub:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.refs: dict[str, str] = {}
        self.tags: dict[str, dict[str, Any]] = {}
        self.statuses: list[dict[str, Any]] = []
        self.runs: dict[int, dict[str, Any]] = {}
        self.attempts: dict[tuple[int, int], dict[str, Any]] = {}
        self.dispatches: list[dict[str, Any]] = []
        self.cycle: Any = None
        self.cycle_publications = 0
        self.next_run = 5000
        self.fail: dict[str, Any] = {}
        self.calls: list[tuple[str, str]] = []
        self.ref_gate: threading.Barrier | None = None
        self.clock = 0

    # -- test controls -------------------------------------------------------
    def add_orchestrator_run(self, run_id: int, status: str = "in_progress", conclusion: str | None = None) -> None:
        self.runs[run_id] = {
            "id": run_id,
            "path": RECONCILE_PATH,
            "event": "schedule",
            "head_branch": "main",
            "head_sha": TIP,
            "status": status,
            "conclusion": conclusion,
            "created_at": "2026-10-10T10:00:00Z",
            "run_attempt": 1,
        }

    def finish(self, run_id: int, conclusion: str = "failure") -> None:
        self.runs[run_id]["status"] = "completed"
        self.runs[run_id]["conclusion"] = conclusion

    def collector_runs(self) -> list[dict[str, Any]]:
        return [r for r in self.runs.values() if r["path"] == orchestrator.COLLECTOR_WORKFLOW_PATH]

    def age_claims(self, seconds: int = 3600) -> None:
        """Make every owner claim and the stored cycle look ``seconds`` old."""
        from datetime import UTC, datetime, timedelta

        old = (datetime.now(UTC) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for tag in self.tags.values():
            record = json.loads(tag["message"])
            if record.get("kind") == "owner":
                record["claimed_at"] = old
                tag["message"] = json.dumps(record, sort_keys=True, separators=(",", ":"))
        if self.cycle is not None:
            self.cycle = replace(self.cycle, started_at=old)

    def owner_epochs(self) -> list[int]:
        return sorted(
            json.loads(self.tags[sha]["message"])["epoch"]
            for ref, sha in self.refs.items()
            if ref.startswith(orchestrator.OWNER_REF_PREFIX)
        )

    def proofs(self) -> list[dict[str, Any]]:
        return [s for s in self.statuses if s["context"].startswith(orchestrator.DISPATCH_PROOF_CONTEXT_PREFIX)]

    # -- REST surface --------------------------------------------------------
    def request_json(
        self, _repo: str, _token: str, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        with self.lock:
            self.calls.append((method, path))
            for needle, error in list(self.fail.items()):
                if needle == f"{method} {path.split('?')[0]}" or (
                    needle.endswith("*") and f"{method} {path}".startswith(needle[:-1])
                ):
                    if isinstance(error, tuple) and error[0] == "once":
                        del self.fail[needle]
                        error = error[1]
                    raise error
        if method == "POST" and path == "git/refs":
            if self.ref_gate is not None:
                self.ref_gate.wait(timeout=5)
        with self.lock:
            return self._dispatch(method, path, payload)

    def _dispatch(self, method: str, path: str, payload: dict[str, Any] | None) -> Any:
        if path == "":
            return {"default_branch": "main"}
        if path == "commits/main":
            return {"sha": TIP}
        if path.startswith("compare/"):
            return {"status": "identical"}
        if method == "POST" and path == "git/tags":
            assert payload is not None
            sha = hashlib.sha1(f"{len(self.tags)}:{payload['message']}".encode()).hexdigest()
            self.tags[sha] = {"tag": payload["tag"], "message": payload["message"]}
            return {"sha": sha}
        if method == "POST" and path == "git/refs":
            assert payload is not None
            ref = payload["ref"].removeprefix("refs/")
            if ref in self.refs:
                raise transport.GitHubRequestError("Reference already exists", category="permanent", status_code=422)
            self.refs[ref] = payload["sha"]
            return {}
        if method == "GET" and path.startswith("git/ref/"):
            ref = path.removeprefix("git/ref/")
            if ref not in self.refs:
                raise transport.GitHubRequestError("not found", category="permanent", status_code=404)
            return {"object": {"type": "tag", "sha": self.refs[ref]}}
        if method == "GET" and path.startswith("git/tags/"):
            return self.tags[path.removeprefix("git/tags/")]
        if method == "POST" and path.startswith("statuses/"):
            assert payload is not None
            self.clock += 1
            self.statuses.append(
                {
                    **payload,
                    "creator": {"login": orchestrator.TRUSTED_STATUS_CREATOR},
                    "created_at": "2026-10-10T10:00:00Z",
                }
            )
            return {}
        if method == "GET" and path.startswith("commits/") and "/statuses" in path:
            return list(self.statuses) if "&page=" not in path else []
        if method == "POST" and path == "dispatches":
            assert payload is not None
            self.dispatches.append(payload)
            self.next_run += 1
            client = payload["client_payload"]
            self.runs[self.next_run] = {
                "id": self.next_run,
                "path": orchestrator.COLLECTOR_WORKFLOW_PATH,
                "event": "repository_dispatch",
                "head_branch": "main",
                "head_sha": TIP,
                "status": "in_progress",
                "conclusion": None,
                "created_at": "2026-10-10T10:00:01Z",
                "run_attempt": 1,
                "display_title": orchestrator.collector_run_name(
                    int(client["pr_number"]), client["head_sha"], client["generation_id"]
                ),
            }
            return {}
        if method == "GET" and path.startswith("actions/workflows/"):
            return {"workflow_runs": list(reversed(self.collector_runs()))}
        if method == "GET" and "/attempts/" in path:
            parts = path.split("/")
            return self.attempts[(int(parts[-3]), int(parts[-1]))]
        if method == "GET" and path.startswith("actions/runs/"):
            run_id = int(path.rsplit("/", 1)[-1])
            if run_id not in self.runs:
                raise transport.GitHubRequestError("not found", category="permanent", status_code=404)
            return dict(self.runs[run_id])
        if method == "GET" and path.startswith("pulls/"):
            return {"state": "open", "head": {"sha": "a" * 40}}
        raise AssertionError(f"unmodelled GitHub call: {method} {path}")

    # -- cycle store (the status codec is not under test here) ---------------
    def read_cycle(self, *_args: Any) -> tuple[str, Any, None]:
        with self.lock:
            return ("present", self.cycle, None) if self.cycle is not None else ("absent", None, None)

    def publish_cycle(self, _repo: str, _token: str, _head: str, *, cycle: Any) -> None:
        with self.lock:
            self.cycle = cycle
            self.cycle_publications += 1


def install(monkeypatch: Any, fake: FakeGitHub, run_ids: threading.local | None = None) -> threading.local:
    local = run_ids or threading.local()
    monkeypatch.setattr(orchestrator, "request_json", fake.request_json)
    monkeypatch.setattr(orchestrator, "read_cycle", fake.read_cycle)
    monkeypatch.setattr(orchestrator, "publish_cycle", fake.publish_cycle)
    monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64)
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: local.run_id)
    return local
