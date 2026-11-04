"""Real Kilo repairs via broker with model traffic restricted to the gateway."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from container_broker_canary import IMAGE, docker


def run(*args: str, timeout: int = 30) -> str:
    result = docker(*args, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Docker command failed: {result.stderr[-1500:]}")
    return result.stdout.strip()


def main() -> None:
    suffix = uuid.uuid4().hex[:12]
    socket_volume = f"hunter-682-kilo-sock-{suffix}"
    work_volume = f"hunter-682-kilo-work-{suffix}"
    broker = f"hunter-682-kilo-broker-{suffix}"
    network = f"hunter-682-kilo-net-{suffix}"
    gateway = f"hunter-682-kilo-gateway-{suffix}"
    try:
        for volume in (socket_volume, work_volume):
            run("volume", "create", volume)
        run("network", "create", "--internal", network)
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
            "--mount",
            f"type=bind,src={Path(__file__).parent / 'model_egress_gateway.py'},dst=/opt/gateway.py,readonly",
            IMAGE,
            "python",
            "/opt/gateway.py",
        )
        run("network", "connect", "bridge", gateway)
        run(
            "run",
            "--rm",
            "--user",
            "0:0",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "FOWNER",
            "--mount",
            f"type=volume,src={socket_volume},dst=/run/hunter",
            "--mount",
            f"type=volume,src={work_volume},dst=/workspace",
            IMAGE,
            "python",
            "-c",
            "import os,pathlib;os.chown('/run/hunter',65533,65532);os.chmod('/run/hunter',0o770);"
            "p=pathlib.Path('/workspace/test_sample.py');"
            "p.write_text('def test_sample():\\n    assert 2 + 2 == 5\\n');"
            "os.chown(p,65534,65532);os.chown('/workspace',65534,65532)",
        )
        run(
            "run",
            "-d",
            "--name",
            broker,
            "--user",
            "65533:65532",
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
            "256m",
            "--cpus",
            "1",
            "--mount",
            f"type=volume,src={socket_volume},dst=/run/hunter",
            "--mount",
            f"type=volume,src={work_volume},dst=/workspace,readonly",
            "--mount",
            f"type=bind,src={Path(__file__).parent / 'real_broker_server.py'},dst=/opt/broker.py,readonly",
            "--tmpfs",
            "/tmp:rw,nosuid,size=32m",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "hunter-682-broker-canary:local",
            "python",
            "/opt/broker.py",
        )
        policy = {
            "permission": {"*": "deny", "bash": "allow", "read": "allow", "edit": "allow"},
            "agent": {"build": {"permission": {"*": "deny", "bash": "allow", "read": "allow", "edit": "allow"}}},
            "shell": "/usr/local/bin/hunter-governed-shell",
        }
        result = docker(
            "run",
            "--rm",
            "--network",
            network,
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
            "65534:65532",
            "--tmpfs",
            "/tmp:rw,nosuid,size=128m",
            "--mount",
            f"type=volume,src={socket_volume},dst=/run/hunter,readonly",
            "--mount",
            f"type=volume,src={work_volume},dst=/workspace",
            "-e",
            "HTTP_PROXY=http://" + gateway + ":8080",
            "-e",
            "HTTPS_PROXY=http://" + gateway + ":8080",
            "-e",
            "ALL_PROXY=http://" + gateway + ":8080",
            "-e",
            "NO_PROXY=",
            "-e",
            "NODE_USE_ENV_PROXY=1",
            "-e",
            "HOME=/tmp",
            "-e",
            "XDG_CONFIG_HOME=/tmp/config",
            "-e",
            "XDG_CACHE_HOME=/tmp/cache",
            "-e",
            "XDG_DATA_HOME=/tmp/data",
            "-e",
            "KILO_DISABLE_PROJECT_CONFIG=1",
            "-e",
            "KILO_CONFIG_CONTENT=" + json.dumps(policy),
            "-e",
            "SHELL=/usr/local/bin/hunter-governed-shell",
            "hunter-682-kilo-canary:local",
            "run",
            "--auto",
            "--model",
            "kilo/kilo-auto/free",
            "In /workspace/test_sample.py there is a deliberately broken assertion. "
            "First use the bash tool to run exactly `python -m pytest -q test_sample.py` and observe failure. "
            "Then use the edit tool to change only the incorrect expected number from 5 to 4 in that file. "
            "Then use the bash tool to run exactly `python -m pytest -q test_sample.py` again. "
            "Do not use other bash commands. Report both test results.",
            timeout=115,
        )
        logs = docker("logs", broker)
        gateway_logs = docker("logs", gateway)
        print("GATEWAY_LOGS", gateway_logs.stdout[-2000:])
        print("KILO_EXIT", result.returncode)
        print("KILO_STDOUT", result.stdout[-4000:])
        print("KILO_STDERR", result.stderr[-2500:])
        print("BROKER_LOGS", logs.stdout[-2000:])
        content = run(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--user",
            "65534:65532",
            "--mount",
            f"type=volume,src={work_volume},dst=/workspace,readonly",
            IMAGE,
            "python",
            "-c",
            "print(open('/workspace/test_sample.py').read())",
        )
        print("FINAL_FILE", repr(content))
        if (
            result.returncode
            or logs.stdout.count("BROKER_EXECUTED:") != 2
            or "EXIT: 1" not in logs.stdout
            or "EXIT: 0" not in logs.stdout
            or "assert 2 + 2 == 4" not in content
        ):
            raise RuntimeError("REAL KILO EDIT/REPAIR/RETEST NOT PROVEN")
        # Independent verifier runs in a separate process, reopens the volume read-only,
        # and reads broker evidence without relying on Kilo stdout.
        verified = __import__("subprocess").run(
            ["python3", str(Path(__file__).parent / "kilo_canary_independent_validator.py"), work_volume, broker],
            capture_output=True,
            text=True,
            check=False,
            timeout=40,
        )
        if verified.returncode or "CANARY_ONLY_NOT_PRODUCTION_TRUSTED_VALIDATOR" not in verified.stdout:
            raise RuntimeError("independent canary verification failed")
        print("INDEPENDENT_CANARY_VERIFIER", verified.stdout.strip())
        print("REAL_KILO_REPAIR_PROVEN")
    finally:
        docker("rm", "-f", broker)
        docker("rm", "-f", gateway)
        docker("network", "rm", network)
        for volume in (socket_volume, work_volume):
            docker("volume", "rm", "-f", volume)


if __name__ == "__main__":
    main()
