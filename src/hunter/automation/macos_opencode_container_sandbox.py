"""Mac-host Docker isolation adapter for the existing governed OpenCode bwrap contract.

This adapter is an isolation transport, not a second issuer or publisher. It
refuses unpinned images and all unexpected bwrap mounts/flags. The provider
image must contain the reviewed Linux OpenCode binary and its pinned runtime.
"""
from __future__ import annotations

import os
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
    if not _IMAGE.fullmatch(image):
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
    workspace = Path(mounts["/workspace"]).resolve(strict=True)
    credentials = Path(mounts["/home/hunter"]).resolve(strict=True)
    if any(p.is_symlink() for p in (workspace, credentials, workspace.parent)) or workspace.name != "repo" or not workspace.parent.name.startswith("hunter-opencode-attempt-") or credentials != workspace.parent / "credential-home":
        raise ContainerSandboxRefused("attempt workspace or credential home is not isolated")
    if not workspace.is_dir() or not credentials.is_dir():
        raise ContainerSandboxRefused("attempt directories must exist")
    command = args[index:]
    if len(command) < 2 or command[0] not in {"/usr/local/bin/opencode", "/usr/bin/opencode", "/opt/hunter/bin/opencode"} or command[1] != "run":
        raise ContainerSandboxRefused("only image-owned OpenCode run is allowed")
    return ["docker", "run", "--rm", "--init", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=128", "--memory=2g", f"--user={os.getuid()}:{os.getgid()}", "--tmpfs=/tmp:rw,nosuid,nodev,size=128m", "--mount", f"type=bind,src={workspace},dst=/workspace", "--mount", f"type=bind,src={credentials},dst=/home/hunter", "--workdir=/workspace", "--env", "HOME=/home/hunter", image, "/usr/local/bin/opencode", *command[1:]]


def main() -> int:
    try:
        command = translate(sys.argv[1:], os.environ.get("HUNTER_MACOS_OPENCODE_IMAGE", ""))
    except (ContainerSandboxRefused, OSError) as error:
        print(f"governed container sandbox refused: {error}", file=sys.stderr)
        return 2
    return subprocess.run(command, check=False).returncode

if __name__ == "__main__":
    raise SystemExit(main())
