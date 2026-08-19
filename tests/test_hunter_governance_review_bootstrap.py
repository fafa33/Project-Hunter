"""Regression tests for the legacy governance transport boundary.

The old workflow-bootstrap wiring was retired by Issue #282. These tests keep
only the still-meaningful transport guarantees: transient GitHub failures are
retried, permanent failures are not, and status publication never fabricates a
semantic verdict when the transport is unavailable.
"""

from __future__ import annotations

import argparse
import json
from subprocess import CompletedProcess

import hunter_governance_review.github_api as github_api_module
import pytest
from hunter_governance_review.__main__ import run_review
from hunter_governance_review.github_api import GhCliRunner, GitHubUnavailable
from test_hunter_governance_review import DEFAULT_MAP_TEXT, GOOD_BODY

HEAD = "a" * 40
BASE = "b" * 40
REPOSITORY = "fafa33/Project-Hunter"

_HIERARCHY_DOCS = {
    "docs/CANONICAL_ARCHITECTURE_MAP.md": DEFAULT_MAP_TEXT,
    "docs/PROJECT_CONSTITUTION.md": "constitution text",
    "docs/PROJECT_PRINCIPLES.md": "principles text",
    "docs/HUNTER_ARCHITECTURE_MANIFEST.md": "manifest text",
    "docs/DEVELOPMENT_GOVERNANCE.md": "development governance text",
    "docs/AI_REVIEW_PROTOCOL.md": "ai review protocol text",
}

_HTTP_503_DIAGNOSTIC = (
    "gh: No server is currently available to service your request. Sorry about that. "
    "Please try resubmitting your request and contact us if the problem persists. (HTTP 503)"
)

_PR_JSON = {
    "number": 277,
    "title": "feat: canonical mispricing orchestration",
    "body": GOOD_BODY,
    "state": "open",
    "isDraft": False,
    "headRefName": "governance/issue-276-agent-preflight",
    "headRefOid": HEAD,
    "baseRefName": "main",
    "baseRefOid": BASE,
    "mergeable": "MERGEABLE",
    "changedFiles": 1,
    "url": f"https://github.com/{REPOSITORY}/pull/277",
    "author": {"login": "fafa33"},
}


class GhSubprocessRouter:
    def __init__(self, *, statuses: str = "ok", pr_view: str = "ok") -> None:
        self.statuses_behaviour = statuses
        self.pr_view_behaviour = pr_view
        self.status_calls = 0
        self.statuses_published: list[str] = []
        self.pr_views = 0

    def __call__(self, args: list[str], **kwargs: object) -> CompletedProcess:
        if args[1] == "pr":
            self.pr_views += 1
            if self.pr_view_behaviour == "fail_once" and self.pr_views == 1:
                return self._failure(args, _HTTP_503_DIAGNOSTIC)
            return CompletedProcess(args, 0, stdout=json.dumps(_PR_JSON), stderr="")
        if args[1] != "api":
            raise AssertionError(f"unexpected gh command: {args}")
        if "-H" in args:
            return self._contents(args)
        if "--jq" in args:
            if ".[].name" in args[-1]:
                return CompletedProcess(args, 0, stdout="", stderr="")
            return CompletedProcess(args, 0, stdout="src/hunter/example.py\tmodified\t1\t0\n", stderr="")
        self.status_calls += 1
        state = _status_state(args)
        if self.statuses_behaviour == "fail_once" and self.status_calls == 1:
            return self._failure(args, _HTTP_503_DIAGNOSTIC)
        if self.statuses_behaviour == "always_503":
            return self._failure(args, _HTTP_503_DIAGNOSTIC)
        if self.statuses_behaviour == "always_422":
            return self._failure(args, "HTTP 422: Validation Failed")
        self.statuses_published.append(state)
        return CompletedProcess(args, 0, stdout="{}", stderr="")

    def _contents(self, args: list[str]) -> CompletedProcess:
        target = next(part for part in args if "contents/" in part and "?ref=" in part)
        path, _, ref = target.partition("?ref=")
        path = path.split("contents/", 1)[1]
        content = _HIERARCHY_DOCS.get(path) if ref == BASE else None
        if content is None:
            return self._failure(args, "HTTP 404: Not Found")
        return CompletedProcess(args, 0, stdout=content, stderr="")

    @staticmethod
    def _failure(args: list[str], stderr: str) -> CompletedProcess:
        return CompletedProcess(args, 1, stdout="", stderr=stderr)


def _status_state(args: list[str]) -> str:
    pairs = args[args.index("-f") :]
    return next(v.split("=", 1)[1] for v in pairs if v.startswith("state="))


def _args() -> argparse.Namespace:
    return argparse.Namespace(pr=277, repository=None, root=None, protected_branches=None, dry_run=False)


def _env(**overrides: object) -> dict[str, str]:
    env: dict[str, str] = {
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_TOKEN": "token",
        "GITHUB_RUN_ID": "123",
        "GITHUB_SERVER_URL": "https://github.com",
    }
    for key, value in overrides.items():
        env[key] = str(value)
    return env


def _runner(router: GhSubprocessRouter, monkeypatch: pytest.MonkeyPatch) -> GhCliRunner:
    monkeypatch.setattr(github_api_module.subprocess, "run", router)
    monkeypatch.setattr(github_api_module, "_sleeper", lambda _: None)
    return GhCliRunner(REPOSITORY, token="t")


def test_transport_first_status_503_then_success(monkeypatch, capsys) -> None:
    router = GhSubprocessRouter(statuses="fail_once")
    runner = _runner(router, monkeypatch)

    code = run_review(args=_args(), env=_env(), gh=runner)

    assert code == 0
    assert router.statuses_published == ["success"]
    assert router.status_calls == 2
    assert "APPROVED" in capsys.readouterr().out


def test_transport_repeated_503_exhausts_to_typed_unavailable(monkeypatch, capsys) -> None:
    router = GhSubprocessRouter(statuses="always_503")
    runner = _runner(router, monkeypatch)

    code = run_review(args=_args(), env=_env(), gh=runner)

    assert code == 4
    assert router.status_calls == 3
    assert router.statuses_published == []
    out = capsys.readouterr().out
    assert "APPROVED" in out
    assert "unavailable" in out
    assert "CHANGES_REQUIRED" not in out


def test_transport_repeated_503_summary_records_unavailable(monkeypatch, tmp_path) -> None:
    summary = tmp_path / "summary.md"
    router = GhSubprocessRouter(statuses="always_503")
    runner = _runner(router, monkeypatch)

    code = run_review(args=_args(), env=_env(GITHUB_STEP_SUMMARY=str(summary)), gh=runner)

    assert code == 4
    text = summary.read_text(encoding="utf-8")
    assert "**Outcome**: `APPROVED`" in text
    assert "-> `unavailable`" in text
    assert "Changes required" not in text


def test_transport_boundary_is_binding_counterfactual(monkeypatch) -> None:
    router = GhSubprocessRouter(statuses="always_503")
    runner = _runner(router, monkeypatch)

    code = run_review(args=_args(), env=_env(), gh=runner)
    assert code == 4
    assert router.status_calls == 3

    router_boundary_off = GhSubprocessRouter(statuses="always_503")
    monkeypatch.setattr(github_api_module.subprocess, "run", router_boundary_off)
    monkeypatch.setattr(github_api_module.transport, "execute_with_retry", lambda fn, **kwargs: fn())

    code_without_boundary = run_review(args=_args(), env=_env(), gh=runner)

    assert code_without_boundary == 3
    assert router_boundary_off.status_calls == 1


def test_transport_permanent_publication_error_not_retried(monkeypatch) -> None:
    router = GhSubprocessRouter(statuses="always_422")
    runner = _runner(router, monkeypatch)

    code = run_review(args=_args(), env=_env(), gh=runner)

    assert code == 3
    assert router.status_calls == 1
    assert router.statuses_published == []


def test_transport_acquisition_503_retried_then_succeeds(monkeypatch, capsys) -> None:
    router = GhSubprocessRouter(pr_view="fail_once")
    runner = _runner(router, monkeypatch)

    code = run_review(args=_args(), env=_env(), gh=runner)

    assert code == 0
    assert router.pr_views == 3
    assert router.statuses_published == ["success"]
    assert "APPROVED" in capsys.readouterr().out


def test_runner_raises_typed_unavailable_after_exhaustion(monkeypatch) -> None:
    router = GhSubprocessRouter(statuses="always_503")
    runner = _runner(router, monkeypatch)

    with pytest.raises(GitHubUnavailable) as raised:
        runner.post_commit_status(sha=HEAD, state="success", context="x", description="y", target_url="z")

    assert raised.value.attempts == 3
