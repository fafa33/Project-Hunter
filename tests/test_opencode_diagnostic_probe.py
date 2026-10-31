"""Issue #574: the hosted OpenCode diagnostic must exercise the production execution contract.

Two hosted observations motivated these tests:

* the diagnostic launched ``opencode run`` with its own hand-built argv and environment, so a hosted failure described
  a runtime the executor never uses (diagnostic/production drift), and
* it reported ``git_changed=True`` for ``probe.txt``, which the diagnostic itself had pre-created untracked, so the
  observation that was presented as "the model edited a file" proved nothing.

The tests pin three properties: the probe launches through the production contract, success requires a real edit to a
tracked repository file (detected by the production collector), and only closed-vocabulary telemetry is printed.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import hunter_opencode_diagnostic_probe as probe
import pytest

from hunter.automation import issue_agent_roles as roles

CREDENTIAL = "sk-live-diagnostic-credential-0123456789"
PRIVATE_MODEL_TEXT = "PRIVATE_MODEL_STREAM_AND_PROMPT_TEXT"


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A trusted checkout the probe takes its base commit from, exactly as ``GITHUB_WORKSPACE`` is on a runner."""

    origin = tmp_path / "source"
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(origin)], check=True)
    (origin / "README.md").write_text("base\n")
    git(origin, "add", "README.md")
    git(origin, "-c", "user.name=s", "-c", "user.email=s@s", "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    return origin


class StandInIsolation:
    """Records the launch and lets a test decide what the stand-in model does inside the workspace."""

    def __init__(self, effect: Callable[[Path], None] | None = None, *, exit_code: int = 0, timeout: bool = False):
        self.effect, self.exit_code, self.timeout = effect, exit_code, timeout
        self.calls: list[dict[str, object]] = []
        self.prepared = 0
        self.stopped = 0

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        public_env: Mapping[str, str],
        secret_env: Mapping[str, str],
        stdin: bytes,
        timeout: float,
    ) -> int:
        self.calls.append(
            {
                "argv": tuple(argv),
                "cwd": cwd,
                "public": dict(public_env),
                "secret": dict(secret_env),
                "stdin": stdin,
            }
        )
        if self.timeout:
            raise subprocess.TimeoutExpired(list(argv), timeout)
        if self.effect is not None:
            self.effect(cwd)
        return self.exit_code

    def prepare(self, *paths: Path) -> None:
        self.prepared += 1

    def stop(self) -> None:
        self.stopped += 1


def _probe(tmp_path: Path, source: Path, isolation: StandInIsolation) -> probe.ProbeOutcome:
    root = tmp_path / "run"
    return probe.run_probe(
        source=source,
        workspace=root / "workspace",
        git_dir=root / "trusted.git",
        home=root / "home",
        opencode=Path("/opt/hunter/bin/opencode"),
        model="opencode/glm-5.3-flash-free",
        secret_environment={"GROQ_API_KEY": CREDENTIAL},
        isolation=isolation,
        timeout=30,
    )


def _edit(content: bytes) -> Callable[[Path], None]:
    def edit(workspace: Path) -> None:
        (workspace / probe.PROBE_FILE).write_bytes(content)

    return edit


# --- the edit assertion has to mean "a repository file changed" ------------------------------------------------


def test_the_probe_base_tracks_the_probe_file_outside_the_model_workspace(tmp_path: Path, source: Path) -> None:
    workspace, git_dir = tmp_path / "workspace", tmp_path / "trusted.git"
    base = probe.prepare(source, workspace, git_dir)
    assert not (workspace / ".git").exists()  # a planted workspace .git can never configure the trusted view
    tracked = git(
        workspace, "--git-dir", str(git_dir), "--work-tree", str(workspace), "ls-tree", "-r", "--name-only", base
    )
    assert probe.PROBE_FILE in tracked.splitlines()  # committed before the model runs: already tracked
    assert (workspace / probe.PROBE_FILE).read_bytes() == probe.PROBE_BEFORE


def test_a_pre_created_untracked_file_is_never_counted_as_an_edit(tmp_path: Path, source: Path) -> None:
    """The exact false claim the hosted diagnostic made: untracked noise reported as ``git_changed=True``."""

    workspace, git_dir = tmp_path / "workspace", tmp_path / "trusted.git"
    base = probe.prepare(source, workspace, git_dir)
    assert probe.assess(workspace, git_dir, base) == (False, 0)
    (workspace / "stray-note.txt").write_text("created, not edited\n")
    assert probe.assess(workspace, git_dir, base) == (False, 1)


def test_the_probe_reports_success_only_for_the_expected_tracked_edit(tmp_path: Path, source: Path) -> None:
    workspace, git_dir = tmp_path / "workspace", tmp_path / "trusted.git"
    base = probe.prepare(source, workspace, git_dir)
    (workspace / probe.PROBE_FILE).write_bytes(probe.PROBE_AFTER)
    assert probe.assess(workspace, git_dir, base) == (True, 1)
    (workspace / "extra.txt").write_text("out of task\n")
    assert probe.assess(workspace, git_dir, base) == (False, 2)


def test_a_zero_exit_without_the_expected_edit_is_not_success(tmp_path: Path, source: Path) -> None:
    outcome = _probe(tmp_path, source, StandInIsolation())
    assert (outcome.exit_zero, outcome.tracked_edit, outcome.changed_files, outcome.succeeded) == (
        True,
        False,
        0,
        False,
    )
    assert _probe(tmp_path / "ok", source, StandInIsolation(_edit(probe.PROBE_AFTER))).succeeded is True


@pytest.mark.parametrize("exit_code", [1, 2])
def test_a_provider_failure_exit_is_not_success(tmp_path: Path, source: Path, exit_code: int) -> None:
    outcome = _probe(tmp_path, source, StandInIsolation(_edit(probe.PROBE_AFTER), exit_code=exit_code))
    assert outcome.exit_zero is False and outcome.succeeded is False


def test_a_timeout_is_reported_as_a_timeout_not_as_a_missing_edit(tmp_path: Path, source: Path) -> None:
    isolation = StandInIsolation(timeout=True)
    outcome = _probe(tmp_path, source, isolation)
    assert (outcome.timed_out, outcome.exit_zero, outcome.succeeded) == (True, False, False)
    assert isolation.stopped == 1  # nothing the model started survives into collection


# --- the launch is the production contract, structurally -------------------------------------------------------


def test_the_probe_launches_through_the_production_opencode_contract(tmp_path: Path, source: Path) -> None:
    isolation = StandInIsolation(_edit(probe.PROBE_AFTER))
    outcome = _probe(tmp_path, source, isolation)
    assert outcome.succeeded is True and len(isolation.calls) == 1
    call = isolation.calls[0]
    home, workspace = tmp_path / "run" / "home", tmp_path / "run" / "workspace"
    invocation = probe.probe_invocation(
        opencode=Path("/opt/hunter/bin/opencode"),
        model="opencode/glm-5.3-flash-free",
        home=home,
        workspace=workspace,
    )
    # The probe's launch *is* the production value: no second copy of the argv/env contract.
    assert call["argv"] == invocation.argv
    assert call["public"] == invocation.public_env
    assert call["secret"] == {"GROQ_API_KEY": CREDENTIAL}
    assert call["stdin"] == b""
    for expected in (
        "--pure",
        "--dir",
        str(workspace),
        roles.EDIT_DIRECTIVE,
        "--file",
        str(home / "hunter-task.txt"),
    ):
        assert expected in invocation.argv
    permission = json.loads(invocation.public_env["OPENCODE_CONFIG_CONTENT"])["permission"]
    assert permission["*"] == "deny" and permission["external_directory"] == "deny"
    assert invocation.public_env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    # The task is attached privately: it never enters argv, and the credential never enters the public environment.
    assert probe.PROBE_TASK not in " ".join(invocation.argv)
    assert CREDENTIAL not in json.dumps(dict(invocation.public_env))
    assert (home / "hunter-task.txt").stat().st_mode & 0o777 == 0o600


def test_the_probe_contract_telemetry_reports_what_actually_ran(tmp_path: Path, source: Path) -> None:
    isolation = StandInIsolation(_edit(probe.PROBE_AFTER))
    _probe(tmp_path, source, isolation)
    invocation = probe.probe_invocation(
        opencode=Path("/opt/hunter/bin/opencode"),
        model="opencode/glm-5.3-flash-free",
        home=tmp_path / "run" / "home",
        workspace=tmp_path / "run" / "workspace",
    )
    telemetry = probe.contract_telemetry(invocation, tmp_path / "run" / "workspace")
    assert telemetry == {
        "pure": True,
        "workspace_bound": True,
        "workspace_only_tools": True,
        "shell_denied": True,
        "project_config_disabled": True,
    }


def test_contract_telemetry_never_claims_a_contract_that_did_not_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # A hand-built launch like the old diagnostic: no pure mode, no --dir, no permission contract at all.
    hand_built = roles.OpenCodeInvocation(
        argv=("/opt/hunter/bin/opencode", "run", "--model", "m"),
        stdin=b"",
        public_env={"PWD": str(workspace)},
    )
    telemetry = probe.contract_telemetry(hand_built, workspace)
    assert (telemetry["pure"], telemetry["workspace_bound"], telemetry["workspace_only_tools"]) == (False, False, False)


# --- confidentiality: closed vocabulary only -------------------------------------------------------------------


def test_the_probe_prints_only_closed_vocabulary_telemetry(
    tmp_path: Path, source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _probe(tmp_path, source, StandInIsolation(_edit(probe.PROBE_AFTER)))
    output = capsys.readouterr().out
    lines = [line for line in output.splitlines() if line]
    assert lines and all(
        line.startswith(("hosted diagnostic contract: ", "hosted diagnostic result: ")) for line in lines
    )
    for forbidden in (CREDENTIAL, probe.PROBE_TASK, PRIVATE_MODEL_TEXT, "before\n"):
        assert forbidden not in output
    assert "tracked_edit=True" in output and "changed_files=1" in output


def test_a_harness_failure_is_reported_by_code_and_never_echoes_candidate_content(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing(**_kwargs: object) -> probe.ProbeOutcome:
        raise probe.ProbeFailed(f"{PRIVATE_MODEL_TEXT} {CREDENTIAL}")

    monkeypatch.setattr(probe, "run_probe", failing)
    monkeypatch.setattr(probe.roles, "SudoIsolation", lambda _user: StandInIsolation())
    monkeypatch.setenv("HUNTER_DIAGNOSTIC_KEY", CREDENTIAL)
    rc = probe.main(
        [
            "--source", "/src", "--workspace", "/ws", "--git-dir", "/ws.git", "--home", "/home",
            "--opencode", "/opt/hunter/bin/opencode", "--model", "m",
            "--secret-env", "HUNTER_DIAGNOSTIC_KEY", "--model-key-name", "GROQ_API_KEY",
        ]
    )  # fmt: skip
    output = capsys.readouterr().out
    assert rc == 2 and "hosted diagnostic refused: ProbeFailed" in output
    assert PRIVATE_MODEL_TEXT not in output and CREDENTIAL not in output


def test_a_missing_credential_refuses_before_any_model_launch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("HUNTER_DIAGNOSTIC_KEY", raising=False)
    launched = StandInIsolation()
    rc = probe.main(
        [
            "--source", "/src", "--workspace", "/ws", "--git-dir", "/ws.git", "--home", "/home",
            "--opencode", "/opt/hunter/bin/opencode", "--model", "m",
            "--secret-env", "HUNTER_DIAGNOSTIC_KEY", "--model-key-name", "GROQ_API_KEY",
        ]
    )  # fmt: skip
    assert rc == 2 and launched.calls == []
    assert "refused: configured model or model credential is absent" in capsys.readouterr().out


def test_a_runtime_that_is_not_opencode_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HUNTER_DIAGNOSTIC_KEY", CREDENTIAL)
    rc = probe.main(
        [
            "--source", "/src", "--workspace", "/ws", "--git-dir", "/ws.git", "--home", "/home",
            "--opencode", "/bin/sh", "--model", "m",
            "--secret-env", "HUNTER_DIAGNOSTIC_KEY", "--model-key-name", "GROQ_API_KEY",
        ]
    )  # fmt: skip
    assert rc == 2
    assert "the configured runtime is not the pinned OpenCode executable" in capsys.readouterr().out
