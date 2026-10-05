"""AT-46: the retired parallel Issue-agent authorities fail closed (ADR 0037 D9, FMEA F-60)."""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

import hunter.automation.agent_fallback_runtime as fallback_runtime
import hunter.automation.n8n as n8n
import hunter.automation.n8n_canary as n8n_canary
import hunter.automation.opencode_provider_runtime as opencode
import hunter.automation.retired_issue_agent_authority as guard
from hunter.__main__ import main as hunter_main

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("verb", ["agent-fallback-run", "n8n-canary"])
def test_the_cli_refuses_the_retired_verbs(verb: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert hunter_main([verb, "--handoff", "/nonexistent", "--repo", ".", "--branch", "x"]) == 2
    assert "retired (ADR 0037 D9)" in capsys.readouterr().err


@pytest.mark.parametrize("verb", ["agent-fallback-run", "n8n-canary"])
def test_no_environment_re_enables_a_retired_verb(verb: str, tmp_path: Path) -> None:
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(ROOT / "src"),
        "HUNTER_AGENT_FALLBACK_ENABLED": "1",
        "HUNTER_N8N_WEBHOOK_URL": "https://n8n.example.test/webhook",
        "HUNTER_RETIRED_AUTHORITY_OVERRIDE": "1",
    }
    handoff = tmp_path / "handoff.json"
    handoff.write_text("{}", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "hunter", verb, "--handoff", str(handoff)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "retired (ADR 0037 D9)" in completed.stderr


def test_the_fallback_runtime_refuses_before_any_work() -> None:
    runtime = object.__new__(fallback_runtime.OperationalAgentFallbackRuntime)  # no settings, no repository
    with pytest.raises(guard.RetiredAuthorityError):
        runtime.dispatch(b"{}")


def test_the_n8n_transport_refuses_before_any_request() -> None:
    transport = object.__new__(n8n.N8nPromptAutomationTransport)
    with pytest.raises(guard.RetiredAuthorityError):
        transport.deliver({})


def test_the_n8n_canary_refuses() -> None:
    with pytest.raises(guard.RetiredAuthorityError):
        n8n_canary.run_n8n_canary("{}", environ={})


def test_the_opencode_runtime_refuses_to_run_or_push(tmp_path: Path) -> None:
    with pytest.raises(guard.RetiredAuthorityError):
        opencode.run("{}")
    with pytest.raises(guard.RetiredAuthorityError):
        opencode._push_trusted(tmp_path, "issue-1", "https://example.invalid/r.git", "")


@pytest.mark.parametrize(
    ("function", "name"),
    [
        (
            fallback_runtime.OperationalAgentFallbackRuntime.dispatch,
            "agent_fallback_runtime.OperationalAgentFallbackRuntime",
        ),
        (n8n.N8nPromptAutomationTransport.deliver, "n8n.N8nPromptAutomationTransport"),
        (n8n_canary.run_n8n_canary, "n8n-canary"),
        (opencode.run, "opencode_provider_runtime"),
        (opencode._push_trusted, "opencode_provider_runtime"),
    ],
)
def test_the_refusal_is_the_first_statement(function: object, name: str) -> None:
    """Nothing (no read, no network, no subprocess) precedes the refusal in a retired entry point."""
    tree = ast.parse(inspect.cleandoc("\n" + inspect.getsource(function)))  # type: ignore[arg-type]
    body = tree.body[0].body  # type: ignore[attr-defined]
    statements = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    first = statements[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
    assert isinstance(first.value.func, ast.Name) and first.value.func.id == "refuse_retired_authority"
    assert [a.value for a in first.value.args if isinstance(a, ast.Constant)] == [name]


def test_the_guard_has_no_configuration_surface() -> None:
    tree = ast.parse(Path(guard.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name if isinstance(node, ast.Import) else node.module
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported <= {"__future__", "typing"}


def test_an_unknown_name_is_not_a_silent_refusal() -> None:
    with pytest.raises(ValueError):
        guard.refuse_retired_authority("issue_agent_roles")
