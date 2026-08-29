from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from hunter.automation.agent_fallback_runtime import (
    PROVIDER_COMMAND_ENV,
    AgentFallbackRuntimeError,
    OperationalAgentFallbackRuntime,
)
from hunter.automation.n8n_handoff import serialize_prompt_automation_handoff
from hunter.evidence_intelligence.smart_prompt_routing import _issue_prompt_automation_envelope

_SIGNING_KEY = "11" * 32
_VERIFYING_KEY = "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737"
_HEAD_A = "a" * 40
_HEAD_B = "b" * 40


def _environment() -> dict[str, str]:
    values = {
        "HUNTER_PROMPT_AUTOMATION_SIGNING_KEY": _SIGNING_KEY,
        "HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY": _VERIFYING_KEY,
        "HUNTER_AGENT_VALIDATION_COMMAND": '["validate-wrapper"]',
        "HUNTER_AGENT_ATTEMPT_TIMEOUT_SECONDS": "30",
    }
    for provider, name in PROVIDER_COMMAND_ENV.items():
        values[name] = f'["{provider}-wrapper"]'
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
    remote_heads = iter((_HEAD_A, _HEAD_A, _HEAD_B))

    def fake_run(argv: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = tuple(argv)
        if command[:3] == ("git", "ls-remote", "--exit-code"):
            head = next(remote_heads)
            return _completed(command, 0, stdout=f"{head}\trefs/heads/issue-384\n")
        if command == ("codex-wrapper",):
            provider_inputs.append(("codex", kwargs["input"]))
            return _completed(command, 75, stderr="rate limited")
        if command == ("claude-wrapper",):
            provider_inputs.append(("claude", kwargs["input"]))
            return _completed(command, 0)
        if command == ("validate-wrapper",):
            assert kwargs["env"]["HUNTER_AGENT_EXPECTED_HEAD"] == _HEAD_B
            return _completed(command, 0)
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr("hunter.automation.agent_fallback_runtime.subprocess.run", fake_run)
    receipt = OperationalAgentFallbackRuntime(
        repo_dir=tmp_path,
        branch="issue-384",
        environ=_environment(),
    ).dispatch(document)

    assert receipt.provider == "claude"
    assert receipt.head_before == _HEAD_A
    assert receipt.head_after == _HEAD_B
    assert provider_inputs == [("codex", document), ("claude", document)]
    assert [attempt["state"] for attempt in receipt.attempts] == ["rate_limited", "available"]


def test_runtime_requires_all_five_provider_adapters(tmp_path: Path) -> None:
    environ = _environment()
    del environ["HUNTER_AGENT_FREEBUFF_COMMAND"]

    with pytest.raises(AgentFallbackRuntimeError, match="HUNTER_AGENT_FREEBUFF_COMMAND"):
        OperationalAgentFallbackRuntime(repo_dir=tmp_path, branch="issue-384", environ=environ)


def test_provider_commands_are_argv_json_not_shell_text(tmp_path: Path) -> None:
    environ = _environment()
    environ["HUNTER_AGENT_OPENCODE_COMMAND"] = "opencode --run"

    with pytest.raises(AgentFallbackRuntimeError, match="valid JSON"):
        OperationalAgentFallbackRuntime(repo_dir=tmp_path, branch="issue-384", environ=environ)
