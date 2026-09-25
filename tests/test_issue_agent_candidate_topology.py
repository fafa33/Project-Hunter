"""End-to-end proof of the governed Issue Agent execution contract (P0-D).

`docs/ISSUE_AGENT_EXECUTION_CONTRACT.md` defines one execution chain. This
suite drives it end to end against a local bare Git repository standing in
for GitHub:

    trigger-signed authorization -> issuer edge over HTTP -> execution target
    -> isolated workspace at the exact signed base_sha -> fallback dispatcher
    -> provider push -> targeted validation -> ledger -> status endpoint
    -> Draft PR decision -> governance branch binding

Every component is the production one. Only three things are stand-ins, and
each is genuinely external to this repository:

- GitHub itself. A bare repository, reached through a test-only
  ``git-remote-https`` helper on a test ``GIT_EXEC_PATH``, so the production
  code keeps its pinned canonical ``https://github.com/<owner>/<name>.git``
  origin byte-for-byte and no production code path is aware of the stand-in.
- The model provider. A deterministic script edits, commits and pushes like
  an agent would.
- The candidate repository's own `scripts/hunter_pr_preflight.py`, which is
  repository content of the fake candidate, not a production seam.

New production symbols are resolved at call time, so that before the contract
is implemented these tests fail as tests rather than as collection errors.
"""

from __future__ import annotations

import fnmatch
import importlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import hunter_governance_review_v2 as governance
import hunter_issue_agent_issuer as issuer
import hunter_issue_agent_trigger as trigger
import pytest
from test_issue_agent_issuer import (
    AUTOMATION_SIGNING_KEY_HEX,
    AUTOMATION_VERIFYING_KEY_HEX,
    ISSUE_NUMBER,
    ISSUE_TITLE,
    ISSUE_URL,
    ISSUER_SIGNING_KEY,
    OWNER,
    REPOSITORY,
    UPDATED_AT,
    MutableClock,
    Webhook,
    _environment,
    _provenance,
    _provision_authority,
    _public_key_bytes,
)

from hunter.automation.issue_agent_execution import (
    ISSUE_AGENT_AUTHORIZATION_LABEL,
    REPOSITORY_CHECKOUT_ENV,
    IssueAgentExecutionLedger,
    SignedIssueAgentAuthorization,
    issue_agent_document_id,
)

CANONICAL_REMOTE = f"https://github.com/{REPOSITORY}.git"
ISSUE_TEXT = "src/hunter/example.py::apply_fix must preserve the governed authority boundary."
PROVIDERS = ("codex", "claude", "freebuff", "opencode", "jules")
EXECUTION_TIMEOUT = 90.0

_FAKE_PROVIDER = r"""
import os
import pathlib
import subprocess
import sys

sys.stdin.read()
repo = pathlib.Path(os.environ["HUNTER_AGENT_REPO_DIR"])
branch = os.environ["HUNTER_AGENT_BRANCH"]
marker = os.environ.get("FAKE_PROVIDER_MARKER")
if marker:
    pathlib.Path(marker).write_text(branch, encoding="utf-8")


def git(*args):
    subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True, text=True)


current = subprocess.run(
    ("git", "branch", "--show-current"), cwd=repo, check=True, capture_output=True, text=True
).stdout.strip()
if current != branch:
    sys.exit(3)
target = repo / "src" / "hunter" / "example.py"
target.write_text(target.read_text(encoding="utf-8") + "\n# governed agent change\n", encoding="utf-8")
git("add", "-A")
git("commit", "-q", "-m", "fix: apply governed Issue change")
if os.environ.get("FAKE_PROVIDER_MODE") == "merge-main":
    git("fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main")
    git("merge", "-q", "--no-ff", "--no-edit", "origin/main")
git("push", "-q", f"--force-with-lease=refs/heads/{branch}:", "origin", f"HEAD:refs/heads/{branch}")
"""

_STUB_PREFLIGHT = """import sys

sys.exit(0)
"""


def _module(name: str) -> Any:
    return importlib.import_module(name)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(("git", *args), cwd=cwd, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _commit(repo: Path, relative: str, content: str, message: str) -> str:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


class FakeGitHub:
    """A bare repository reached through the canonical GitHub URL."""

    def __init__(self, root: Path) -> None:
        self.bare = root / "github.git"
        subprocess.run(("git", "init", "-q", "--bare", "-b", "main", str(self.bare)), check=True)
        seed = root / "seed"
        seed.mkdir()
        _git(seed, "init", "-q", "-b", "main")
        _commit(seed, "README.md", "Project Hunter\n", "chore: initial")
        _commit(seed, "scripts/hunter_pr_preflight.py", _STUB_PREFLIGHT, "chore: candidate preflight")
        self.base = _commit(seed, "src/hunter/example.py", "def apply_fix():\n    return True\n", "feat: base")
        _git(seed, "remote", "add", "origin", CANONICAL_REMOTE)
        _git(seed, "push", "-q", "origin", "main")
        # main advances beyond the signed base: the workspace must stay on base.
        self.main_head = _commit(seed, "docs/later.md", "later main work\n", "docs: later main work")
        _git(seed, "push", "-q", "origin", "main")
        self.seed = seed

    def head(self, branch: str) -> str | None:
        completed = subprocess.run(
            ("git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"),
            cwd=self.bare,
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.stdout.strip() or None

    def parents(self, sha: str) -> list[str]:
        return _git(self.bare, "rev-list", "--parents", "-n", "1", sha).split()[1:]

    def range_commits(self, head: str) -> list[dict[str, Any]]:
        """Commits between main's merge-base and head, shaped like the GitHub compare API."""
        merge_base = _git(self.bare, "merge-base", "main", head)
        commits = []
        for sha in _git(self.bare, "rev-list", "--reverse", f"{merge_base}..{head}").split():
            commits.append(
                {
                    "sha": sha,
                    "parents": [{"sha": parent} for parent in self.parents(sha)],
                    "committer": {"login": OWNER},
                    "commit": {"verification": {"verified": True, "reason": "valid"}},
                }
            )
        return commits


def _exec_path_with_fake_github(root: Path, bare: Path) -> Path:
    """A Git exec path whose ``https`` transport reaches the bare repository."""
    exec_dir = root / "git-exec"
    exec_dir.mkdir()
    real = Path(_git(root, "--exec-path"))
    for entry in real.iterdir():
        if entry.name != "git-remote-https":
            (exec_dir / entry.name).symlink_to(entry)
    helper = exec_dir / "git-remote-https"
    helper.write_text(f'#!/bin/sh\nexec git remote-ext "$1" "git-%s {bare}"\n', encoding="utf-8")
    helper.chmod(0o755)
    return exec_dir


@pytest.fixture
def github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n"
        "\tname = Farhad5778\n"
        "\temail = 34549283+fafa33@users.noreply.github.com\n"
        "[commit]\n"
        "\tgpgsign = false\n"
        "[init]\n"
        "\tdefaultBranch = main\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_EXEC_PATH", str(_exec_path_with_fake_github(tmp_path, tmp_path / "github.git")))
    return FakeGitHub(tmp_path)


def _scope(**overrides: Any) -> dict[str, Any]:
    scope = {
        "branch_pattern": f"issue-{ISSUE_NUMBER}-*",
        "base_ref": "main",
        "allowed_paths": ["src/", "tests/"],
        "prohibited_paths": [],
    }
    scope.update(overrides)
    return scope


def _signed_document(scope: dict[str, Any]) -> str:
    body = ISSUE_TEXT + "\n<!-- hunter-task-scope-v1\n" + json.dumps(scope, separators=(",", ":")) + "\n-->"
    event = {
        "action": "labeled",
        "repository": {"full_name": REPOSITORY},
        "sender": {"login": OWNER},
        "label": {"name": ISSUE_AGENT_AUTHORIZATION_LABEL},
        "issue": {
            "number": ISSUE_NUMBER,
            "state": "open",
            "html_url": ISSUE_URL,
            "title": ISSUE_TITLE,
            "body": body,
            "updated_at": UPDATED_AT,
        },
    }
    authorization = trigger.authorize_event(
        event,
        expected_repository=REPOSITORY,
        owner_login=OWNER,
        authorization_label=ISSUE_AGENT_AUTHORIZATION_LABEL,
    )
    return trigger.sign_authorization(authorization, signing_key=ISSUER_SIGNING_KEY).to_json()


def _authorization_id(document: str) -> str:
    return SignedIssueAgentAuthorization.from_json(document).authorization.authorization_id


def _expected_branch(document: str) -> str:
    digest = _authorization_id(document).split(":", 1)[1]
    return f"issue-{ISSUE_NUMBER}-{digest[:16]}"


def _provider_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Configure the real fallback runtime: one deterministic agent, four failing providers."""
    script = tmp_path / "fake_provider.py"
    script.write_text(_FAKE_PROVIDER, encoding="utf-8")
    marker = tmp_path / "provider-ran"
    failing = json.dumps([sys.executable, "-c", "import sys; sys.stdin.read(); sys.exit(1)"])
    git_env = ["GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_EXEC_PATH"]
    for provider in PROVIDERS:
        name = provider.upper()
        command = json.dumps([sys.executable, str(script)]) if provider == "codex" else failing
        monkeypatch.setenv(f"HUNTER_AGENT_{name}_COMMAND", command)
        allowlist = [*git_env, "FAKE_PROVIDER_MODE", "FAKE_PROVIDER_MARKER"] if provider == "codex" else []
        monkeypatch.setenv(f"HUNTER_AGENT_{name}_ENV_ALLOWLIST", json.dumps(allowlist))
    monkeypatch.setenv(
        "HUNTER_AGENT_VALIDATION_COMMAND",
        json.dumps([sys.executable, "-m", "hunter.automation.agent_targeted_validation"]),
    )
    # PYTHONPATH lets the validation subprocess import this checkout's own code.
    monkeypatch.setenv("HUNTER_AGENT_VALIDATION_ENV_ALLOWLIST", json.dumps([*git_env, "PYTHONPATH"]))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    monkeypatch.setenv("HUNTER_AGENT_ATTEMPT_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("FAKE_PROVIDER_MARKER", str(marker))
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", AUTOMATION_SIGNING_KEY_HEX)
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY", AUTOMATION_VERIFYING_KEY_HEX)
    return marker


class Topology:
    """The production issuer composition over one durable authority database."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, document: str) -> None:
        self.document = document
        self.marker = _provider_environment(monkeypatch, tmp_path)
        self.database = tmp_path / "evidence.sqlite"
        self.workspace_root = tmp_path / "workspaces"
        clock = MutableClock()
        authorization = SignedIssueAgentAuthorization.from_json(document).authorization
        key = _provision_authority(self.database, issue_agent_document_id(authorization), clock)
        environment = _environment(tmp_path, self.database, verification_key=_public_key_bytes(key))
        environment[REPOSITORY_CHECKOUT_ENV] = str(self.workspace_root)
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        self.configuration = issuer.IssuerConfiguration.from_environment(
            environ=environment,
            provenance_resolver=_provenance,
            clock=clock,
        )
        self.services = issuer.compose_services(self.configuration)
        self.webhook = Webhook(self.services)
        self.ledger = IssueAgentExecutionLedger(self.database)
        self.authorization_id = _authorization_id(document)

    def post(self) -> tuple[int, str]:
        return self.webhook.post(self.document.encode("utf-8"))

    def wait_terminal(self) -> Any:
        deadline = time.monotonic() + EXECUTION_TIMEOUT
        while time.monotonic() < deadline:
            entry = self.ledger.entry(self.authorization_id)
            if entry is not None and entry.state in {"COMPLETED", "FAILED"}:
                return entry
            time.sleep(0.05)
        raise AssertionError(f"execution did not reach a terminal state: {self.ledger.entry(self.authorization_id)}")

    def status(self, authorization_id: str | None = None) -> tuple[int, dict[str, Any] | str]:
        code, body = self.webhook.get(f"/issue-agent/status/{authorization_id or self.authorization_id}")
        try:
            return code, json.loads(body)
        except json.JSONDecodeError:
            return code, body

    def close(self) -> None:
        self.webhook.close()


@pytest.fixture
def topology(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, github: FakeGitHub) -> Any:
    created: list[Topology] = []

    def _make(scope: dict[str, Any] | None = None) -> Topology:
        document = _signed_document(scope if scope is not None else _scope(base_sha=github.base))
        instance = Topology(tmp_path, monkeypatch, document)
        created.append(instance)
        return instance

    yield _make
    for instance in created:
        instance.close()


# --- the complete governed chain ----------------------------------------------


def test_signed_authorization_becomes_an_admissible_candidate_on_the_exact_base(
    topology: Any, github: FakeGitHub
) -> None:
    run = topology()
    branch = _expected_branch(run.document)

    status, _body = run.post()
    assert status == 200
    entry = run.wait_terminal()
    assert entry.state == "COMPLETED", (entry.failure_type, entry.failure_message)

    # The candidate lives on its own deterministic branch, forked at exactly the
    # signed base -- not at main's later head, and not on a shared branch.
    head = github.head(branch)
    assert head is not None
    assert github.parents(head) == [github.base]
    assert github.base != github.main_head
    assert github.head("issue-agent-execution") is None

    # The ledger binds the execution to its target and its verified outcome.
    assert entry.execution_branch == branch
    assert entry.base_sha == github.base
    assert entry.head_after == head
    assert entry.provider == "codex"

    # The outcome is observable over HTTP without the handoff or prompt.
    code, payload = run.status()
    assert code == 200
    assert isinstance(payload, dict)
    assert payload["state"] == "COMPLETED"
    assert payload["execution_branch"] == branch
    assert payload["base_sha"] == github.base
    assert payload["head_after"] == head
    assert "handoff_document" not in payload
    assert "failure_message" not in payload

    # The workspace was per-authorization and disposable.
    assert not run.workspace_root.exists() or not any(run.workspace_root.iterdir())

    # The pushed branch is the one the governance chain binds to this Issue and
    # the signed scope admits.
    assert governance.issue_for_branch(branch) == str(ISSUE_NUMBER)
    assert fnmatch.fnmatchcase(branch, f"issue-{ISSUE_NUMBER}-*")

    # The trusted Draft PR decision opens exactly one Draft PR against main.
    candidate_pr = _module("hunter_issue_agent_candidate_pr")
    decision = candidate_pr.decide_candidate_pr(
        candidate_pr.CandidateEvidence(
            branch=branch,
            workflow_head_sha=head,
            branch_head_sha=head,
            issue={"number": ISSUE_NUMBER, "state": "open", "title": ISSUE_TITLE},
            open_pull_requests=(),
            range_commits=tuple(github.range_commits(head)),
            authorized_signers=frozenset({"fafa33", "claude"}),
        )
    )
    assert decision.open is True, decision.reason
    assert decision.head == branch
    assert decision.base == "main"
    assert decision.draft is True
    assert f"#{ISSUE_NUMBER}" in decision.title


def test_replay_of_a_consumed_authorization_never_executes_again(topology: Any, github: FakeGitHub) -> None:
    run = topology()
    branch = _expected_branch(run.document)
    assert run.post()[0] == 200
    assert run.wait_terminal().state == "COMPLETED"
    first_head = github.head(branch)
    run.marker.unlink()

    status, body = run.post()
    assert status == 409, body
    time.sleep(0.5)
    assert github.head(branch) == first_head
    assert not run.marker.exists()


# --- refusals before the authorization is consumed (F2) -----------------------


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"branch_pattern": "issue-999-*"}, id="pattern-binds-another-issue"),
        pytest.param({"branch_pattern": "feature/*"}, id="pattern-admits-no-issue-branch"),
        pytest.param({"base_ref": "develop"}, id="base-ref-is-not-main"),
    ],
)
def test_underivable_execution_target_is_refused_before_claim(
    topology: Any, github: FakeGitHub, overrides: dict[str, Any]
) -> None:
    run = topology(_scope(base_sha=github.base, **overrides))
    status, body = run.post()
    assert status == 403, body
    assert run.ledger.entry(run.authorization_id) is None
    assert not run.marker.exists()


# --- post-ACK failures are durable, specific and observable (F5, F6, F9) ------


def test_base_not_on_main_fails_closed_without_running_a_provider(topology: Any, github: FakeGitHub) -> None:
    run = topology(_scope(base_sha="b" * 40))
    assert run.post()[0] == 200
    entry = run.wait_terminal()
    assert entry.state == "FAILED"
    assert entry.failure_code == "BASE_NOT_ON_MAIN"
    assert not run.marker.exists()
    assert github.head(_expected_branch(run.document)) is None
    code, payload = run.status()
    assert code == 200
    assert isinstance(payload, dict)
    assert payload["failure_code"] == "BASE_NOT_ON_MAIN"


def test_foreign_remote_branch_is_never_taken_over(topology: Any, github: FakeGitHub) -> None:
    run = topology()
    branch = _expected_branch(run.document)
    _git(github.seed, "push", "-q", "origin", f"{github.main_head}:refs/heads/{branch}")

    assert run.post()[0] == 200
    entry = run.wait_terminal()
    assert entry.state == "FAILED"
    assert entry.failure_code == "REMOTE_BRANCH_CONFLICT"
    assert github.head(branch) == github.main_head
    assert not run.marker.exists()


def test_merging_main_into_the_authorization_branch_is_never_a_valid_candidate(
    topology: Any, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_PROVIDER_MODE", "merge-main")
    run = topology()
    branch = _expected_branch(run.document)
    assert run.post()[0] == 200
    entry = run.wait_terminal()
    assert entry.state == "FAILED"
    assert entry.failure_code == "PROVIDER_POOL_EXHAUSTED"

    head = github.head(branch)
    assert head is not None and len(github.parents(head)) == 2
    candidate_pr = _module("hunter_issue_agent_candidate_pr")
    decision = candidate_pr.decide_candidate_pr(
        candidate_pr.CandidateEvidence(
            branch=branch,
            workflow_head_sha=head,
            branch_head_sha=head,
            issue={"number": ISSUE_NUMBER, "state": "open", "title": ISSUE_TITLE},
            open_pull_requests=(),
            range_commits=tuple(github.range_commits(head)),
            authorized_signers=frozenset({"fafa33", "claude"}),
        )
    )
    assert decision.open is False
    assert "merge commit" in decision.reason


def test_status_endpoint_reveals_nothing_for_unknown_or_malformed_identities(topology: Any, github: FakeGitHub) -> None:
    run = topology()
    unknown = "hunter-issue-agent-authorization:" + "0" * 64
    code, _payload = run.status(unknown)
    assert code == 404
    for malformed in ("..%2F..%2Fetc", "hunter-issue-agent-authorization:XYZ", "x" * 300):
        code, _payload = run.status(malformed)
        assert code == 404


# --- the pure execution target derivation (I1) ---------------------------------


def _signed(scope: dict[str, Any], base_sha: str) -> Any:
    return SignedIssueAgentAuthorization.from_json(_signed_document(scope | {"base_sha": base_sha}))


def test_execution_target_is_a_pure_function_of_the_signed_authorization() -> None:
    workspace = _module("hunter.automation.issue_agent_workspace")
    signed = _signed(_scope(), "c" * 40)
    first = workspace.derive_execution_target(signed)
    second = workspace.derive_execution_target(SignedIssueAgentAuthorization.from_json(signed.to_json()))
    assert first == second
    digest = signed.authorization.authorization_id.split(":", 1)[1]
    assert first.branch == f"issue-{ISSUE_NUMBER}-{digest[:16]}"
    assert first.base_sha == "c" * 40
    assert first.base_ref == "main"
    assert first.issue_number == ISSUE_NUMBER
    assert first.authorization_id == signed.authorization.authorization_id


def test_a_different_authorization_gets_a_different_branch() -> None:
    workspace = _module("hunter.automation.issue_agent_workspace")
    first = workspace.derive_execution_target(_signed(_scope(), "c" * 40))
    rebased = workspace.derive_execution_target(_signed(_scope(), "d" * 40))
    assert first.branch != rebased.branch
    assert first.branch.startswith(f"issue-{ISSUE_NUMBER}-")
    assert rebased.branch.startswith(f"issue-{ISSUE_NUMBER}-")
