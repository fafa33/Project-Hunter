"""Fail-closed executor ownership targets before any sudo invocation."""

from pathlib import Path

import pytest

from hunter.automation import issue_agent_roles as roles


def test_executor_rejects_out_of_scope_ownership_without_sudo(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(roles.core, "require_isolation_user", lambda user: user)
    monkeypatch.setattr(roles.core, "run_privileged", lambda *args: calls.append(args))
    isolation = roles.SudoIsolation("hunterexecutor")
    root = tmp_path / "hunter-ia.invalid"
    (root / "workspace").mkdir(parents=True)
    (root / "home").mkdir()
    with pytest.raises(roles.RoleRefused, match="workroot"):
        isolation.prepare(root / "workspace", root / "home")
    with pytest.raises(roles.RoleRefused, match="ownership"):
        isolation.prepare(Path("/etc"), Path("/tmp"))
    assert calls == []


def test_executor_rejects_symlink_workspace_without_sudo(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(roles.core, "require_isolation_user", lambda user: user)
    monkeypatch.setattr(roles.core, "run_privileged", lambda *args: calls.append(args))
    isolation = roles.SudoIsolation("hunterexecutor")
    root = Path("/private/tmp/hunter-ia.test-scope")
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "workspace")
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    with pytest.raises(roles.RoleRefused, match="workroot"):
        isolation.prepare(root / "workspace", root / "home")
    assert calls == []
