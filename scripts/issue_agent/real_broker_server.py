"""Container-local allowlisted test executor. No host Docker socket or shell."""

import json
import os
import shlex
import socket
import subprocess

ALLOWED = {("python", "-m", "pytest", "-q"), ("python", "-m", "pytest", "-q", "test_sample.py")}


def main():
    path = "/run/hunter/broker.sock"
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    os.chmod(path, 0o666)  # Transport proof only; production must restrict peer credentials.
    server.listen(4)
    server.settimeout(75)
    for _ in range(2):
        conn, _ = server.accept()
        with conn:
            request = json.loads(conn.recv(4096))
            command = request.get("command") if isinstance(request, dict) else None
            print("BROKER_RECEIVED:", repr(command), flush=True)
            try:
                args = shlex.split(command) if isinstance(command, str) else []
                if tuple(args) not in ALLOWED:
                    raise ValueError("command not allowlisted")
                result = subprocess.run(args, cwd="/workspace", capture_output=True, text=True, timeout=30, check=False)
                response = {"exit": result.returncode, "stdout": result.stdout[:16000], "stderr": result.stderr[:16000]}
                print("BROKER_EXECUTED:", repr(args), "EXIT:", result.returncode, flush=True)
            except (ValueError, subprocess.TimeoutExpired, OSError):
                response = {"exit": 126, "stdout": "", "stderr": "DENIED"}
                print("BROKER_DENIED", flush=True)
            conn.sendall(json.dumps(response).encode())
    server.close()


if __name__ == "__main__":
    main()
