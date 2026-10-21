"""Portable push launcher contract tests."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hunter_push.py"
spec = importlib.util.spec_from_file_location("hunter_push", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_missing_toolchain_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("HUNTER_PUSH_VENV", str(tmp_path / "absent"))
    with patch.object(module.subprocess, "call") as call:
        assert module.main(["origin", "HEAD"]) == 70
        call.assert_not_called()


def test_push_preserves_git_hooks_and_injects_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HUNTER_PUSH_VENV", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in module.TOOLS:
        (bindir / tool).touch()
    with patch.object(module.subprocess, "call", return_value=0) as call:
        assert module.main(["origin", "HEAD:refs/heads/test"]) == 0
    args, kwargs = call.call_args
    assert args[0] == ["git", "push", "origin", "HEAD:refs/heads/test"]
    assert kwargs["env"]["PATH"].startswith(str(bindir) + ":")
