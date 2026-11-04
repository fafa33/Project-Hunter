"""Restricted container broker: allowlisted argv, peer UID, bounded output/time."""

from __future__ import annotations

import json
import os
import shlex
import socket
import struct
import subprocess
from pathlib import Path

AGENT_UID = 65534
BROKER_UID = 65533
SHARED_GID = 65532
MAX_REQUEST = 4096
ALLOWED = {
    ("python", "-m", "pytest", "-q"),
    ("python", "-m", "pytest", "-q", "test_sample.py"),
}


def handle(conn: socket.socket) -> None:
    try:
        conn.settimeout(4)
        credentials = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _, uid, _ = struct.unpack("3i", credentials)
        if uid != AGENT_UID:
            raise PermissionError("unauthorized peer")
        payload = conn.recv(MAX_REQUEST + 1)
        if len(payload) > MAX_REQUEST:
            raise ValueError("oversized request")
        request = json.loads(payload)
        if not isinstance(request, dict) or set(request) != {"command"}:
            raise ValueError("invalid request")
        command = request["command"]
        if not isinstance(command, str) or len(command) > 256:
            raise ValueError("invalid command")
        args = shlex.split(command)
        if tuple(args) not in ALLOWED:
            raise ValueError("command not allowlisted")
        print("BROKER_RECEIVED:", repr(command), flush=True)
        result = subprocess.run(
            args,
            cwd="/workspace",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTEST_ADDOPTS": "-p no:cacheprovider",
            },
        )
        response = {"exit": result.returncode, "stdout": result.stdout[:16000], "stderr": result.stderr[:16000]}
        print("BROKER_EXECUTED:", repr(args), "EXIT:", result.returncode, flush=True)
    except (
        PermissionError,
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
    server.settimeout(75)
    try:
        for _ in range(2):
            conn, _ = server.accept()
            with conn:
                handle(conn)
    finally:
        server.close()


if __name__ == "__main__":
    main()
