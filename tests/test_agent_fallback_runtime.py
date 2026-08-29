from __future__ import annotations

import signal
import subprocess
from pathlib import Path
from typing import Any

import pytest

import hunter.automation.agent_fallback_runtime as runtime_module
from hunter.automation.agent_fallback_runtime import (
    PROVIDER_COMMAND_ENV,
    PROVIDER_ENV_ALLOWLIST_ENV,
    AgentFallbackRuntimeError,
    OperationalAgentFallbackRuntime,
)
from hunter.automation.n8n_handoff import serialize_prompt_automation_handoff
from hunter.evidence_intelligence.smart_prompt_routing import _issue_prompt_automation_envelope

_SIGNING_KEY = "11" * 32
_VERIFYING_KEY = "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737"
_HEAD_A = "a" * 40
_HEAD_B = "b" * 40
_REMOTE = "https://github.com/fafa33/Project-Hunter.git"


def _environment() -> dict[str, str]:
    values = {
        "PATH": "/usr/bin:/bin",
        "HUNTER_PROMPT_AUTOMATION_SIGNING_KEY": _SIGNING_KEY,
        "HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY": _VERIFYING_KEY,
        "HUNTER_AGENT_VALIDATION_COMMAND": '["validate-wrapper"]',
        "HUNTER_AGENT_ATTEMPT_TIMEOUT_SECONDS": "30",
        "OPENAI_API_KEY": "codex-secret",
        "ANTHROPIC_API_KEY": "claude-secret",
    }
    for provider, name in PROVIDER_COMMAND_ENV.items():
        values[name] = f'["{provider}-wrapper"]'
    values[PROVIDER_ENV_ALLOWLIST_ENV["codex"]] = '["OPENAI_API_KEY"]'
    values[PROVIDER_ENV_ALLOWLIST_ENV["claude"]] = '["ANTHROPIC_API_KEY"]'
    return values


def _handoff(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", _SIGNING_KEY)
    envelope = _issue_prompt_automation_envelope(
        task_request_id="task-384",
        route_registry_identity="routes-1",
        profile_registry_identity="profiles-1",
        route_identity="engineering-review-fix-route-1",
        profile_identity="engineering-review-fix-1",
        build_manifest_id="manifest-1",
        build_record_id="build-1",
    )
    return serialize_prompt_automation_handoff(envelope)


def _completed(
    argv: tuple[str, ...] | list[str], returncode: int, *, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


def test_real_adapter_contract_falls_through_rate_limit_without_mutating_handoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = _handoff(monkeypatch)
    provider_inputs: list[tuple[str, str]] = []
    provider_envs: dict[str, dict[str, str]] = {}
    remote_heads = iter((_HEAD_A, _HEAD_A, _HEAD_B))

    def fake_run(argv: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = tuple(argv)
        if command[:3] == ("git", "ls-remote", "--exit-code"):
            assert command[3] == _REMOTE
            head = next(remote_heads)
            return _completed(command, 0, stdout=f"{head}\trefs/heads/issue-384\n")
        pytest.fail(f"unexpected command: {command}")

    def fake_isolated(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
        input_text: str | None = None,
    ) -> runtime_module._ProcessOutcome:
        del cwd, timeout
        command = tuple(argv)
        if command == ("codex-wrapper",):
            provider_inputs.append(("codex", input_text or ""))
            provider_envs["codex"] = dict(env)
            return runtime_module._ProcessOutcome(75)
        if command == ("claude-wrapper",):
            provider_inputs.append(("claude", input_text or ""))
            provider_envs["claude"] = dict(env)
            return runtime_module._ProcessOutcome(0)
        if command == ("validate-wrapper",):
            assert env["HUNTER_AGENT_EXPECTED_HEAD"] == _HEAD_B
            assert "HUNTER_PROMPT_AUTOMATION_SIGNING_KEY" not in env
            assert "OPENAI_API_KEY" not in env
            assert "ANTHROPIC_API_KEY" not in env
            return runtime_module._ProcessOutcome(0)
        pytest.fail(f"unexpected isolated command: {command}")

    monkeypatch.setattr(runtime_module, "_pinned_github_remote", lambda _repo: _REMOTE)
    monkeypatch.setattr(runtime_module, "_run_isolated", fake_isolated)
    monkeypatch.setattr(runtime_module.subprocess, "run", fake_run)
    receipt = OperationalAgentFallbackRuntime(
        repo_dir=tmp_path,
        branch="issue-384",
        environ=_environment(),
    ).dispatch(document)

    assert receipt.provider == "claude"
    assert receipt.head_before == _HEAD_A
    assert receipt.head_after == _HEAD_B
    assert receipt.validation_succeeded is True
    assert provider_inputs == [("codex", document), ("claude", document)]
    assert [attempt["state"] for attempt in receipt.attempts] == ["rate_limited", "available"]
    assert provider_envs["codex"]["OPENAI_API_KEY"] == "codex-secret"
    assert "ANTHROPIC_API_KEY" not in provider_envs["codex"]
    assert "HUNTER_PROMPT_AUTOMATION_SIGNING_KEY" not in provider_envs["codex"]
    assert provider_envs["claude"]["ANTHROPIC_API_KEY"] == "claude-secret"
    assert "OPENAI_API_KEY" not in provider_envs["claude"]
    assert {attempt["detail"] for attempt in receipt.attempts} == {"provider_rate_limited", ""}


def test_timeout_terminates_provider_process_group(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    killed: list[tuple[int, signal.Signals]] = []

    class FakeProcess:
        pid = 4242
        returncode: int | None = None

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.calls = 0

        def communicate(self, input: str | None = None, timeout: float | None = None) -> tuple[None, None]:
            del input, timeout
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(cmd=("provider",), timeout=1)
            self.returncode = -int(signal.SIGKILL)
            return (None, None)

        def poll(self) -> int | None:
            return self.returncode

    monkeypatch.setattr(runtime_module.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(runtime_module.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))
    outcome = runtime_module._run_isolated(
        ("provider",),
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout=1,
        input_text="handoff",
    )

    assert outcome.timed_out is True
    assert killed
    assert all(pgid == 4242 and sig == signal.SIGKILL for pgid, sig in killed)


def test_runtime_requires_all_five_provider_adapters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    environ = _environment()
    del environ["HUNTER_AGENT_FREEBUFF_COMMAND"]
    monkeypatch.setattr(runtime_module, "_pinned_github_remote", lambda _repo: _REMOTE)

    with pytest.raises(AgentFallbackRuntimeError, match="HUNTER_AGENT_FREEBUFF_COMMAND"):
        OperationalAgentFallbackRuntime(repo_dir=tmp_path, branch="issue-384", environ=environ)


def test_provider_commands_are_argv_json_not_shell_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    environ = _environment()
    environ["HUNTER_AGENT_OPENCODE_COMMAND"] = "opencode --run"
    monkeypatch.setattr(runtime_module, "_pinned_github_remote", lambda _repo: _REMOTE)

    with pytest.raises(AgentFallbackRuntimeError, match="valid JSON"):
        OperationalAgentFallbackRuntime(repo_dir=tmp_path, branch="issue-384", environ=environ)


def test_environment_allowlist_rejects_prompt_authority(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    environ = _environment()
    environ[PROVIDER_ENV_ALLOWLIST_ENV["codex"]] = '["HUNTER_PROMPT_AUTOMATION_SIGNING_KEY"]'
    monkeypatch.setattr(runtime_module, "_pinned_github_remote", lambda _repo: _REMOTE)

    with pytest.raises(AgentFallbackRuntimeError, match="cannot expose prompt authority"):
        OperationalAgentFallbackRuntime(repo_dir=tmp_path, branch="issue-384", environ=environ)
