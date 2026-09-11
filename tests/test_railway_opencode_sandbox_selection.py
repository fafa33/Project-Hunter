from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import railway_issuer_startup as startup

ROOT = Path(__file__).resolve().parent.parent


def test_startup_selects_repository_owned_railway_sandbox_by_default() -> None:
    with patch.dict(os.environ, {}, clear=True):
        startup._ensure_opencode_sandbox_executable()

        assert (
            os.environ["HUNTER_OPENCODE_SANDBOX_EXECUTABLE"]
            == "/app/bin/hunter-railway-opencode-sandbox"
        )


def test_startup_preserves_explicit_sandbox_override() -> None:
    with patch.dict(
        os.environ,
        {"HUNTER_OPENCODE_SANDBOX_EXECUTABLE": "/custom/sandbox"},
        clear=True,
    ):
        startup._ensure_opencode_sandbox_executable()

        assert os.environ["HUNTER_OPENCODE_SANDBOX_EXECUTABLE"] == "/custom/sandbox"


def test_railway_build_installs_executable_sandbox_launcher() -> None:
    config = (ROOT / "railway.toml").read_text(encoding="utf-8")

    assert (
        "install -m 0755 scripts/hunter_railway_opencode_sandbox "
        "/app/bin/hunter-railway-opencode-sandbox" in config
    )


def test_launcher_executes_only_the_permission_sandbox_module() -> None:
    launcher = (ROOT / "scripts/hunter_railway_opencode_sandbox").read_text(
        encoding="utf-8"
    )

    assert "exec python -m hunter.automation.railway_opencode_permission_sandbox \"$@\"" in launcher
    assert "HUNTER_RUNTIME_VENDOR_DIR:-/app/vendor" in launcher
