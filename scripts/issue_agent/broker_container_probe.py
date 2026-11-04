"""Bounded Docker Desktop probe: no model credentials and no host shell."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from pathlib import Path

from hunter.automation.issue_agent_shell_broker import ShellBroker, serve_one

IMAGE = "python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"


def probe() -> None:
    with tempfile.TemporaryDirectory(prefix="hunter-682-", dir=Path.home()) as root:
        base = Path(root)
        workspace = base / "workspace"
        workspace.mkdir()
        (workspace / "sample.py").write_text("assert 2 + 2 == 4\n")
        socket_path = base / "broker.sock"
        # Container and broker use the same numeric UID, but container has no
        # host paths except the task workspace and one broker socket.
        with ShellBroker(socket_path, workspace, os.getuid()) as broker:
            worker = threading.Thread(
                target=serve_one,
                args=(broker.listener,),
                kwargs={"workspace": workspace, "agent_uid": os.getuid()},
                daemon=False,
            )
            worker.start()
            command = [
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
                "32",
                "--memory",
                "128m",
                "--cpus",
                "0.5",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--tmpfs",
                "/tmp:rw,nosuid,noexec,size=8m",
                "--mount",
                f"type=bind,src={workspace},dst=/workspace,readonly",
                "--mount",
                f"type=bind,src={socket_path},dst=/run/hunter.sock",
                "--workdir",
                "/workspace",
                IMAGE,
                "python",
                "-c",
                "import socket; s=socket.socket(socket.AF_UNIX); "
                "s.settimeout(4); s.connect('/run/hunter.sock'); "
                's.sendall(b\'{"command":"git status --short"}\'); '
                "print(s.recv(2048).decode()); s.close()",
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=25)
            print(f"container_exit={result.returncode}")
            print(result.stdout[:1000])
            print(result.stderr[:1000])
            if result.returncode:
                # Docker Desktop's VM does not export host Unix sockets as
                # ordinary bind mounts. Close the listener and join the worker
                # before exiting; never leave a daemon thread behind.
                broker.listener.close()
                with __import__("socket").socket(__import__("socket").AF_UNIX) as wake:
                    try:
                        wake.connect(str(socket_path))
                    except OSError:
                        pass
                worker.join(timeout=2)
                raise RuntimeError("Docker socket bridge probe failed: transport unavailable")
            worker.join(timeout=2)


if __name__ == "__main__":
    probe()
