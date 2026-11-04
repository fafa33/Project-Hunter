"""Issue #682: trusted Unix socket bridge; Docker never enters the agent principal.

The broker is a trusted parent process. The agent receives only a Unix socket
and a fixed client. The broker binds a single immutable workspace and delegates
all commands to the governed container tool.
"""

from __future__ import annotations

import json
import os
import socket
import struct
from collections.abc import Callable
from pathlib import Path

from hunter.automation.issue_agent_governed_shell import ShellDenied, run_governed

MAX_REQUEST = 4096
MAX_OUTPUT = 16384


def peer_uid(conn: socket.socket) -> int:
    """Linux SO_PEERCRED, never trust a client-supplied uid."""
    if hasattr(conn, "getpeereid"):
        return conn.getpeereid()[0]
    if hasattr(socket, "LOCAL_PEERCRED"):
        credentials = conn.getsockopt(0, socket.LOCAL_PEERCRED, 76)
        _version, uid = struct.unpack_from("=II", credentials)
        return uid
    if hasattr(socket, "SO_PEERCRED"):
        credentials = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", credentials)
        return uid
    raise ShellDenied("peer credentials unavailable")


def serve_one(
    listener: socket.socket,
    *,
    workspace: Path,
    agent_uid: int,
    runner: Callable = run_governed,
) -> None:
    conn, _ = listener.accept()
    with conn:
        try:
            if peer_uid(conn) != agent_uid:
                raise ShellDenied("unauthorized peer")
            data = conn.recv(MAX_REQUEST + 1)
            if len(data) > MAX_REQUEST:
                raise ShellDenied("oversized request")
            request = json.loads(data)
            if not isinstance(request, dict) or set(request) != {"command"} or not isinstance(request["command"], str):
                raise ShellDenied("invalid request")
            result = runner(request["command"], workspace=workspace)
            reply = {
                "exit": result.returncode,
                "stdout": result.stdout[:MAX_OUTPUT],
                "stderr": result.stderr[:MAX_OUTPUT],
            }
        except (ShellDenied, ValueError, TypeError, UnicodeError, OSError) as exc:
            reply = {"exit": 126, "stdout": "", "stderr": f"DENIED: {type(exc).__name__}"}
        conn.sendall(json.dumps(reply).encode())


class ShellBroker:
    """One-shot-per-request broker; no subprocess or Docker handle is shared."""

    def __init__(self, socket_path: Path, workspace: Path, agent_uid: int):
        self.socket_path = socket_path
        self.workspace = workspace
        self.agent_uid = agent_uid
        self.listener: socket.socket | None = None

    def __enter__(self):
        if self.socket_path.exists() or self.socket_path.is_symlink():
            raise ShellDenied("socket path already exists")
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        self.listener.listen(1)
        return self

    def handle_once(self):
        if self.listener is None:
            raise ShellDenied("broker not started")
        serve_one(self.listener, workspace=self.workspace, agent_uid=self.agent_uid)

    def __exit__(self, *_):
        if self.listener:
            self.listener.close()
        self.socket_path.unlink(missing_ok=True)


def client(socket_path: Path, command: str) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(65)
        conn.connect(str(socket_path))
        conn.sendall(json.dumps({"command": command}).encode())
        return json.loads(conn.recv(2 * MAX_OUTPUT + 1024))


def run_client(socket_path: Path, command: str) -> int:
    response = client(socket_path, command)
    print(response["stdout"], end="")
    print(response["stderr"], end="", file=__import__("sys").stderr)
    return int(response["exit"])
