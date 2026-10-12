"""A small in-memory GitHub for the native collector/publisher model.

It serves exactly the endpoints the native flow reads (repository, pull request, workflow run,
default-branch ancestry, commit statuses, issue comments, reviews, run artifacts) and records the
statuses a publisher writes. It models no ownership protocol: there is none to model.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from typing import Any

import hunter_github_transport as transport
import hunter_governance_review_v2 as governance
import hunter_review_orchestrator as orchestration
import hunter_reviewer_collector as collector

REPOSITORY = "owner/repo"
MAIN_SHA = "9" * 40
HEAD = "a" * 40
CLAIMS = "c" * 64
DIGEST = "d" * 64
PR = 472
COLLECTOR_RUN = 1001
PUBLISHER_RUN = 2002
COLLECTOR_PATH = ".github/workflows/hunter-reviewer-collector.yml"
PUBLISHER_PATH = ".github/workflows/hunter-collector-publisher.yml"


def receipt_for(*, pr: int = PR, head: str = HEAD, run_id: int = COLLECTOR_RUN, attempt: int = 1, **overrides: Any):
    receipt: dict[str, Any] = {
        "schema": orchestration.COLLECTOR_RESULTS_SCHEMA,
        "repository": REPOSITORY,
        "pr_number": pr,
        "head_sha": head,
        "run_id": run_id,
        "run_attempt": attempt,
        "claims_id": CLAIMS,
        "remediation_generation_id": orchestration.BASE_GENERATION_ID,
        "configuration_digest": DIGEST,
        "attempts": [],
    }
    receipt.update(overrides)
    return receipt


class NativeFake:
    def __init__(self) -> None:
        self.default_branch = "main"
        self.compare_status = "identical"
        self.prs: dict[int, dict[str, Any]] = {
            PR: {"state": "open", "draft": False, "base": {"ref": "main"}, "head": {"sha": HEAD}}
        }
        self.runs: dict[int, dict[str, Any]] = {}
        self.statuses: dict[str, list[dict[str, Any]]] = {}
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self.reviews: dict[int, list[dict[str, Any]]] = {}
        self.artifacts: dict[int, list[dict[str, Any]]] = {}
        self.archives: dict[int, bytes] = {}
        self.posted: list[tuple[str, dict[str, Any]]] = []
        self.unavailable: set[str] = set()
        self.add_run(COLLECTOR_RUN, path=COLLECTOR_PATH, event="pull_request_target", conclusion="success")
        self.add_run(PUBLISHER_RUN, path=PUBLISHER_PATH, event="workflow_run", conclusion="success")

    # -- fixtures ---------------------------------------------------------------------------
    def add_run(self, run_id: int, *, path: str, event: str, conclusion: str | None, attempt: int = 1, **extra: Any):
        self.runs[run_id] = {
            "id": run_id,
            "run_attempt": attempt,
            "path": path,
            "event": event,
            "head_branch": self.default_branch,
            "head_sha": MAIN_SHA,
            "status": "completed" if conclusion else "in_progress",
            "conclusion": conclusion,
            **extra,
        }

    def add_receipt(self, receipt: dict[str, Any], *, run_id: int = COLLECTOR_RUN, tamper_digest: bool = False):
        attempt = self.runs[run_id]["run_attempt"]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("reviewer-results.json", json.dumps(receipt))
        archive = buffer.getvalue()
        artifact_id = run_id * 10 + len(self.artifacts.get(run_id, []))
        digest = "sha256:" + hashlib.sha256(archive + (b"x" if tamper_digest else b"")).hexdigest()
        self.artifacts.setdefault(run_id, []).append(
            {
                "id": artifact_id,
                "name": f"hunter-reviewer-results-{receipt['head_sha']}-{attempt}",
                "expired": False,
                "digest": digest,
                "workflow_run": {"id": run_id},
            }
        )
        self.archives[artifact_id] = archive

    def add_cycle_status(self, *, state: str, run_id: int = PUBLISHER_RUN, generation: str = "", head: str = HEAD):
        self.statuses.setdefault(head, []).insert(
            0,
            {
                "creator": {"login": "github-actions[bot]"},
                "context": f"{orchestration.CONTEXT_PREFIX}{PR}",
                "description": f"{state}|publisher|{run_id}|{orchestration.reviewer_pool_config_digest()}|{generation}",
                "target_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
                "created_at": "2026-10-11T00:00:00Z",
                "state": "success",
            },
        )

    # -- transport --------------------------------------------------------------------------
    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        bare = path.split("?", 1)[0]
        if bare in self.unavailable:
            raise transport.GitHubRequestError(f"{bare} unavailable", category="transient", status_code=502)
        if bare == "":
            return {"default_branch": self.default_branch}
        if bare.startswith("pulls/") and bare.count("/") == 1:
            pr = self.prs.get(int(bare.split("/")[1]))
            if pr is None:
                raise transport.GitHubRequestError("not found", category="permanent", status_code=404)
            return pr
        if bare.startswith("pulls/") and bare.endswith("/reviews"):
            return self._page(self.reviews.get(int(bare.split("/")[1]), []), path)
        if bare.startswith("issues/") and bare.endswith("/comments"):
            return self._page(self.comments.get(int(bare.split("/")[1]), []), path)
        if bare.startswith("actions/runs/") and bare.endswith("/artifacts"):
            run_id = int(bare.split("/")[2])
            return {"artifacts": self._page(self.artifacts.get(run_id, []), path)}
        if bare.startswith("actions/runs/"):
            run = self.runs.get(int(bare.split("/")[2]))
            if run is None:
                raise transport.GitHubRequestError("not found", category="permanent", status_code=404)
            return run
        if bare == f"commits/{self.default_branch}":
            return {"sha": MAIN_SHA}
        if bare.startswith("compare/"):
            return {"status": self.compare_status}
        if bare.startswith("commits/") and bare.endswith("/statuses"):
            return self._page(self.statuses.get(bare.split("/")[1], []), path)
        if bare.startswith("statuses/") and method == "POST":
            sha = bare.split("/")[1]
            assert payload is not None
            self.posted.append((sha, payload))
            self.statuses.setdefault(sha, []).insert(
                0, {"creator": {"login": "github-actions[bot]"}, "created_at": "2026-10-11T01:00:00Z", **payload}
            )
            return {"id": len(self.posted)}
        if bare == "actions/runners":
            return {"runners": []}
        raise AssertionError(f"unexpected request {method} {path}")

    @staticmethod
    def _page(items: list[Any], path: str) -> list[Any]:
        match = re.search(r"[?&]page=(\d+)", path)
        page = int(match.group(1)) if match else 1
        return list(items) if page == 1 else []

    def install(self, monkeypatch) -> NativeFake:
        def repo_request(_repository: str, _token: str, method: str, path: str, payload: Any = None) -> Any:
            return self.request(method, path, payload)

        monkeypatch.setattr(orchestration, "request_json", repo_request)
        monkeypatch.setattr(governance, "request_json", repo_request)
        monkeypatch.setattr(
            collector,
            "download_artifact",
            lambda _repository, _token, artifact_id: self.archives[artifact_id],
        )
        monkeypatch.setattr(orchestration, "reviewer_pool_config_digest", lambda: DIGEST)
        monkeypatch.setattr(orchestration, "blocking_reviewer_threads", lambda *_a, **_k: ())
        monkeypatch.setenv("GITHUB_RUN_ID", str(PUBLISHER_RUN))
        return self

    def allow_review_request(self, monkeypatch, *, ready: bool = True, state: str = "success", reason: str = ""):
        monkeypatch.setattr(
            orchestration,
            "review_request_state",
            lambda *_a: orchestration.ReviewRequestReadiness(ready, CLAIMS if ready else "", reason, state),
        )
        monkeypatch.setattr(orchestration, "exact_head_codex_clear_exists", lambda *_a: False)
