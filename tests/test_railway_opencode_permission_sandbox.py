from __future__ import annotations

import json
from pathlib import Path

import pytest

from hunter.automation import railway_opencode_permission_sandbox as shim


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


def _provider_sequence(
    calls: list[list[str]],
    *,
    tools: dict[str, bool] | None = None,
    compatibility_returncode: int = 0,
):
    resolved_tools = tools or {"read": True, "edit": True, "glob": True, "grep": True}

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[1:] == ["--version"]:
            return shim.subprocess.CompletedProcess(command, 0, stdout="1.18.30\n", stderr="")
        if command[1:4] == ["debug", "agent", "build"]:
            return shim.subprocess.CompletedProcess(command, 0, stdout=json.dumps({"tools": resolved_tools}), stderr="")
        if shim._PROVIDER_COMPATIBILITY_PROMPT in command:
            if compatibility_returncode:
                return shim.subprocess.CompletedProcess(
                    command,
                    compatibility_returncode,
                    stdout="",
                    stderr="unsupported request option: prompt_cache_key",
                )
            return shim.subprocess.CompletedProcess(
                command, 0, stdout=shim._PROVIDER_COMPATIBILITY_SENTINEL + "\n", stderr=""
            )
        if command[-1] == "prompt":
            return shim.subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        pytest.fail(f"unexpected provider command: {command}")

    return fake_run


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


def test_inline_permissions_allow_only_project_editing_capabilities(
    tmp_path: Path,
) -> None:
    credential_home = tmp_path / "credential-home"
    credential_home.mkdir()

    env = shim._restricted_environment(credential_home)
    permission = json.loads(env["OPENCODE_CONFIG_CONTENT"])["permission"]

    assert permission["*"] == "deny"
    assert permission["external_directory"] == "deny"
    assert permission["read"] == "allow"
    assert permission["edit"] == "allow"
    assert permission["glob"] == "allow"
    assert permission["grep"] == "allow"
    assert permission["lsp"] == "allow"
    for denied in (
        "webfetch",
        "websearch",
        "task",
        "skill",
        "question",
    ):
        assert permission.get(denied, "deny") == "deny"


def test_provider_capability_contract_is_checked_before_execution(tmp_path: Path, monkeypatch) -> None:
    credential_home = tmp_path / "credential-home"
    credential_home.mkdir()
    broken = {
        "permission": {
            **shim._PERMISSION_CONFIG["permission"],
            "edit": "deny",
        }
    }
    monkeypatch.setattr(shim, "_PERMISSION_CONFIG", broken)

    with pytest.raises(shim.SandboxShimError, match="provider capability mismatch: missing edit"):
        shim._restricted_environment(credential_home)


def test_provider_runtime_instructions_make_shell_validation_parent_owned(tmp_path: Path) -> None:
    credential_home = tmp_path / "credential-home"
    credential_home.mkdir()

    env = shim._restricted_environment(credential_home)
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    instruction_paths = config["instructions"]

    assert len(instruction_paths) == 1
    instruction_path = Path(instruction_paths[0])
    assert instruction_path.is_relative_to(credential_home.resolve())
    instructions = instruction_path.read_text(encoding="utf-8")
    assert "Do not attempt shell or test commands" in instructions
    assert "trusted parent owns exact-head targeted validation after publication" in instructions


def test_provider_capability_contract_keeps_shell_and_external_directory_denied(tmp_path: Path) -> None:
    credential_home = tmp_path / "credential-home"
    credential_home.mkdir()

    env = shim._restricted_environment(credential_home)
    permission = json.loads(env["OPENCODE_CONFIG_CONTENT"])["permission"]

    assert permission["external_directory"] == "deny"
    assert permission["bash"] == "allow"
    assert set(shim._REQUIRED_PROVIDER_CAPABILITIES) <= {
        name for name, decision in permission.items() if decision == "allow"
    }
    plugin = credential_home / ".config" / "opencode" / "plugins" / shim._PROVIDER_GUARD_PLUGIN_FILE
    assert plugin.is_file()
    assert '"bash"' in plugin.read_text(encoding="utf-8")
    assert "forbids tool" in plugin.read_text(encoding="utf-8")


def test_main_fails_closed_before_provider_run_when_runtime_capability_is_missing(tmp_path: Path, monkeypatch) -> None:
    argv, _, _ = _argv(tmp_path)
    calls: list[list[str]] = []
    tools = {"read": True, "edit": False, "glob": True, "grep": True}
    monkeypatch.setattr(shim.subprocess, "run", _provider_sequence(calls, tools=tools))

    assert shim.main(argv) == 1
    assert calls == [
        ["/app/bin/opencode", "--version"],
        ["/app/bin/opencode", "debug", "agent", "build", "--pure"],
    ]


def test_pinned_runtime_version_is_checked_before_provider_execution(tmp_path: Path, monkeypatch) -> None:
    credential_home = tmp_path / "credential-home"
    credential_home.mkdir()
    env = shim._restricted_environment(credential_home)

    def fake_run(command, **kwargs):
        return shim.subprocess.CompletedProcess(command, 0, stdout="1.18.29\n", stderr="")

    monkeypatch.setattr(shim.subprocess, "run", fake_run)

    with pytest.raises(shim.SandboxShimError, match="provider runtime version mismatch"):
        shim._validate_pinned_runtime("/app/bin/opencode", env)


def test_provider_compatibility_probe_uses_exact_model_and_fails_closed_on_rejected_option(
    tmp_path: Path, monkeypatch
) -> None:
    credential_home = tmp_path / "credential-home"
    credential_home.mkdir()
    env = shim._restricted_environment(credential_home)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        return shim.subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="unsupported request option: prompt_cache_key",
        )

    monkeypatch.setattr(shim.subprocess, "run", fake_run)

    with pytest.raises(shim.SandboxShimError, match="provider compatibility probe failed"):
        shim._validate_provider_compatibility(
            "/app/bin/opencode",
            ["run", "--model", "openai/gpt-test", "real prompt"],
            env,
        )

    assert calls == [
        [
            "/app/bin/opencode",
            "--pure",
            "run",
            "--model",
            "openai/gpt-test",
            shim._PROVIDER_COMPATIBILITY_PROMPT,
        ]
    ]


def test_main_runs_version_capability_and_provider_compatibility_before_real_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    argv, _, _ = _argv(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(shim.subprocess, "run", _provider_sequence(calls))

    assert shim.main(argv) == 0
    assert calls[-1][-1] == "prompt"
    assert shim._PROVIDER_COMPATIBILITY_PROMPT in calls[-2]


def test_main_preserves_rate_limit_exit_from_provider_compatibility_probe(tmp_path: Path, monkeypatch) -> None:
    argv, _, _ = _argv(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        shim.subprocess,
        "run",
        _provider_sequence(calls, compatibility_returncode=shim._RATE_LIMIT_EXIT_CODE),
    )

    assert shim.main(argv) == shim._RATE_LIMIT_EXIT_CODE
    assert all(command[-1] != "prompt" for command in calls)


def test_main_fails_before_real_prompt_when_provider_compatibility_probe_fails(tmp_path: Path, monkeypatch) -> None:
    argv, _, _ = _argv(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        shim.subprocess,
        "run",
        _provider_sequence(calls, compatibility_returncode=1),
    )

    assert shim.main(argv) == 1
    assert all(command[-1] != "prompt" for command in calls)


def test_runtime_contract_matches_canonical_installer_pin() -> None:
    installer = Path("scripts/install_opencode_runtime.py").read_text(encoding="utf-8")
    assert f'OPENCODE_VERSION = "{shim._PINNED_OPENCODE_VERSION}"' in installer
