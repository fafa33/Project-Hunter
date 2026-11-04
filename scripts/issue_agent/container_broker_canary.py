"""Issue 682: cross-container broker socket canary, no model credentials."""

from __future__ import annotations

import subprocess
import uuid

IMAGE = "python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"


def docker(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], text=True, capture_output=True, timeout=timeout)


def main() -> None:
    suffix = uuid.uuid4().hex[:12]
    volume = f"hunter-682-socket-{suffix}"
    broker = f"hunter-682-broker-{suffix}"
    assert docker("volume", "create", volume).returncode == 0
    try:
        broker_code = (
            "import os,socket,json,time;"
            "s=socket.socket(socket.AF_UNIX);"
            "s.bind('/run/hunter/broker.sock');os.chmod('/run/hunter/broker.sock',0o666);"
            "s.listen(1);s.settimeout(20);"
            "c,_=s.accept();data=c.recv(4096);"
            "request=json.loads(data);"
            "reply={'exit':126,'stdout':'','stderr':'DENIED'};"
            "reply.update({'exit':0,'stdout':'ALLOWED'} if request=={'command':'git status --short'} else {});"
            "c.sendall(json.dumps(reply).encode());c.close();s.close()"
        )
        start = docker(
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
        if start.returncode:
            raise RuntimeError(start.stderr)
        agent_code = (
            "import socket,json,os;"
            "assert not os.path.exists('/var/run/docker.sock');"
            "assert not os.path.exists('/Users/farhadafshari');"
            "s=socket.socket(socket.AF_UNIX);s.connect('/run/hunter/broker.sock');"
            "s.sendall(json.dumps({'command':'git status --short'}).encode());"
            "reply=json.loads(s.recv(4096));print(reply);"
            "assert reply['exit']==0 and reply['stdout']=='ALLOWED'"
        )
        # Wait for broker socket, bounded.
        import time

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
            raise RuntimeError("broker did not create socket")
        agent = docker(
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
            "65534:65534",
            "--mount",
            f"type=volume,src={volume},dst=/run/hunter,readonly",
            IMAGE,
            "python",
            "-c",
            agent_code,
        )
        print(f"agent_exit={agent.returncode} stdout={agent.stdout.strip()} stderr={agent.stderr.strip()}")
        if agent.returncode:
            raise RuntimeError("agent failed canary")
        logs = docker("logs", broker)
        print(f"broker_logs={logs.stdout.strip()}")
    finally:
        docker("rm", "-f", broker)
        docker("volume", "rm", "-f", volume)


if __name__ == "__main__":
    main()
