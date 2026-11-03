"""The macOS Codex lane must invoke the fixed helper, never unrestricted chown."""

from pathlib import Path

from hunter.automation import issue_agent_roles as roles


def test_executor_uses_fixed_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(roles.core, "require_isolation_user", lambda user: user)
    monkeypatch.setattr(roles.core, "run_privileged", lambda *args: calls.append(args))
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    isolation = roles.SudoIsolation("hunterexecutor")
    root = Path("/private/tmp/hunter-ia.testscope")
    isolation.prepare(root / "workspace", root / "home")
    assert calls == [
        (
            "/usr/bin/python3",
            "/usr/local/libexec/hunter/hunter_mac_workspace_owner.py",
            "prepare",
            str(root),
        )
    ]
