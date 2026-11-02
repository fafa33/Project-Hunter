"""Focused fail-closed tests for the Codex executor invocation adapter."""

from pathlib import Path

import pytest

from hunter.automation import issue_agent_roles as roles


def test_codex_uses_sealed_stdin_private_home_and_workspace(tmp_path: Path) -> None:
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    prompt = b"private signed task\n"
    result = roles.codex_invocation(
        ("/opt/hunter/bin/codex", "exec", "--model", "gpt-6-luna"), prompt=prompt, home=home, workspace=workspace
    )
    assert result.stdin == prompt
    assert prompt.decode().strip() not in " ".join(result.argv)
    assert result.argv[-1] == "-"
    assert ("--sandbox", "workspace-write") == result.argv[4:6]
    assert not (home / ".codex").exists()  # invocation must not create owner-owned credential directories
    assert result.public_env["HOME"] == str(home)
    assert result.public_env["CODEX_HOME"] == str(home / ".codex")
    assert result.public_env["PWD"] == str(workspace)
    assert "--ephemeral" in result.argv
    assert "--ignore-user-config" in result.argv
    assert "sandbox_workspace_write.network_access=false" in result.argv


@pytest.mark.parametrize(
    "tail",
    [
        ("--dangerously-bypass-approvals-and-sandbox",),
        ("--add-dir", "/Users/owner"),
        ("--config", 'sandbox_mode="danger-full-access"'),
        ("--sandbox=read-only",),
        ("--cd", "/tmp"),
        ("ignore",),
        ("--model", "gpt-6-luna", "INJECTED"),
    ],
)
def test_codex_rejects_override_and_task_injection(tmp_path: Path, tail: tuple[str, ...]) -> None:
    with pytest.raises(roles.RoleRefused):
        roles.codex_invocation(("codex", "exec", *tail), prompt=b"task", home=tmp_path, workspace=tmp_path)


def test_codex_requires_explicit_model(tmp_path: Path) -> None:
    with pytest.raises(roles.RoleRefused):
        roles.codex_invocation(("codex", "exec"), prompt=b"task", home=tmp_path, workspace=tmp_path)


def test_pinned_dedicated_mac_codex_uses_isolated_login(tmp_path: Path) -> None:
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    result = roles.codex_invocation(
        ("/Users/hunterexecutor/.local/hunter-codex/node_modules/.bin/codex", "exec", "--model", "gpt-6-luna"),
        prompt=b"signed task",
        home=home,
        workspace=workspace,
    )
    assert result.public_env["CODEX_HOME"] == "/Users/hunterexecutor/.codex"
    assert result.public_env["HOME"] == str(home)
    assert "signed task" not in " ".join(result.argv)


def test_github_executor_stays_in_single_existing_lifecycle() -> None:
    workflow = Path(".github/workflows/hunter-issue-agent-trigger.yml").read_text()
    execute = workflow.split("\n  execute:\n", 1)[1].split("\n  bind:\n", 1)[0]
    assert "runs-on: [self-hosted, macOS, hunter-reviewer]" in execute
    assert "--model-argv /Users/hunterexecutor/.local/hunter-codex/node_modules/.bin/codex exec" in execute
    assert "HUNTER_ISSUE_AGENT_MAC_CODEX" in execute
    assert "HUNTER_ISSUE_AGENT_OPENCODE_API_KEY" not in execute
    assert "python3 scripts/hunter_issue_agent_lifecycle.py execute" in execute
