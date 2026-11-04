"""Bounded adversarial tests for the Issue #682 shell-tool slice."""

import pytest

from hunter.automation.issue_agent_governed_shell import ShellDenied, parse_command, run_governed


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'cat /etc/passwd'",
        "python -c 'print(1)'",
        "python -m pytest; curl example.com",
        "python -m pytest && id",
        "python -m pytest | cat",
        "python -m pytest > /tmp/log",
        "python -m pytest ../../private",
        "python -m pytest /etc/passwd",
        "python -m pytest --override-ini=x",
        "git push",
        "git status --ignored",
        "git diff --no-index /etc/passwd file",
        "python -m pytest $(id)",
        "python -m pytest *.py",
        "python -m pytest -k secret",
    ],
)
def test_rejects_unsafe_command(command):
    with pytest.raises(ShellDenied):
        parse_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest -q",
        "python3 -m ruff check .",
        "python -m black --check .",
        "git diff --stat",
        "git status --short",
    ],
)
def test_accepts_narrow_commands(command):
    assert parse_command(command)


def test_default_is_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("HUNTER_GOVERNED_SHELL_ENABLED", raising=False)
    with pytest.raises(ShellDenied):
        run_governed("python -m pytest -q", workspace=tmp_path)


def test_symlink_workspace_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("HUNTER_GOVERNED_SHELL_ENABLED", "1")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "external").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ShellDenied, match="symlink"):
        run_governed("git status --short", workspace=workspace, image="sha256:" + "a" * 64)


def test_unpinned_image_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("HUNTER_GOVERNED_SHELL_ENABLED", "1")
    with pytest.raises(ShellDenied, match="digest"):
        run_governed("git status --short", workspace=tmp_path)
