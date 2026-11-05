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

import json
import os
import resource
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
MAX_WORKSPACE_ENTRIES = 50000
WORKSPACE = "/workspace"
BROKER_TOOLS = {"python", "python3"}  # git is not provided: the workspace carries no .git metadata


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
        (resource.RLIMIT_CPU, COMMAND_TIMEOUT),
        (resource.RLIMIT_FSIZE, 10 * 1024 * 1024),
        (resource.RLIMIT_NPROC, 64),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(kind, (value, value))


def assert_no_symlinks(root: str = WORKSPACE) -> None:
    """The agent can create links with its edit tool at any time: re-check before every command."""
    seen = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            seen += 1
            if seen > MAX_WORKSPACE_ENTRIES:
                raise ShellDenied("workspace too large")
            if os.path.islink(os.path.join(directory, name)):
                raise ShellDenied("symlink in task workspace")


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
        args = parse_command(command)
        if args[0] not in BROKER_TOOLS:
            raise ShellDenied("tool not provided in broker")
        assert_no_symlinks()
        budget.charge_command()
        remaining = budget.deadline - time.monotonic()  # recv() and the scan above consumed wall clock
        if remaining <= 0:
            raise BudgetExhausted("wall-clock budget exhausted before execution")
        print("BROKER_RECEIVED:", repr(command), flush=True)
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
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTEST_ADDOPTS": "-p no:cacheprovider",
                    "RUFF_NO_CACHE": "true",
                    "BLACK_CACHE_DIR": cache_dir,
                },
            )
        response = {"exit": result.returncode, "stdout": result.stdout[:16000], "stderr": result.stderr[:16000]}
        print("BROKER_EXECUTED:", repr(args), "EXIT:", result.returncode, flush=True)
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
