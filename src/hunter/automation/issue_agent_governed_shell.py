"""Issue #682: fail-closed, container-isolated task verification commands.

This is a shell *tool* boundary, not a substitute for isolating the model
process. Never expose the Docker socket or credentials to the model process.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

ALLOWED = {
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
    ("python", "-m", "ruff", "check"),
    ("python3", "-m", "ruff", "check"),
    ("python", "-m", "black", "--check"),
    ("python3", "-m", "black", "--check"),
    ("git", "diff"),
    ("git", "status"),
    ("ls",),
    ("cat",),
    ("rg",),
}
FORBIDDEN = {";", "&&", "||", "|", ">", "<", "`", "$(", "\n", "\r"}


class ShellDenied(ValueError):
    pass


def parse_command(command: str) -> list[str]:
    if any(marker in command for marker in FORBIDDEN):
        raise ShellDenied("compound or redirected shell command")
    try:
        args = shlex.split(command)
    except ValueError as exc:
        raise ShellDenied("invalid command") from exc
    if not args or not any(tuple(args[: len(prefix)]) == prefix for prefix in ALLOWED):
        raise ShellDenied("command not allowlisted")
    # Read-only discovery is essential for a coding agent. Keep commands argv-only,
    # workspace-relative, bounded, and free of executable search options.
    if args[0] == "ls":
        if any(arg.startswith("-") and arg not in {"-a", "-l", "-la"} for arg in args[1:]):
            raise ShellDenied("unapproved ls flag")
    elif args[0] == "cat":
        if len(args) < 2 or any(arg.startswith("-") for arg in args[1:]):
            raise ShellDenied("cat requires workspace-relative files")
    elif args[0] == "rg":
        if len(args) < 2 or any(
            arg.startswith("-") and arg not in {"-n", "-l", "--files", "-i", "-F"} for arg in args[1:]
        ):
            raise ShellDenied("unapproved rg flag")
        if "--files" not in args and not any(not arg.startswith("-") for arg in args[1:]):
            raise ShellDenied("rg requires a pattern")
    if len(args) > 24 or sum(map(len, args)) > 2048:
        raise ShellDenied("command budget exceeded")
    for arg in args:
        if arg.startswith("-") and arg not in {
            "-m",
            "-q",
            "-v",
            "--check",
            "--stat",
            "--short",
            "--porcelain",
            "--no-color",
            "-a",
            "-l",
            "-la",
            "-n",
            "-i",
            "-F",
            "--files",
        }:
            raise ShellDenied("unapproved flag")
        if arg.startswith("/") or arg.startswith("~") or ".." in Path(arg).parts or "\\" in arg:
            raise ShellDenied("path outside task workspace")
        if any(c in arg for c in ("*", "?", "[", "]", "$")):
            raise ShellDenied("shell expansion forbidden")
    return args


def run_governed(
    command: str, *, workspace: Path, image: str = "hunter-issue-agent-tools:locked", timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    args = parse_command(command)
    if workspace.is_symlink():
        raise ShellDenied("symlink workspace")
    root = workspace.resolve(strict=True)
    if not root.is_dir():
        raise ShellDenied("invalid workspace")
    # Never mount a workspace containing symlinks: a symlink can point to
    # sensitive paths *inside* the container, bypassing lexical argument checks.
    # This conservative first slice refuses all symlinks, including nested ones.
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            if (Path(directory) / name).is_symlink():
                raise ShellDenied("symlink in task workspace")
    if os.environ.get("HUNTER_GOVERNED_SHELL_ENABLED") != "1":
        raise ShellDenied("container tool runtime not explicitly enabled")
    if not image.startswith("sha256:") and "@sha256:" not in image:
        raise ShellDenied("container image must be digest pinned")
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--user",
            "65534:65534",
            "--tmpfs",
            "/tmp:rw,nosuid,noexec,size=32m",
            "--mount",
            f"type=bind,src={root},dst=/workspace,readonly",
            "--workdir",
            "/workspace",
            "--entrypoint",
            args[0],
            image,
            *args[1:],
        ],
        capture_output=True,
        text=True,
        timeout=min(timeout, 60),
        check=False,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
    )
