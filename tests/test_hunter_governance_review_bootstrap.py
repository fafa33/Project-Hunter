"""Bootstrap-path regression tests: Hunter Governance Review status publication
must execute through the shared resilient GitHub transport boundary even when
the engine is materialized from the PR head (installation bootstrap).

The exact hosted failure being locked down (runs 509/510 on head b3a8b65):

    [Outcome] APPROVED
    gh api repos/fafa33/Project-Hunter/statuses/<sha> -f state=success ...
    -> gh: No server is currently available to service your request.
       Sorry about that. Please try resubmitting your request and contact
       us if the problem persists. (HTTP 503)
    ##[error]Process completed with exit code 3.

Root cause (proven from hosted logs): the workflow's trusted-engine branch
executes the engine from the default branch, which predates the shared
transport boundary; bootstrap fallback never activates because main already
owns a pre-resilience engine. The workflow now falls back to the PR-head
engine whenever the default-branch engine lacks
``scripts/hunter_github_transport.py`` -- and this suite proves that engine
really is bound to the boundary.

Proofs:

A. bootstrap + APPROVED + first status POST 503 + second succeeds
   -> final success, APPROVED preserved, exactly one terminal status.
B. bootstrap + APPROVED + repeated 503 until exhaustion
   -> typed publication-unavailable (exit 4), APPROVED preserved,
   no CHANGES_REQUIRED, no raw exception, no contradictory status.
C. the executing path calls the shared boundary -- disabling the boundary
   changes the outcome (non-vacuous counterfactual).
D. permanent 4xx publication error -> no inappropriate retry, distinct
   from transient unavailability.
E. workflow/bootstrap fixture: trusted engine missing or pre-resilience on
   main -> PR-engine bootstrap fallback -> APPROVED -> publish status.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
from pathlib import Path
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
    """Faithful ``gh`` CLI stand-in served through ``subprocess.run``.

    Mirrors the exact invocation shapes of ``GhCliRunner`` (``pr view``,
    ``api .../pulls/N/files --paginate --jq ...``, raw contents fetches,
    directory listings, and the ``statuses`` POST) and the exact hosted
    ``gh`` 503/4xx diagnostics.
    """

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
                return self._directory_listing(args)
            return self._pull_files(args)
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
        path, _, rest = target.partition("?ref=")
        path = path.split("contents/", 1)[1]
        ref = rest
        content = _HIERARCHY_DOCS.get(path) if ref == BASE else None
        if content is None:
            return self._failure(args, "HTTP 404: Not Found")
        return CompletedProcess(args, 0, stdout=content, stderr="")

    def _directory_listing(self, args: list[str]) -> CompletedProcess:
        return CompletedProcess(args, 0, stdout="", stderr="")

    def _pull_files(self, args: list[str]) -> CompletedProcess:
        return CompletedProcess(args, 0, stdout="src/hunter/example.py\tmodified\t1\t0\n", stderr="")

    def _failure(self, args: list[str], stderr: str) -> CompletedProcess:
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


def _workflow_text() -> str:
    return (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-governance-review.yml").read_text(
        encoding="utf-8"
    )


def _engine_selection_script(workflow: str) -> str:
    """The workflow's run block, trimmed to the engine-selection shell logic."""
    tail = workflow.split("run: |", 1)[1]
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    start = next(i for i, line in enumerate(lines) if line.startswith("set -euo pipefail"))
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("cd "))
    return "\n".join(lines[start:end])


def _engine_selection_condition(script: str) -> str:
    """The exact multi-line ``if [ ! -f ... ]; then`` bootstrap condition."""
    lines = script.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("if [ ! -f"))
    end = next(i for i in range(start, len(lines)) if lines[i].rstrip().endswith("; then"))
    return "\n".join(lines[start : end + 1])


_CONDITION_FILE_RE = re.compile(r'\[ ! -f "([^"]+)" \]')


def _condition_file_paths(condition: str) -> set[str]:
    return {match.group(1).removeprefix("${ENGINE_ROOT}/scripts/") for match in _CONDITION_FILE_RE.finditer(condition)}


def _bootstrap_guard(script: str) -> str:
    for line in script.splitlines():
        if '"pull_request"' in line and '"277"' in line:
            return line
    raise AssertionError("bootstrap PR guard not found in workflow selection script")


def _engine_import_closure() -> set[str]:
    """Every local module the review engine loads at import time, relative to
    ``scripts/``. Only module-level imports count: function-deferred imports
    (for example preflight's merge-readiness CLI subcommands) are not part of
    what must exist on the default branch before the engine can run."""
    scripts_root = Path(__file__).resolve().parents[1] / "scripts"

    def module_level_imports(tree: ast.AST) -> list[str]:
        modules: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.append(node.module)
        return modules

    def resolve(module: str) -> list[Path]:
        as_module = scripts_root / f"{Path(*module.split('.'))}.py"
        return [as_module] if as_module.is_file() else []

    seen: set[Path] = set()
    stack = [scripts_root / "hunter_governance_review" / "__main__.py"]
    while stack:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module in module_level_imports(tree):
            stack.extend(resolve(module))
    return {path.relative_to(scripts_root).as_posix() for path in seen}


def _materialize_engine(tmp_path: Path, missing: tuple[str, ...] = ()) -> Path:
    engine = tmp_path / "engine"
    for relative in _engine_import_closure():
        if relative in missing:
            continue
        target = engine / "scripts" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    return engine


def _execute_selection(engine: Path, *, event_name: str, pr_number: str) -> CompletedProcess:
    script = _engine_selection_script(_workflow_text()) + '\nprintf "%s" "${ENGINE_ROOT}"'
    env = os.environ.copy()
    env["GITHUB_WORKSPACE"] = str(engine.parent)
    env["GITHUB_EVENT_NAME"] = event_name
    env["PR_NUMBER"] = pr_number
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_bootstrap_approved_first_status_503_then_success(monkeypatch, capsys) -> None:
    """A: transient publication outage recovers through bounded retry."""
    router = GhSubprocessRouter(statuses="fail_once")
    runner = _runner(router, monkeypatch)

    code = run_review(args=_args(), env=_env(), gh=runner)

    assert code == 0
    assert router.statuses_published == ["success"]
    assert router.status_calls == 2  # one 503 + one successful attempt
    assert "APPROVED" in capsys.readouterr().out


def test_bootstrap_repeated_503_exhausts_to_typed_unavailable(monkeypatch, capsys) -> None:
    """B: exhaustion is a typed publication-unavailable, never a semantic verdict."""
    router = GhSubprocessRouter(statuses="always_503")
    runner = _runner(router, monkeypatch)

    code = run_review(args=_args(), env=_env(), gh=runner)

    assert code == 4
    assert router.status_calls == 3  # bounded: exactly the retry policy's attempts
    assert router.statuses_published == []
    out = capsys.readouterr().out
    assert "APPROVED" in out
    assert "unavailable" in out
    assert "CHANGES_REQUIRED" not in out


def test_bootstrap_repeated_503_summary_records_unavailable(monkeypatch, tmp_path) -> None:
    summary = tmp_path / "summary.md"
    router = GhSubprocessRouter(statuses="always_503")
    runner = _runner(router, monkeypatch)

    code = run_review(args=_args(), env=_env(GITHUB_STEP_SUMMARY=str(summary)), gh=runner)

    assert code == 4
    text = summary.read_text(encoding="utf-8")
    assert "**Outcome**: `APPROVED`" in text
    assert "-> `unavailable`" in text
    assert "Changes required" not in text


def test_bootstrap_publication_boundary_is_binding_counterfactual(monkeypatch) -> None:
    """C: the executing path really calls the shared boundary.

    With the boundary active, a repeated 503 is retried exactly 3 times and
    ends in the typed exit-4 outcome. With the boundary disabled (the
    counterfactual -- what the pre-resilience engine did), the same outage
    is a single raw attempt ending in the permanent-failure exit 3.
    """
    router = GhSubprocessRouter(statuses="always_503")
    runner = _runner(router, monkeypatch)

    code = run_review(args=_args(), env=_env(), gh=runner)
    assert code == 4
    assert router.status_calls == 3

    router_boundary_off = GhSubprocessRouter(statuses="always_503")
    monkeypatch.setattr(github_api_module.subprocess, "run", router_boundary_off)
    monkeypatch.setattr(
        github_api_module.transport,
        "execute_with_retry",
        lambda fn, **kwargs: fn(),
    )
    code_without_boundary = run_review(args=_args(), env=_env(), gh=runner)
    assert code_without_boundary == 3
    assert router_boundary_off.status_calls == 1


def test_bootstrap_permanent_publication_error_not_retried(monkeypatch) -> None:
    """D: a permanent 4xx is never retried and stays distinct from unavailability."""
    router = GhSubprocessRouter(statuses="always_422")
    runner = _runner(router, monkeypatch)

    code = run_review(args=_args(), env=_env(), gh=runner)

    assert code == 3
    assert router.status_calls == 1
    assert router.statuses_published == []


def test_bootstrap_acquisition_503_retried_then_succeeds(monkeypatch, capsys) -> None:
    """The 509-pattern: a 503 during PR resolution is retried, not a crash."""
    router = GhSubprocessRouter(pr_view="fail_once")
    runner = _runner(router, monkeypatch)

    code = run_review(args=_args(), env=_env(), gh=runner)

    assert code == 0
    assert router.pr_views == 3  # failed attempt + retry + freshness re-resolve
    assert router.statuses_published == ["success"]
    assert "APPROVED" in capsys.readouterr().out


def test_runner_raises_typed_unavailable_after_exhaustion(monkeypatch) -> None:
    router = GhSubprocessRouter(statuses="always_503")
    runner = _runner(router, monkeypatch)

    with pytest.raises(GitHubUnavailable) as raised:
        runner.post_commit_status(sha=HEAD, state="success", context="x", description="y", target_url="z")
    assert raised.value.attempts == 3


def test_workflow_bootstrap_binds_resilient_engine() -> None:
    """E: the workflow fixture mirrors the exact bootstrap decision path."""
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-governance-review.yml"
    ).read_text(encoding="utf-8")
    assert '-f "${ENGINE_ROOT}/scripts/hunter_github_transport.py"' in workflow
    assert 'ENGINE_ROOT="${GITHUB_WORKSPACE}/pr-engine"' in workflow
    assert "one-time installation review" in workflow
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in workflow
    assert "hunter_github_transport.py" in workflow


def test_trusted_engine_selection_condition_covers_complete_import_closure() -> None:
    """The workflow's bootstrap condition must name every local module of the
    review engine's import closure -- exactly the files the engine needs before
    status publication. The closure is computed from the real source imports,
    so removing any required file check from the workflow fails this test."""
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-governance-review.yml"
    ).read_text(encoding="utf-8")
    condition = _engine_selection_condition(_engine_selection_script(workflow))
    checked = {path for path in _condition_file_paths(condition)}
    assert checked == set(_engine_import_closure())


def test_workflow_bootstrap_guard_restricts_pr_head_execution_to_pr_277() -> None:
    """PR-head bootstrap may be reachable only when the event is a
    ``pull_request`` event for installation PR #277; the fallback assignment
    must sit inside that guarded branch, and every other event/PR must hit the
    fail-closed error path instead of PR-controlled code."""
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-governance-review.yml"
    ).read_text(encoding="utf-8")
    script = _engine_selection_script(workflow)
    guard = _bootstrap_guard(script)
    assert '"pull_request"' in guard
    assert '"277"' in guard
    pr_engine_assignment = 'ENGINE_ROOT="${GITHUB_WORKSPACE}/pr-engine"'
    assert pr_engine_assignment in script
    assert script.index(pr_engine_assignment) > script.index(guard)
    assert "exit 1" in script


def test_workflow_bootstrap_decision_executes_binding(tmp_path) -> None:
    """Execute the exact shell decision text extracted from the workflow and
    prove the four live behaviors: complete engine never bootstraps; the
    installation PR may bootstrap only when a closure file is missing; any
    other PR number fails closed; and a manual dispatch for PR #277 also fails
    closed (the pull_request event alone authorizes the one-time bootstrap)."""
    complete = _materialize_engine(tmp_path / "complete")
    incomplete = _materialize_engine(tmp_path / "incomplete", missing=("hunter_governance_preflight.py",))

    ran = _execute_selection(complete, event_name="pull_request", pr_number="999")
    assert ran.returncode == 0
    assert ran.stdout.strip() == str(complete)

    bootstrapped = _execute_selection(incomplete, event_name="pull_request", pr_number="277")
    assert bootstrapped.returncode == 0
    assert bootstrapped.stdout.strip().splitlines()[-1] == str(incomplete.parent / "pr-engine")
    assert "bootstrap mode" in bootstrapped.stdout

    other_pr = _execute_selection(incomplete, event_name="pull_request", pr_number="999")
    assert other_pr.returncode == 1
    assert "restricted to installation pull request #277" in other_pr.stdout

    dispatched = _execute_selection(incomplete, event_name="workflow_dispatch", pr_number="277")
    assert dispatched.returncode == 1
    assert "restricted to installation pull request #277" in dispatched.stdout
