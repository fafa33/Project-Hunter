"""Bounded Kilo -> governed shell -> Unix broker proof on Docker Desktop."""

import json
import time
import uuid

from container_broker_canary import IMAGE, docker


def main():
    suffix = uuid.uuid4().hex[:12]
    volume = f"hunter-682-e2e-{suffix}"
    broker = f"hunter-682-e2e-broker-{suffix}"
    assert docker("volume", "create", volume).returncode == 0
    try:
        broker_code = (
            "import socket,json,os;"
            "s=socket.socket(socket.AF_UNIX);"
            "s.bind('/run/hunter/broker.sock');os.chmod('/run/hunter/broker.sock',0o666);"
            "s.listen(4);s.settimeout(55);"
            "c,_=s.accept();request=json.loads(c.recv(4096));"
            "print('BROKER_RECEIVED:',repr(request),flush=True);"
            "allowed=request=={'command':'git status --short'};"
            "reply={'exit':0 if allowed else 126,'stdout':'BROKER_OK\\n' if allowed else '',"
            "'stderr':'' if allowed else 'DENIED'};"
            "c.sendall(json.dumps(reply).encode());c.close();s.close()"
        )
        started = docker(
            "run",
            "-d",
            "--name",
            broker,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"type=volume,src={volume},dst=/run/hunter",
            IMAGE,
            "python",
            "-c",
            broker_code,
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
            raise RuntimeError("broker socket unavailable")
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
            "65534:65534",
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
            "Use the bash tool to run exactly: git status --short. Report its output.",
            timeout=65,
        )
        logs = docker("logs", broker)
        print("KILO_EXIT:", agent.returncode)
        print("KILO_STDOUT:", agent.stdout[-2500:])
        print("KILO_STDERR:", agent.stderr[-1200:])
        print("BROKER_LOGS:", logs.stdout[-1500:])
        if "BROKER_RECEIVED:" not in logs.stdout:
            raise RuntimeError("NO END-TO-END BROKER INVOCATION")
    finally:
        docker("rm", "-f", broker)
        docker("volume", "rm", "-f", volume)


if __name__ == "__main__":
    main()
