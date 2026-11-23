"""Mac-host Docker isolation adapter for the existing governed OpenCode bwrap contract.

This adapter is an isolation transport, not a second issuer or publisher. It
refuses unpinned images and all unexpected bwrap mounts/flags. The provider
image must contain the reviewed Linux OpenCode binary and its pinned runtime.
"""
from __future__ import annotations

import os
import json
import re
import subprocess
import sys
from pathlib import Path

class ContainerSandboxRefused(RuntimeError):
    pass

_IMAGE = re.compile(r"^[a-zA-Z0-9._:/-]+@sha256:[0-9a-f]{64}$")
_FLAGS = {"--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts"}
_VALUES = {"--proc": "/proc", "--dev": "/dev", "--tmpfs": "/tmp"}


def translate(args: list[str], image: str) -> list[str]:
    if not (_IMAGE.fullmatch(image) or re.fullmatch(r"sha256:[0-9a-f]{64}", image)):
        raise ContainerSandboxRefused("provider image must be pinned by sha256 digest")
    flags: set[str] = set()
    values: dict[str, str] = {}
    mounts: dict[str, str] = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _FLAGS:
            if arg in flags:
                raise ContainerSandboxRefused("duplicate isolation flag")
            flags.add(arg)
            index += 1
        elif arg in _VALUES:
            if arg in values or index + 1 >= len(args):
                raise ContainerSandboxRefused("duplicate or incomplete isolation option")
            values[arg] = args[index + 1]
            index += 2
        elif arg in {"--ro-bind", "--bind"}:
            if index + 2 >= len(args):
                raise ContainerSandboxRefused("incomplete mount")
            source, destination = args[index + 1:index + 3]
            if arg == "--ro-bind" and destination == "/opt/hunter/bin/opencode" and Path(source).name == "opencode":
                pass  # Host binary never enters the container; image binary is authoritative
            elif arg == "--ro-bind" and source == destination and source in {"/usr", "/bin", "/lib", "/lib64", "/etc"}:
                pass  # Linux image already owns its own system files
            elif arg == "--bind" and destination in {"/workspace", "/home/hunter"} and destination not in mounts:
                mounts[destination] = source
            else:
                raise ContainerSandboxRefused("unexpected host mount")
            index += 3
        elif arg == "--dir":
            if index + 1 >= len(args) or args[index + 1] != "/opt/hunter/bin":
                raise ContainerSandboxRefused("unexpected binary mount directory")
            index += 2
        elif arg == "--chdir":
            if index + 1 >= len(args) or args[index + 1] != "/workspace":
                raise ContainerSandboxRefused("invalid working directory")
            index += 2
            break
        else:
            raise ContainerSandboxRefused("unexpected sandbox argument")
    if flags != _FLAGS or values != _VALUES or set(mounts) != {"/workspace", "/home/hunter"}:
        raise ContainerSandboxRefused("incomplete isolation contract")
    raw_workspace = Path(mounts["/workspace"])
    raw_credentials = Path(mounts["/home/hunter"])
    if not raw_workspace.is_absolute() or not raw_credentials.is_absolute():
        raise ContainerSandboxRefused("attempt paths must be absolute")
    for raw in (raw_workspace, raw_credentials):
        if any(parent.is_symlink() for parent in (raw, *raw.parents)):
            raise ContainerSandboxRefused("symlink in attempt mount path")
    workspace = raw_workspace.resolve(strict=True)
    credentials = raw_credentials.resolve(strict=True)
    if workspace.name != "repo" or not workspace.parent.name.startswith("hunter-opencode-attempt-") or credentials != workspace.parent / "credential-home":
        raise ContainerSandboxRefused("attempt workspace or credential home is not isolated")
    if not workspace.is_dir() or not credentials.is_dir():
        raise ContainerSandboxRefused("attempt directories must exist")
    command = args[index:]
    if len(command) < 2 or command[0] not in {"/usr/local/bin/opencode", "/usr/bin/opencode", "/opt/hunter/bin/opencode"} or command[1] != "run":
        raise ContainerSandboxRefused("only image-owned OpenCode run is allowed")
    config_dir = credentials / ".config" / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    denied_shell = config_dir / "hunter-denied-shell"
    denied_shell.write_text("#!/bin/sh\necho 'Hunter forbids shell execution' >&2\nexit 126\n", encoding="utf-8")
    denied_shell.chmod(0o555)
    config = {"permission": {"*": "deny", "external_directory": "deny", "read": "allow", "edit": "allow", "glob": "allow", "grep": "allow", "lsp": "allow", "bash": "allow"}, "shell": "/home/hunter/.config/opencode/hunter-denied-shell"}
    config_json = json.dumps(config, sort_keys=True, separators=(",", ":"))
    nonce = os.environ.get("HUNTER_PROBE_NONCE", "")
    if nonce and not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ContainerSandboxRefused("invalid self-check nonce")
    probe_env = ["--env", f"HUNTER_PROBE_NONCE={nonce}"] if nonce else []
    return ["docker", "run", "--rm", "--init", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=128", "--memory=2g", f"--user={os.getuid()}:{os.getgid()}", "--tmpfs=/tmp:rw,nosuid,nodev,size=128m", "--mount", f"type=bind,src={workspace},dst=/workspace", "--mount", f"type=bind,src={credentials},dst=/home/hunter", "--workdir=/workspace", "--env", "HOME=/home/hunter", "--env", "SHELL=/home/hunter/.config/opencode/hunter-denied-shell", "--env", f"OPENCODE_CONFIG_CONTENT={config_json}", "--env", "OPENCODE_DISABLE_AUTOUPDATE=true", *probe_env, image, "/usr/local/bin/opencode", "--pure", *command[1:]]


def verify_capabilities(payload: str) -> None:
    try:
        tools = json.loads(payload)["tools"]
    except (ValueError, KeyError, TypeError) as error:
        raise ContainerSandboxRefused("provider capability discovery malformed") from error
    if not isinstance(tools, dict):
        raise ContainerSandboxRefused("provider capabilities are malformed")
    if any(tools.get(name) is not True for name in ("read", "edit", "glob", "grep")):
        raise ContainerSandboxRefused("required provider tools are disabled")
    if any(tools.get(name) is not False for name in ("webfetch", "websearch", "task", "skill", "question")):
        raise ContainerSandboxRefused("forbidden provider tools are enabled or unknown")


def main() -> int:
    try:
        command = translate(sys.argv[1:], os.environ.get("HUNTER_MACOS_OPENCODE_IMAGE", ""))
    except (ContainerSandboxRefused, OSError) as error:
        print(f"governed container sandbox refused: {error}", file=sys.stderr)
        return 2
    # Resolve tools under the identical container, mounts, permissions and model
    # configuration before permitting an agent run. A failed probe is not an opt-out.
    binary = command.index("/usr/local/bin/opencode")
    probe = [*command[:binary], "/usr/local/bin/opencode", "debug", "agent", "build", "--pure"]
    try:
        observed = subprocess.run(probe, check=False, capture_output=True, text=True, timeout=45)
        if observed.returncode != 0:
            raise ContainerSandboxRefused("provider capability discovery failed")
        verify_capabilities(observed.stdout)
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired, ContainerSandboxRefused) as error:
        print(f"governed container sandbox refused: {error}", file=sys.stderr)
        return 2
    return subprocess.run(command, check=False).returncode

if __name__ == "__main__":
    raise SystemExit(main())
