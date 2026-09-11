from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "railway_opencode_permission_sandbox.py"
SPEC = importlib.util.spec_from_file_location("railway_opencode_permission_sandbox", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
shim = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shim)


def _argv(tmp_path: Path) -> tuple[list[str], Path, Path]:
    root = tmp_path / "hunter-opencode-attempt-123"
    workspace = root / "repo"
    credential_home = root / "credential-home"
    workspace.mkdir(parents=True)
    credential_home.mkdir()
    argv = [
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        "/usr",
        "/usr",
        "--dir",
        "/opt/hunter/bin",
        "--ro-bind",
        "/app/bin/opencode",
        "/opt/hunter/bin/opencode",
        "--bind",
        str(workspace),
        "/workspace",
        "--bind",
        str(credential_home),
        "/home/hunter",
        "--chdir",
        "/workspace",
        "/opt/hunter/bin/opencode",
        "run",
        "prompt",
    ]
    return argv, workspace, credential_home


def test_parse_accepts_exact_hunter_contract(tmp_path: Path) -> None:
    argv, workspace, credential_home = _argv(tmp_path)

    parsed_workspace, parsed_home, executable, provider_args = shim._parse(argv)

    assert parsed_workspace == workspace.resolve()
    assert parsed_home == credential_home.resolve()
    assert executable == "/app/bin/opencode"
    assert provider_args == ["run", "prompt"]


def test_parse_rejects_unknown_option(tmp_path: Path) -> None:
    argv, _, _ = _argv(tmp_path)
    argv.insert(0, "--share-net")

    with pytest.raises(shim.SandboxShimError, match="unsupported sandbox option"):
        shim._parse(argv)


def test_parse_rejects_workspace_outside_attempt_root(tmp_path: Path) -> None:
    argv, workspace, _ = _argv(tmp_path)
    outside = tmp_path / "repo"
    outside.mkdir()
    bind_index = argv.index(str(workspace))
    argv[bind_index] = str(outside)

    with pytest.raises(shim.SandboxShimError, match="workspace is outside"):
        shim._parse(argv)


def test_restricted_environment_strips_authority_and_publication_credentials(tmp_path: Path, monkeypatch) -> None:
    credential_home = tmp_path / "credential-home"
    credential_home.mkdir()
    monkeypatch.setenv("HUNTER_AGENT_GITHUB_PUSH_TOKEN", "secret")
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret")

    env = shim._restricted_environment(credential_home)

    assert "HUNTER_AGENT_GITHUB_PUSH_TOKEN" not in env
    assert "HUNTER_PROMPT_AUTOMATION_SIGNING_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert env["OPENAI_API_KEY"] == "provider-secret"
    assert env["HOME"] == str(credential_home)
    assert env["OPENCODE_DISABLE_CLAUDE_CODE"] == "1"
    assert env["OPENCODE_AUTO_SHARE"] == "false"


def test_inline_permissions_allow_only_project_editing_capabilities(tmp_path: Path) -> None:
    credential_home = tmp_path / "credential-home"
    credential_home.mkdir()

    env = shim._restricted_environment(credential_home)
    permission = json.loads(env["OPENCODE_CONFIG_CONTENT"])["permission"]

    assert permission["*"] == "deny"
    assert permission["read"] == "allow"
    assert permission["edit"] == "allow"
    assert permission["glob"] == "allow"
    assert permission["grep"] == "allow"
    assert permission["lsp"] == "allow"
    for denied in ("bash", "external_directory", "webfetch", "websearch", "task", "skill", "question"):
        assert permission.get(denied, "deny") == "deny"
