"""Prove internal-network agent cannot bypass its dedicated CONNECT gateway."""

from __future__ import annotations

import uuid
from pathlib import Path

from container_broker_canary import IMAGE, docker


def run(*args: str) -> str:
    result = docker(*args, timeout=40)
    if result.returncode:
        raise RuntimeError(result.stderr[-1200:])
    return result.stdout


def main() -> None:
    suffix = uuid.uuid4().hex[:12]
    network = f"hunter-682-internal-{suffix}"
    gateway = f"hunter-682-gateway-{suffix}"
    run("network", "create", "--internal", network)
    try:
        run(
            "run",
            "-d",
            "--name",
            gateway,
            "--network",
            network,
            "--user",
            "65534:65534",
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
            "--mount",
            f"type=bind,src={Path(__file__).parent / 'model_egress_gateway.py'},dst=/opt/gateway.py,readonly",
            IMAGE,
            "python",
            "/opt/gateway.py",
        )
        # The gateway, not the agent, is the sole dual-homed container.
        run("network", "connect", "bridge", gateway)
        code = (
            "import socket,time;"
            "\nfor _ in range(30):\n"
            " try:\n"
            "  s=socket.create_connection(('" + gateway + "',8080),timeout=2);break\n"
            " except OSError:time.sleep(.2)\n"
            "else:raise RuntimeError('gateway unavailable')\n"
            "s.sendall(b'CONNECT 127.0.0.1:443 HTTP/1.1\\r\\n\\r\\n');"
            "assert b'403 Forbidden' in s.recv(4096);s.close();"
            "\ntry:socket.create_connection(('1.1.1.1',443),timeout=2)\n"
            "except OSError:print('DIRECT_EGRESS_DENIED')\n"
            "else:raise RuntimeError('agent bypassed gateway')\n"
        )
        print(
            run(
                "run",
                "--rm",
                "--network",
                network,
                "--user",
                "65534:65534",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "32",
                "--memory",
                "128m",
                IMAGE,
                "python",
                "-c",
                code,
            )
        )
    finally:
        docker("rm", "-f", gateway)
        docker("network", "rm", network)


if __name__ == "__main__":
    main()
