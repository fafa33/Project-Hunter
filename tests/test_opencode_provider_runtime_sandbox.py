from __future__ import annotations

from pathlib import Path

from hunter.automation import opencode_provider_runtime as runtime


def _sandbox(tmp_path: Path, name: str = "repo") -> Path:
    return tmp_path / name


def test_executable_outside_system_sandbox_gets_read_only_bind(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    credential_home = tmp_path / "credential-home"
    in_sandbox, mounts = runtime._sandbox_executable_path("/app/bin/opencode", sandbox, credential_home)

    assert in_sandbox == "/opt/hunter/bin/opencode"
    assert mounts == ["--dir", "/opt/hunter/bin", "--ro-bind", "/app/bin/opencode", in_sandbox]


def test_executable_inside_system_path_is_reused_unmounted(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    credential_home = tmp_path / "credential-home"
    in_sandbox, mounts = runtime._sandbox_executable_path("/usr/bin/opencode", sandbox, credential_home)

    assert in_sandbox == "/usr/bin/opencode"
    assert mounts == []


def test_executable_inside_sandbox_workspace_is_remapped(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    credential_home = tmp_path / "credential-home"
    in_sandbox, mounts = runtime._sandbox_executable_path(str(sandbox / "bin" / "opencode"), sandbox, credential_home)

    assert in_sandbox == "/workspace/bin/opencode"
    assert mounts == []


def test_executable_inside_credential_home_is_remapped(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    credential_home = tmp_path / "credential-home"
    in_sandbox, mounts = runtime._sandbox_executable_path(
        str(credential_home / ".local" / "bin" / "opencode"),
        sandbox,
        credential_home,
    )

    assert in_sandbox == "/home/hunter/.local/bin/opencode"
    assert mounts == []


def test_sandbox_command_uses_in_sandbox_executable_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(runtime._SANDBOX_EXECUTABLE_ENV, "/usr/bin/true")
    sandbox = _sandbox(tmp_path)
    credential_home = tmp_path / "credential-home"
    command = runtime._sandbox_command(
        "/app/bin/opencode",
        ["/app/bin/opencode", "run", "--model", "m"],
        sandbox,
        credential_home,
    )

    assert command[0] == "/usr/bin/true"
    assert command[-3:] == ["run", "--model", "m"]
    assert command[-4] == "/opt/hunter/bin/opencode"
    source_index = command.index("/app/bin/opencode")
    assert command[source_index - 3 : source_index + 1] == [
        "--dir",
        "/opt/hunter/bin",
        "--ro-bind",
        "/app/bin/opencode",
    ]
    assert command[source_index + 1] == "/opt/hunter/bin/opencode"
