"""Deterministic fail/edit/retest proof with an isolated Docker workspace volume."""

from __future__ import annotations

import uuid
from pathlib import Path

from container_broker_canary import IMAGE, docker

BROKER_IMAGE = "hunter-682-broker-canary:local"


def checked(*args: str) -> str:
    result = docker(*args)
    if result.returncode:
        raise RuntimeError(f"Docker failed: {result.stderr[-1200:]}")
    return result.stdout.strip()


def main() -> None:
    suffix = uuid.uuid4().hex[:12]
    socket_volume = f"hunter-682-socket-{suffix}"
    work_volume = f"hunter-682-work-{suffix}"
    broker = f"hunter-682-work-broker-{suffix}"
    for volume in (socket_volume, work_volume):
        checked("volume", "create", volume)
    try:
        checked(
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
            "import os,pathlib;os.chown('/run/hunter',65533,65532);"
            "os.chmod('/run/hunter',0o770);"
            "pathlib.Path('/workspace/test_sample.py').write_text('def test_sample():\\n    assert 2 + 2 == 5\\n');"
            "os.chown('/workspace/test_sample.py',65534,65532);os.chown('/workspace',65534,65532)",
        )
        checked(
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
            "--mount",
            f"type=bind,src={Path(__file__).resolve().parents[2] / 'src/hunter/automation/issue_agent_governed_shell.py'},"
            "dst=/opt/policy/issue_agent_governed_shell.py,readonly",
            "-e",
            "PYTHONPATH=/opt/policy",
            "--tmpfs",
            "/tmp:rw,nosuid,size=32m",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            BROKER_IMAGE,
            "python",
            "/opt/broker.py",
        )
        # The agent has a writable isolated volume, not a host bind mount.
        # The broker has the exact same volume mounted read-only.
        agent_code = (
            "import json,pathlib,socket,time,os;"
            "p=pathlib.Path('/workspace/test_sample.py');"
            "assert not pathlib.Path('/var/run/docker.sock').exists();"
            "assert not pathlib.Path('/Users/farhadafshari').exists();"
            "assert not os.access('/etc/passwd',os.W_OK);"
            "\nfor expected in (1,0):\n"
            " s=socket.socket(socket.AF_UNIX);s.settimeout(15);"
            "\n for _ in range(50):\n"
            "  try:s.connect('/run/hunter/broker.sock');break\n"
            "  except (FileNotFoundError,ConnectionRefusedError):time.sleep(.1)\n"
            " else:raise RuntimeError('broker unavailable')\n"
            " s.sendall(json.dumps({'command':'python -m pytest -q test_sample.py'}).encode());"
            " s.shutdown(socket.SHUT_WR);"
            " r=json.loads(s.recv(32768));s.close();"
            " print('BROKER_EXIT',r['exit'],'OUTPUT',r['stdout'][-500:],flush=True);"
            " assert (r['exit']!=0)==bool(expected),r\n"
            " if expected:p.write_text('def test_sample():\\n    assert 2 + 2 == 4\\n')\n"
            "print('EDIT_FAIL_REPAIR_RETEST_PASS',flush=True)"
        )
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
            "65534:65532",
            "--mount",
            f"type=volume,src={socket_volume},dst=/run/hunter,readonly",
            "--mount",
            f"type=volume,src={work_volume},dst=/workspace",
            IMAGE,
            "python",
            "-c",
            agent_code,
            timeout=30,
        )
        logs = docker("logs", broker)
        print("AGENT:", agent.stdout[-1800:], agent.stderr[-500:])
        print("BROKER:", logs.stdout[-1200:])
        if agent.returncode or "EDIT_FAIL_REPAIR_RETEST_PASS" not in agent.stdout:
            raise RuntimeError("workspace edit cycle failed")
        if logs.stdout.count("BROKER_EXECUTED:") != 2:
            raise RuntimeError("broker did not execute two real checks")
    finally:
        docker("rm", "-f", broker)
        for volume in (socket_volume, work_volume):
            docker("volume", "rm", "-f", volume)


if __name__ == "__main__":
    main()
