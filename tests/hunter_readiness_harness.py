"""Shared in-memory GitHub substitute for Hunter Merge Readiness tests.

The harness models GitHub as *state*, never as a sequence of controller calls,
which is what makes the convergence tests non-vacuous: a test mutates repository
state and drives a trigger, and the controller has to rediscover everything by
reading. Nothing in here mirrors the controller's internal call order, so a test
cannot pass merely because a mock was taught the implementation's steps.

Governance evidence is stamped by :meth:`FakeGitHub.publish_governance` using the
production ``hunter_governance_revision`` module against the harness's own view
of pull-request state. A test therefore cannot fabricate matching evidence by
hand: if the controller and the evaluator disagree about what the governance
revision covers, the stamp will not match and readiness will stay pending.
"""

from __future__ import annotations

import json
from typing import Any

import hunter_merge_readiness as core
from hunter_governance_revision import GovernanceInputs, governance_revision, render_marker

GOVERNANCE_CONTEXT = "Hunter Governance Review"
READINESS_CONTEXT = "Hunter Merge Readiness"

READY_BODY = (
    "## Summary\n\nReal governance evidence package for the change under test.\n\n"
    "| Acceptance criterion | Status |\n"
    "|---|---|\n"
    "| Reconciler converges from current state | PASS |\n\n"
    "- [x] `READY FOR REVIEW`\n"
)


class FakeGitHub:
    """An in-memory repository the controller can read and publish against."""

    def __init__(self) -> None:
        self.pulls: dict[int, dict[str, Any]] = {}
        self.base_oids: dict[int, str] = {}
        self.files: dict[int, list[str]] = {}
        self.statuses: dict[str, list[dict[str, Any]]] = {}
        self.check_runs: dict[str, list[dict[str, Any]]] = {}
        self.threads: dict[int, list[dict[str, Any]]] = {}
        self.reviews: dict[int, list[dict[str, Any]]] = {}
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self.reactions: dict[int, list[dict[str, Any]]] = {}
        self.published: list[tuple[str, str, str]] = []
        self.status_writes = 0
        self._next_status_id = 1000
        # Fixed, deliberately meaningless clock. Nothing in the architecture may
        # depend on it; tests that vary it must observe no behavioural change.
        self.clock = "2026-08-13T00:00:00Z"

    # -- state construction --------------------------------------------------

    def add_pull_request(
        self,
        number: int,
        *,
        head_sha: str,
        base_sha: str = "base_main_0",
        base_ref: str = "main",
        title: str = "fix: converge merge readiness from current state",
        body: str = READY_BODY,
        draft: bool = False,
        author: str = "fafa33",
        state: str = "open",
        mergeable: object = True,
        files: list[str] | None = None,
    ) -> None:
        self.pulls[number] = {
            "number": number,
            "state": state,
            "draft": draft,
            "title": title,
            "body": body,
            "head": {"sha": head_sha},
            "base": {"ref": base_ref, "sha": base_sha},
            "user": {"login": author},
            "mergeable": mergeable,
            "created_at": self.clock,
            "updated_at": self.clock,
        }
        self.base_oids[number] = base_sha
        self.files[number] = list(files if files is not None else ["src/hunter/example.py"])
        self.threads.setdefault(number, [])
        self.reviews.setdefault(number, [])
        self.comments.setdefault(number, [])

    def head_sha(self, number: int) -> str:
        return str((self.pulls[number].get("head") or {}).get("sha") or "")

    def green_required_checks(self, sha: str) -> None:
        self.check_runs[sha] = [
            {"id": 1, "name": "Quality Gates", "status": "completed", "conclusion": "success"},
            {"id": 2, "name": "dependency-review", "status": "completed", "conclusion": "success"},
            {"id": 3, "name": "CodeQL", "status": "completed", "conclusion": "success"},
        ]

    def set_check(self, sha: str, name: str, *, status: str, conclusion: str | None = None) -> None:
        runs = self.check_runs.setdefault(sha, [])
        runs.append(
            {
                "id": max((run["id"] for run in runs), default=0) + 1,
                "name": name,
                "status": status,
                "conclusion": conclusion,
            }
        )

    def governance_revision_for(self, number: int) -> str:
        pr = self.pulls[number]
        return governance_revision(
            GovernanceInputs(
                pull_request_number=number,
                head_sha=self.head_sha(number),
                base_sha=self.base_oids[number],
                base_ref=str((pr.get("base") or {}).get("ref") or ""),
                title=str(pr.get("title") or ""),
                body=str(pr.get("body") or ""),
                draft=bool(pr.get("draft")),
                conflicting=pr.get("mergeable") is False,
                changed_paths=tuple(self.files.get(number, ())),
            )
        )

    def publish_governance(
        self,
        number: int,
        state: str = "success",
        *,
        sha: str | None = None,
        revision: str | None = None,
        marked_pull_request: int | None = None,
        marker: bool = True,
    ) -> str:
        """Publish a Governance Review status exactly as the real gate would.

        ``revision`` / ``marked_pull_request`` / ``marker`` exist only so a test
        can model *superseded*, *foreign*, or *legacy unstamped* evidence.
        """
        target = sha or self.head_sha(number)
        used_revision = revision or self.governance_revision_for(number)
        prose = f"Approved for head {target[:7]}: deterministic governance validation passed."
        if marker:
            description = render_marker(marked_pull_request or number, used_revision) + " " + prose
        else:
            description = prose
        self._append_status(target, GOVERNANCE_CONTEXT, state, description)
        return used_revision

    def add_comment(self, number: int, comment_id: int, *, login: str = "reviewer", body: str = "please look") -> None:
        self.comments.setdefault(number, []).append(
            {
                "id": comment_id,
                "user": {"login": login},
                "body": body,
                "created_at": self.clock,
                "updated_at": self.clock,
            }
        )

    def acknowledge(self, comment_id: int, *, owner: str = "fafa33", at: str | None = None) -> None:
        self.reactions.setdefault(comment_id, []).append(
            {"user": {"login": owner}, "content": "+1", "created_at": at or self.clock}
        )

    def add_unresolved_thread(self, number: int, thread_id: str) -> None:
        self.threads.setdefault(number, []).append({"id": thread_id, "isResolved": False})

    def resolve_thread(self, number: int, thread_id: str) -> None:
        for thread in self.threads.get(number, []):
            if thread["id"] == thread_id:
                thread["isResolved"] = True

    def request_changes(self, number: int, login: str, review_id: int = 1) -> None:
        self.reviews.setdefault(number, []).append(
            {"id": review_id, "user": {"login": login}, "state": "CHANGES_REQUESTED"}
        )

    def readiness_status(self, sha: str) -> tuple[str, str] | None:
        matches = [s for s in self.statuses.get(sha, []) if s["context"] == READINESS_CONTEXT]
        if not matches:
            return None
        latest = max(matches, key=lambda s: s["id"])
        return latest["state"], latest["description"]

    def seed_readiness(self, sha: str, state: str, description: str) -> None:
        """Plant a status from a historical controller generation."""
        self._append_status(sha, READINESS_CONTEXT, state, description)

    def _append_status(self, sha: str, status_context: str, state: str, description: str) -> None:
        self._next_status_id += 1
        self.statuses.setdefault(sha, []).append(
            {
                "id": self._next_status_id,
                "context": status_context,
                "state": state,
                "description": description,
                "created_at": self.clock,
            }
        )

    # -- GitHub API substitute ----------------------------------------------

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        clean, _, query = path.partition("?")
        params = dict(item.split("=", 1) for item in query.split("&") if "=" in item)
        page = int(params.get("page", "1"))

        if method == "POST" and clean.startswith("statuses/"):
            sha = clean.split("/", 1)[1]
            assert payload is not None
            self.status_writes += 1
            self.published.append((sha, payload["state"], payload["description"]))
            self._append_status(sha, payload["context"], payload["state"], payload["description"])
            return {}

        if method != "GET":
            return {}

        parts = clean.split("/")

        if clean == "pulls":
            if page > 1:
                return []
            return [pr for pr in self.pulls.values() if pr.get("state") == "open"]

        if parts[0] == "pulls" and len(parts) == 2:
            return self.pulls.get(int(parts[1]), {})
        if parts[0] == "pulls" and len(parts) == 3:
            if page > 1:
                return []
            number = int(parts[1])
            if parts[2] == "files":
                return [{"filename": name} for name in self.files.get(number, [])]
            if parts[2] == "reviews":
                return list(self.reviews.get(number, []))
            if parts[2] == "comments":
                return []

        if parts[0] == "issues" and len(parts) == 3 and parts[2] == "comments":
            if page > 1:
                return []
            return list(self.comments.get(int(parts[1]), []))
        if parts[0] == "issues" and len(parts) == 4 and parts[1] == "comments" and parts[3] == "reactions":
            if page > 1:
                return []
            return list(self.reactions.get(int(parts[2]), []))

        if parts[0] == "commits" and len(parts) == 3:
            sha = parts[1]
            if parts[2] == "statuses":
                if page > 1:
                    return []
                return list(self.statuses.get(sha, []))
            if parts[2] == "check-runs":
                if page > 1:
                    return {"check_runs": []}
                return {"check_runs": list(self.check_runs.get(sha, []))}

        raise AssertionError(f"unexpected GitHub call: {method} {path}")

    def graphql_json(self, query: str, variables: dict[str, Any]) -> Any:
        number = int(variables["number"])
        if "reviewThreads" in query:
            nodes = [dict(thread) for thread in self.threads.get(number, [])]
            return {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": nodes,
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        return {
            "repository": {
                "pullRequest": {
                    "baseRefOid": self.base_oids.get(number, ""),
                    "headRefOid": self.head_sha(number),
                }
            }
        }


def install(monkeypatch, gh: FakeGitHub, *, event: str = "schedule") -> None:
    """Point the controller at ``gh`` and configure its environment."""
    monkeypatch.setattr(core, "request_json", gh.request_json)
    monkeypatch.setattr(core, "graphql_json", gh.graphql_json)
    core.repo = "fafa33/Project-Hunter"
    core.repo_owner = "fafa33"
    core.token = "test-token"
    core.event_name = event
    core.run_url = "https://github.com/fafa33/Project-Hunter/actions/runs/1"


def drive(monkeypatch, tmp_path, gh: FakeGitHub, event_name: str, event: dict[str, Any]) -> None:
    """Run the controller end to end through its real trigger adapters.

    Goes through ``core.main()`` and ``GITHUB_EVENT_PATH`` rather than calling
    ``reconcile_pr`` directly, so the tests exercise the actual adapter that a
    given GitHub event would use.
    """
    event_path = tmp_path / f"event-{event_name}-{len(gh.published)}.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GH_REPO", "fafa33/Project-Hunter")
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("EVENT_NAME", event_name)
    monkeypatch.setenv("RUN_URL", "https://github.com/fafa33/Project-Hunter/actions/runs/1")
    monkeypatch.setattr(core, "request_json", gh.request_json)
    monkeypatch.setattr(core, "graphql_json", gh.graphql_json)
    core.main()


def ready_pull_request(gh: FakeGitHub, number: int = 501, head: str = "head_a" * 6) -> str:
    """A pull request whose current state should evaluate to success."""
    gh.add_pull_request(number, head_sha=head)
    gh.green_required_checks(head)
    gh.publish_governance(number)
    return head
