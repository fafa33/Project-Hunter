import sys
from importlib import import_module
from pathlib import Path

# The preflight uses the pytest console entrypoint, which does not add the
# checkout root to sys.path. Resolve only this checkout, not a global path.

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

broker_shell = import_module("scripts.issue_agent.broker_shell")


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
