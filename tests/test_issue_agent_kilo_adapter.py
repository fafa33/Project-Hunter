"""Fail-closed checks for the local, staging-only Kilo adapter."""

import json
import stat
from pathlib import Path

import pytest

from hunter.automation import issue_agent_roles as roles

MODEL = "kilo/nvidia/nemotron-3-ultra-550b-a55b:free"


def test_kilo_private_task_and_workspace(tmp_path: Path) -> None:
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    secret = b"PRIVATE_SIGNED_TASK_DO_NOT_LOG"
    result = roles.kilo_invocation(
        ("/opt/hunter/bin/kilo", "run", "--model", MODEL),
        prompt=secret,
        home=home,
        workspace=workspace,
    )
    assert result.stdin == b""
    assert secret.decode() not in " ".join(result.argv)
    assert result.argv[:2] == ("/opt/hunter/bin/kilo", "run")
    assert result.argv[2:4] == ("--dir", str(workspace))
    assert result.argv[4:6] == ("--model", MODEL)
    assert "--auto" not in result.argv
    assert result.argv[-2] == "--file"
    attachment = Path(result.argv[-1])
    assert attachment.parent == home
    assert attachment.read_bytes() == secret
    assert stat.S_IMODE(attachment.stat().st_mode) == 0o600
    assert result.public_env["HOME"] == str(home)
    assert result.public_env["PWD"] == str(workspace)
    assert result.public_env["XDG_CONFIG_HOME"] == str(home / ".config")
    assert result.public_env["KILO_DISABLE_PROJECT_CONFIG"] == "1"
    assert result.public_env["KILO_CONFIG_DIR"] == result.public_env["OPENCODE_CONFIG_DIR"]
    config = json.loads(result.public_env["KILO_CONFIG_CONTENT"])
    assert config["permission"]["*"] == "deny"
    assert config["permission"]["external_directory"] == "deny"
    assert config["agent"]["build"]["permission"] == config["permission"]
    assert result.public_env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    shell = Path(config["shell"])
    assert shell.is_file()
    assert stat.S_IMODE(shell.stat().st_mode) == 0o555
    assert "exit 126" in shell.read_text()
    assert result.public_env["SHELL"] == str(shell)


@pytest.mark.parametrize(
    "tail",
    [
        (),
        ("--auto",),
        ("--model", "m", "--auto"),
        ("--model", "m", "INJECTED"),
        ("--dir", "/tmp"),
        ("--model", ""),
        ("--model", "--auto"),
    ],
)
def test_kilo_rejects_unapproved_args(tmp_path: Path, tail: tuple[str, ...]) -> None:
    with pytest.raises(roles.RoleRefused):
        roles.kilo_invocation(
            ("kilo", "run", *tail),
            prompt=b"task",
            home=tmp_path,
            workspace=tmp_path,
        )


def test_kilo_does_not_use_production_dispatch_yet() -> None:
    """Document that this test suite does not prove production wiring."""
    assert callable(roles.kilo_invocation)
