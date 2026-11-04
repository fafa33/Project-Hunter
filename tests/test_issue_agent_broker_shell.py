import sys

from scripts.issue_agent import broker_shell


def test_missing_broker_fails_closed(monkeypatch):
    monkeypatch.delenv("HUNTER_SHELL_BROKER_SOCKET", raising=False)
    monkeypatch.setattr(sys, "argv", ["broker_shell", "-c", "git status"])
    assert broker_shell.main() == 126


def test_broker_receives_shell_command(monkeypatch):
    observed = []
    monkeypatch.setenv("HUNTER_SHELL_BROKER_SOCKET", "/tmp/agent.sock")
    monkeypatch.setattr(sys, "argv", ["broker_shell", "-c", "python -m pytest -q"])
    monkeypatch.setattr(broker_shell, "run_client", lambda path, command: observed.append((path, command)) or 0)
    assert broker_shell.main() == 0
    assert observed[0][1] == "python -m pytest -q"


def test_unsupported_invocation_fails_closed(monkeypatch):
    monkeypatch.setenv("HUNTER_SHELL_BROKER_SOCKET", "/tmp/agent.sock")
    monkeypatch.setattr(sys, "argv", ["broker_shell", "git", "status"])
    assert broker_shell.main() == 126
