"""Bounded Kilo -> governed shell -> Unix broker proof on Docker Desktop."""

import json
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from container_broker_canary import IMAGE, docker


def main():
    suffix = uuid.uuid4().hex[:12]
    volume = f"hunter-682-e2e-{suffix}"
    broker = f"hunter-682-e2e-broker-{suffix}"
    assert docker("volume", "create", volume).returncode == 0
    workspace = None
    try:
        setup = docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "FOWNER",
            "--mount",
            f"type=volume,src={volume},dst=/run/hunter",
            IMAGE,
            "python",
            "-c",
            "import os;os.chown('/run/hunter',65533,65532);os.chmod('/run/hunter',0o770)",
        )
        if setup.returncode:
            raise RuntimeError("volume setup failed: " + setup.stderr)
        workspace = Path(tempfile.mkdtemp(prefix="hunter-broker-work-", dir=Path.home()))
        (workspace / "test_sample.py").write_text("def test_sample():\n    assert 2 + 2 == 4\n")
        started = docker(
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
            "--mount",
            f"type=volume,src={volume},dst=/run/hunter",
            "--mount",
            f"type=bind,src={workspace},dst=/workspace,readonly",
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
        if started.returncode:
            raise RuntimeError(started.stderr)
        for _ in range(20):
            ready = docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--mount",
                f"type=volume,src={volume},dst=/run/hunter,readonly",
                IMAGE,
                "python",
                "-c",
                "import os;assert os.path.exists('/run/hunter/broker.sock')",
            )
            if ready.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError(
                "broker socket unavailable: " + docker("logs", broker).stderr + docker("logs", broker).stdout
            )
        policy = {
            "permission": {"*": "deny", "bash": "allow", "read": "allow"},
            "agent": {"build": {"permission": {"*": "deny", "bash": "allow", "read": "allow"}}},
            "shell": "/usr/local/bin/hunter-governed-shell",
        }
        agent = docker(
            "run",
            "--rm",
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
            f"type=volume,src={volume},dst=/run/hunter,readonly",
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
            "Use the bash tool to run exactly: python -m pytest -q test_sample.py. Report the actual test results.",
            timeout=65,
        )
        logs = docker("logs", broker)
        print("KILO_EXIT:", agent.returncode)
        print("KILO_STDOUT:", agent.stdout[-2500:])
        print("KILO_STDERR:", agent.stderr[-1200:])
        print("BROKER_LOGS:", logs.stdout[-1500:])
        if "BROKER_EXECUTED:" not in logs.stdout or "1 passed" not in agent.stdout + agent.stderr:
            raise RuntimeError("NO VERIFIED REAL TEST EXECUTION")
    finally:
        if workspace is not None:
            shutil.rmtree(workspace)
        docker("rm", "-f", broker)
        docker("volume", "rm", "-f", volume)


if __name__ == "__main__":
    main()
