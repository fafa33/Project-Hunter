"""Startup self-check of the governed OpenCode provider (contract I8).

The decision logic is proven with an injected provider run. When the pinned
OpenCode runtime is available (``HUNTER_TEST_OPENCODE_EXECUTABLE``), the same
probe is also driven against the real binary and a local stand-in model that
always requests a shell tool call, so the rejection is proven on the real
permission engine rather than assumed from configuration.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from hunter.automation import opencode_provider_self_check as self_check
from hunter.automation import railway_opencode_permission_sandbox as shim


@pytest.fixture
def executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "opencode"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("HUNTER_OPENCODE_MODEL", "opencode/probe-model")
    monkeypatch.setenv("HUNTER_AGENT_GITHUB_PUSH_TOKEN", "publication-secret")
    return fake


def _runner(action: str | None = None, returncode: int = 0) -> Any:
    seen: dict[str, Any] = {}

    def run(command: list[str], env: dict[str, str], cwd: Path) -> int:
        seen.update(command=command, env=env, cwd=cwd)
        executed = f"{self_check.PROBE_MARKER}-{env[self_check.PROBE_NONCE_ENV]}"
        if action == "inside":
            (cwd / executed).touch()
        elif action == "nested":
            (cwd / "deep").mkdir()
            (cwd / "deep" / executed).touch()
        elif action == "outside":
            (cwd.parent / executed).touch()
        elif action == "forged":
            # What a file-writing tool could produce: the command text, unexpanded.
            (cwd / f"{self_check.PROBE_MARKER}-${self_check.PROBE_NONCE_ENV}").touch()
            (cwd / self_check.PROBE_MARKER).touch()
        return returncode

    run.seen = seen  # type: ignore[attr-defined]
    return run


def test_a_rejected_shell_attempt_passes(executable: Path) -> None:
    runner = _runner()
    self_check.run_self_check(run_provider=runner)

    seen = runner.seen
    command = seen["command"]
    # The probe goes through the governed sandbox launcher, with the configured
    # model and the probe prompt, exactly as a real execution would.
    assert command[1:3] == ["-m", "hunter.automation.railway_opencode_permission_sandbox"]
    assert command[-4:] == ["run", "--model", "opencode/probe-model", self_check.PROBE_PROMPT]
    assert seen["cwd"].name == "repo" and seen["cwd"].parent.name.startswith("hunter-opencode-attempt-")
    assert "HUNTER_AGENT_GITHUB_PUSH_TOKEN" not in seen["env"]


def test_a_forged_marker_without_the_hidden_nonce_is_not_shell_execution(executable: Path) -> None:
    runner = _runner("forged")
    self_check.run_self_check(run_provider=runner)
    assert runner.seen["env"][self_check.PROBE_NONCE_ENV] not in self_check.PROBE_PROMPT


@pytest.mark.parametrize("where", ["inside", "nested", "outside"])
def test_an_executed_shell_command_fails_closed(executable: Path, where: str) -> None:
    with pytest.raises(self_check.ProviderSelfCheckError, match="executed a forbidden shell command"):
        self_check.run_self_check(run_provider=_runner(where))


def test_an_executed_shell_command_fails_even_when_the_provider_reports_failure(executable: Path) -> None:
    with pytest.raises(self_check.ProviderSelfCheckError, match="executed a forbidden shell command"):
        self_check.run_self_check(run_provider=_runner("inside", returncode=1))


@pytest.mark.parametrize(
    ("returncode", "reason"),
    [(75, "rate-limited"), (1, "exit status 1"), (2, "exit status 2")],
)
def test_an_unprovable_probe_fails_closed(executable: Path, returncode: int, reason: str) -> None:
    with pytest.raises(self_check.ProviderSelfCheckError, match=reason):
        self_check.run_self_check(run_provider=_runner(returncode=returncode))


def test_a_missing_runtime_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("HUNTER_OPENCODE_EXECUTABLE", "opencode")
    with pytest.raises(self_check.ProviderSelfCheckError, match="unavailable"):
        self_check.run_self_check(run_provider=_runner())


def test_the_probe_workspace_is_disposable(executable: Path) -> None:
    runner = _runner()
    self_check.run_self_check(run_provider=runner)
    assert not runner.seen["cwd"].exists()


# --- the real pinned runtime against a stand-in model --------------------------


class _ShellRequestingModel:
    """An OpenAI-compatible stand-in that always requests one shell tool call."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        model = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length) or b"{}")
                model.requests.append(request)
                answered = any(message.get("role") == "tool" for message in request.get("messages", []))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                base = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "probe"}

                def chunk(delta: dict[str, Any], finish: str | None) -> None:
                    payload = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")

                if answered:
                    chunk({"role": "assistant", "content": self_check.PROBE_DONE}, None)
                    chunk({}, "stop")
                else:
                    arguments = json.dumps({"command": self_check.PROBE_COMMAND, "description": "probe"})
                    call = {"index": 0, "id": "call_1", "type": "function"}
                    call["function"] = {"name": "bash", "arguments": arguments}
                    chunk({"role": "assistant", "tool_calls": [call]}, None)
                    chunk({}, "tool_calls")
                self.wfile.write(b"data: [DONE]\n\n")

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = int(probe.getsockname()[1])
        self.server = HTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


_PINNED = os.environ.get("HUNTER_TEST_OPENCODE_EXECUTABLE", "")


@pytest.mark.skipif(not _PINNED, reason="the pinned OpenCode runtime is not available in this environment")
@pytest.mark.parametrize(("bash_decision", "executed"), [("contract", False), ("allow", True)])
def test_the_pinned_runtime_rejects_a_real_shell_tool_call_under_the_hunter_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bash_decision: str, executed: bool
) -> None:
    model = _ShellRequestingModel()
    try:
        root = tmp_path / "hunter-opencode-attempt-real"
        root.mkdir()
        workspace, credential_home = self_check._probe_workspace(root)
        env = shim._restricted_environment(credential_home)
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        if bash_decision == "allow":
            config["permission"]["bash"] = "allow"
        config["provider"] = {
            "probe": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "probe",
                "options": {"baseURL": f"http://127.0.0.1:{model.port}/v1", "apiKey": "probe"},
                "models": {"probe": {"name": "probe", "tool_call": True}},
            }
        }
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
        env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
        env[self_check.PROBE_NONCE_ENV] = "0123456789abcdef"
        env["PWD"] = str(workspace)  # exactly as the sandbox shim binds it
        subprocess.run(
            (_PINNED, "--pure", "run", "--model", "probe/probe", self_check.PROBE_PROMPT),
            cwd=workspace,
            env=env,
            check=False,
            timeout=300,
            capture_output=True,
        )
        offered = {tool["function"]["name"] for request in model.requests for tool in request.get("tools", [])}
        assert ("bash" in offered) is executed
        assert any(root.rglob(f"{self_check.PROBE_MARKER}-0123456789abcdef")) is executed
    finally:
        model.close()
