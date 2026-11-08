"""Governed container broker: shared allowlist policy, peer UID, explicit repair budgets.

The agent has no shell of its own; every command arrives here and is parsed by the single
shared policy (``issue_agent_governed_shell.parse_command``). The broker is the *only* place a
task command executes, inside a no-network container over a read-only workspace.

Repair-loop budgets are explicit and fail closed:
* ``MAX_COMMANDS``  - executed test/lint attempts (edit/test/diagnose/repair/retest cycles)
* ``MAX_REQUESTS``  - all requests, including denied probes
* ``DEADLINE_SECONDS`` - wall clock for the whole session; after it nothing runs
* ``COMMAND_TIMEOUT`` - per command, with CPU / file-size / process rlimits
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path

try:
    from hunter.automation.issue_agent_governed_shell import ShellDenied, parse_command
except ImportError:  # container: the policy module is bind-mounted flat and PYTHONPATH points at it
    from issue_agent_governed_shell import ShellDenied, parse_command  # type: ignore[import-not-found,no-redef]

AGENT_UID = 65534
BROKER_UID = 65533
SHARED_GID = 65532
MAX_REQUEST = 4096
MAX_COMMANDS = max(1, min(int(os.environ.get("HUNTER_BROKER_MAX_COMMANDS", "16")), 32))
MAX_REQUESTS = 4 * MAX_COMMANDS
DEADLINE_SECONDS = max(1, min(int(os.environ.get("HUNTER_BROKER_DEADLINE_SECONDS", "660")), 900))
COMMAND_TIMEOUT = 30
CPU_GRACE_SECONDS = 5  # CPU backstop sits above the wall timeout so the timeout deterministically wins
MAX_WORKSPACE_ENTRIES = 50000
WORKSPACE = "/workspace"
TOOL_PATH = "/usr/local/bin:/usr/bin:/bin"
MEMORY_LIMIT_BYTES = 2 * 1024**3  # address-space backstop; the container cgroup is the hard memory wall
BROKER_TOOLS = {"python", "python3", "git", "ls", "cat", "rg"}
# Read-only task-scoped git: a private, broker-owned snapshot of the workspace at session start. No host
# repository, credentials or remote is ever mounted; the agent can write neither the snapshot nor its config.
GIT_DIR: str | None = None
GIT_HARDENING = (
    "--no-pager",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "diff.external=",
    "-c",
    "safe.directory=*",
    "-c",
    "protocol.allow=never",
)
GIT_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_PAGER": "cat",
    "LC_ALL": "C",
}


class BudgetExhausted(PermissionError):
    pass


class Budget:
    """Mutable session budget; a fresh instance per broker process."""

    def __init__(self, commands: int = MAX_COMMANDS, requests: int = MAX_REQUESTS, deadline: float | None = None):
        self.commands = commands
        self.requests = requests
        self.deadline = time.monotonic() + DEADLINE_SECONDS if deadline is None else deadline

    def charge_request(self) -> None:
        self.requests -= 1
        if self.requests < 0 or time.monotonic() >= self.deadline:
            raise BudgetExhausted("request or wall-clock budget exhausted")

    def charge_command(self) -> None:
        self.commands -= 1
        if self.commands < 0:
            raise BudgetExhausted("repair attempt budget exhausted")


def _limits() -> None:  # runs in the child between fork and exec
    for kind, value in (
        (resource.RLIMIT_CPU, COMMAND_TIMEOUT + CPU_GRACE_SECONDS),
        (resource.RLIMIT_FSIZE, 10 * 1024 * 1024),
        (resource.RLIMIT_NPROC, 64),
        (resource.RLIMIT_AS, MEMORY_LIMIT_BYTES),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(kind, (value, value))


def assert_no_symlinks(root: str | None = None) -> None:
    """The agent can create links with its edit tool at any time: re-check before every command."""
    root = root or WORKSPACE
    seen = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            seen += 1
            if seen > MAX_WORKSPACE_ENTRIES:
                raise ShellDenied("workspace too large")
            if os.path.islink(os.path.join(directory, name)):
                raise ShellDenied("symlink in task workspace")


def workspace_fingerprint(root: str | None = None) -> str:
    """Digest of the workspace as the command sees it: proves an edit happened between two runs."""
    root = root or WORKSPACE
    digest = hashlib.sha256()
    seen = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in sorted(files):
            seen += 1
            if seen > MAX_WORKSPACE_ENTRIES:
                digest.update(b"truncated")
                return digest.hexdigest()
            path = os.path.join(directory, name)
            size = os.lstat(path).st_size
            digest.update(f"{os.path.relpath(path, root)}\0{size}\0".encode())
            if size <= 256 * 1024:
                with open(path, "rb") as handle_:
                    digest.update(hashlib.sha256(handle_.read()).digest())
    return digest.hexdigest()


def init_git_snapshot(root: str | None = None) -> str | None:
    """Create the private baseline snapshot; None (git then fails closed) if it cannot be built."""
    root = root or WORKSPACE
    git_dir = tempfile.mkdtemp(prefix="hunter-git-")  # unpredictable, mode 0700, owned by this uid
    base = ["git", *GIT_HARDENING, f"--git-dir={git_dir}", f"--work-tree={root}"]
    identity = ["-c", "user.name=hunter", "-c", "user.email=hunter@localhost", "-c", "commit.gpgsign=false"]
    try:
        for step in (
            [*base, "init", "-q"],
            [*base, "add", "-A"],
            [*base, *identity, "commit", "-q", "--allow-empty", "-m", "baseline"],
        ):
            done = subprocess.run(step, capture_output=True, timeout=60, check=False, env=GIT_ENV)
            if done.returncode:
                raise OSError("git snapshot failed")
    except (OSError, subprocess.SubprocessError):
        shutil.rmtree(git_dir, ignore_errors=True)
        return None
    return git_dir


def git_argv(args: list[str], git_dir: str, root: str | None = None) -> list[str]:
    """Hardened argv for one allowlisted ``git status|diff``; no external diff, textconv, hook or pager."""
    sub = args[1]
    if sub not in {"status", "diff"}:
        raise ShellDenied("git subcommand not allowed")
    extra = ["--no-ext-diff", "--no-textconv"] if sub == "diff" else []
    return ["git", *GIT_HARDENING, f"--git-dir={git_dir}", f"--work-tree={root or WORKSPACE}", sub, *extra, *args[2:]]


def handle(conn: socket.socket, budget: Budget | None = None) -> None:
    budget = budget or Budget()
    try:
        conn.settimeout(4)
        credentials = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _, uid, _ = struct.unpack("3i", credentials)
        if uid != AGENT_UID:
            raise PermissionError("unauthorized peer")
        budget.charge_request()
        payload = conn.recv(MAX_REQUEST + 1)
        if len(payload) > MAX_REQUEST:
            raise ValueError("oversized request")
        request = json.loads(payload)
        if not isinstance(request, dict) or set(request) != {"command"}:
            raise ValueError("invalid request")
        command = request["command"]
        if not isinstance(command, str) or len(command) > 256:
            raise ValueError("invalid command")
        args = parsed = parse_command(command)
        if args[0] not in BROKER_TOOLS:
            raise ShellDenied("tool not provided in broker")
        env = {
            "PATH": TOOL_PATH,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_ADDOPTS": "-p no:cacheprovider",
            "RUFF_NO_CACHE": "true",
        }
        is_git = args[0] == "git"
        if is_git:
            if GIT_DIR is None:
                raise ShellDenied("git snapshot unavailable")
            args = git_argv(args, GIT_DIR)
            env = dict(GIT_ENV)
        assert_no_symlinks()
        budget.charge_command()
        remaining = budget.deadline - time.monotonic()  # recv() and the scan above consumed wall clock
        if remaining <= 0:
            raise BudgetExhausted("wall-clock budget exhausted before execution")
        print("BROKER_RECEIVED:", repr(command), flush=True)
        tree = workspace_fingerprint()
        # Black writes a cache; give every command its own unpredictable 0700 directory owned by
        # this uid instead of a predictable shared path, and remove it afterwards.
        with tempfile.TemporaryDirectory(prefix="hunter-cache-") as cache_dir:
            result = subprocess.run(
                args,
                cwd=WORKSPACE,
                capture_output=True,
                text=True,
                timeout=min(COMMAND_TIMEOUT, remaining),
                check=False,
                preexec_fn=_limits,
                env=env if is_git else {**env, "BLACK_CACHE_DIR": cache_dir},
            )
        response = {"exit": result.returncode, "stdout": result.stdout[:16000], "stderr": result.stderr[:16000]}
        print(
            "BROKER_EXECUTED:",
            repr(parsed),
            "EXIT:",
            result.returncode,
            "TREE:",
            tree,
            "STDOUT_BYTES:",
            len(result.stdout),
            flush=True,
        )
    except (
        PermissionError,
        ShellDenied,
        ValueError,
        TypeError,
        UnicodeError,
        OSError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as exc:
        response = {"exit": 126, "stdout": "", "stderr": "DENIED"}
        print("BROKER_DENIED:", type(exc).__name__, flush=True)
    conn.sendall(json.dumps(response).encode())


def main() -> None:
    directory = Path("/run/hunter")
    if os.geteuid() != BROKER_UID or directory.is_symlink():
        raise RuntimeError("broker must run as dedicated unprivileged uid")
    path = str(directory / "broker.sock")
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    os.chmod(path, 0o660)
    server.listen(4)
    global GIT_DIR
    GIT_DIR = init_git_snapshot()
    print("BROKER_GIT_SNAPSHOT:", "ready" if GIT_DIR else "unavailable", flush=True)
    budget = Budget()
    try:
        while budget.requests > 0:
            remaining = budget.deadline - time.monotonic()
            if remaining <= 0:
                break
            server.settimeout(min(remaining, 120))
            try:
                conn, _ = server.accept()
            except TimeoutError:
                break
            with conn:
                handle(conn, budget)
    finally:
        server.close()


if __name__ == "__main__":
    main()
