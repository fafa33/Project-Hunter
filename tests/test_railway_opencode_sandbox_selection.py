from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hunter.automation import opencode_provider_runtime as runtime


def test_railway_runtime_selects_repository_permission_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(runtime._SANDBOX_EXECUTABLE_ENV, raising=False)
    monkeypatch.setenv(runtime._RAILWAY_ENV, "production")
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: None)

    assert runtime._sandbox_launcher() == [
        sys.executable,
        "-m",
        "hunter.automation.railway_opencode_permission_sandbox",
    ]


def test_explicit_sandbox_override_wins_on_railway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(runtime._RAILWAY_ENV, "production")
    monkeypatch.setenv(runtime._SANDBOX_EXECUTABLE_ENV, "custom-sandbox")
    monkeypatch.setattr(
        runtime.shutil,
        "which",
        lambda name: "/opt/custom/sandbox" if name == "custom-sandbox" else None,
    )

    assert runtime._sandbox_launcher() == ["/opt/custom/sandbox"]


def test_explicit_missing_sandbox_fails_closed_on_railway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(runtime._RAILWAY_ENV, "production")
    monkeypatch.setenv(runtime._SANDBOX_EXECUTABLE_ENV, "missing-sandbox")
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: None)

    with pytest.raises(runtime.ProviderAdapterError, match="filesystem sandbox executable is unavailable"):
        runtime._sandbox_launcher()


def test_non_railway_runtime_keeps_bubblewrap_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(runtime._SANDBOX_EXECUTABLE_ENV, raising=False)
    monkeypatch.delenv(runtime._RAILWAY_ENV, raising=False)
    monkeypatch.setattr(
        runtime.shutil,
        "which",
        lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
    )

    assert runtime._sandbox_launcher() == ["/usr/bin/bwrap"]


def test_railway_command_prefixes_bwrap_contract_with_python_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(runtime._SANDBOX_EXECUTABLE_ENV, raising=False)
    monkeypatch.setenv(runtime._RAILWAY_ENV, "production")
    sandbox = tmp_path / "hunter-opencode-attempt-1" / "repo"
    credential_home = sandbox.parent / "credential-home"
    sandbox.mkdir(parents=True)
    credential_home.mkdir()

    command = runtime._sandbox_command(
        "/app/bin/opencode",
        ["/app/bin/opencode", "run", "prompt"],
        sandbox,
        credential_home,
    )

    assert command[:3] == [
        sys.executable,
        "-m",
        "hunter.automation.railway_opencode_permission_sandbox",
    ]
    assert command[3:8] == [
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
    ]
    assert command[-2:] == ["run", "prompt"]
