"""Railway-compatible fail-closed OpenCode sandbox shim.

Railway containers are non-privileged, so bubblewrap cannot create the
namespaces used by the default provider sandbox. This module accepts only the
exact bwrap-shaped argv emitted by Hunter, maps the isolated workspace/home
back to their trusted host paths, and executes OpenCode in pure mode with a
strict inline permission policy. Shell, external-directory, web, task, skill,
and question actions remain denied; only project read/edit/search/LSP actions
are allowed. Publication credentials stay outside the provider process and the
trusted parent still owns commit signing, push, and exact-head validation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REQUIRED_FLAGS = {
    "--die-with-parent",
    "--new-session",
    "--unshare-pid",
    "--unshare-ipc",
    "--unshare-uts",
}
_REQUIRED_VALUE_OPTIONS = {"--proc": "/proc", "--dev": "/dev", "--tmpfs": "/tmp"}
_ALLOWED_SYSTEM_RO_BINDS = {"/usr", "/bin", "/lib", "/lib64", "/etc"}
_FORBIDDEN_ENV = {
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "SSH_AUTH_SOCK",
    "HUNTER_AGENT_GITHUB_PUSH_TOKEN",
    "HUNTER_ISSUE_AGENT_EVIDENCE_DB",
    "HUNTER_ISSUE_AGENT_REPOSITORY",
    "HUNTER_ISSUE_AGENT_REPO_DIR",
}
_PERMISSION_CONFIG = {
    "permission": {
        "*": "deny",
        "read": "allow",
        "edit": "allow",
        "glob": "allow",
        "grep": "allow",
        "lsp": "allow",
    }
}


class SandboxShimError(RuntimeError):
    """Raised when the parent sandbox contract is not exactly recognized."""


def _parse(argv: list[str]) -> tuple[Path, Path, str, list[str]]:
    flags: set[str] = set()
    value_options: dict[str, str] = {}
    ro_binds: dict[str, str] = {}
    binds: dict[str, str] = {}
    chdir: str | None = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in _REQUIRED_FLAGS:
            flags.add(token)
            index += 1
            continue
        if token in _REQUIRED_VALUE_OPTIONS:
            if index + 1 >= len(argv):
                raise SandboxShimError(f"missing value for {token}")
            value_options[token] = argv[index + 1]
            index += 2
            continue
        if token in {"--ro-bind", "--bind"}:
            if index + 2 >= len(argv):
                raise SandboxShimError(f"incomplete {token}")
            source, target = argv[index + 1 : index + 3]
            (ro_binds if token == "--ro-bind" else binds)[target] = source
            index += 3
            continue
        if token == "--dir":
            if index + 1 >= len(argv):
                raise SandboxShimError("incomplete --dir")
            index += 2
            continue
        if token == "--chdir":
            if index + 1 >= len(argv):
                raise SandboxShimError("incomplete --chdir")
            chdir = argv[index + 1]
            index += 2
            break
        raise SandboxShimError(f"unsupported sandbox option: {token}")

    if flags != _REQUIRED_FLAGS:
        raise SandboxShimError("required namespace flags are missing")
    if value_options != _REQUIRED_VALUE_OPTIONS:
        raise SandboxShimError("required proc/dev/tmpfs contract is missing")
    if chdir != "/workspace":
        raise SandboxShimError("sandbox chdir must be /workspace")
    if "/workspace" not in binds or "/home/hunter" not in binds:
        raise SandboxShimError("workspace and credential-home binds are required")

    workspace = Path(binds["/workspace"]).resolve()
    credential_home = Path(binds["/home/hunter"]).resolve()
    if workspace.name != "repo" or not workspace.parent.name.startswith(
        "hunter-opencode-attempt-"
    ):
        raise SandboxShimError("workspace is outside the expected isolated attempt root")
    if credential_home != workspace.parent / "credential-home":
        raise SandboxShimError("credential home is outside the expected isolated attempt root")

    for target, source in ro_binds.items():
        if target in _ALLOWED_SYSTEM_RO_BINDS and source == target:
            continue
        if target.startswith("/opt/hunter/bin/") and source.startswith("/app/bin/"):
            continue
        raise SandboxShimError(f"unexpected read-only bind: {source} -> {target}")

    command = argv[index:]
    if not command:
        raise SandboxShimError("provider command is missing")
    sandbox_executable = command[0]
    host_executable = ro_binds.get(sandbox_executable, sandbox_executable)
    if not (
        host_executable.startswith("/app/bin/")
        or host_executable.startswith("/usr/")
        or host_executable.startswith("/bin/")
    ):
        raise SandboxShimError("provider executable is outside the trusted runtime paths")
    if len(command) < 2 or command[1] != "run":
        raise SandboxShimError("only `opencode run` is permitted")
    return workspace, credential_home, host_executable, command[1:]


def _restricted_environment(credential_home: Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in tuple(env):
        if (
            name in _FORBIDDEN_ENV
            or name.startswith("GIT_")
            or name.startswith("HUNTER_PROMPT_")
        ):
            env.pop(name, None)
    config_home = credential_home / ".config"
    opencode_config = config_home / "opencode"
    opencode_config.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(credential_home)
    env["XDG_CONFIG_HOME"] = str(config_home)
    env["OPENCODE_CONFIG_DIR"] = str(opencode_config)
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
        _PERMISSION_CONFIG, sort_keys=True, separators=(",", ":")
    )
    env["OPENCODE_DISABLE_CLAUDE_CODE"] = "1"
    env["OPENCODE_AUTO_SHARE"] = "false"
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def main(argv: list[str] | None = None) -> int:
    try:
        workspace, credential_home, executable, provider_args = _parse(
            list(sys.argv[1:] if argv is None else argv)
        )
        env = _restricted_environment(credential_home)
        completed = subprocess.run(
            [executable, "--pure", *provider_args],
            cwd=workspace,
            env=env,
            check=False,
            timeout=900,
        )
        return completed.returncode
    except (OSError, subprocess.TimeoutExpired, SandboxShimError) as error:
        print(f"Railway OpenCode sandbox failed closed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
