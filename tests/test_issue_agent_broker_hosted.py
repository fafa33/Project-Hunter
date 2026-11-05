"""Hosted, model-free proof of the broker boundary inside the real isolation containers (Issue #682).

Requires Docker (GitHub-hosted runner). It drives the *same* ``KiloContainerIsolation`` port used in production and a
client container running as the agent uid, so it exercises the real socket, uid, cgroup and rlimit enforcement:
read-only git inspection, command timeout, memory, process and CPU enforcement, and per-command fail-closed behaviour.
The "agent edit" is a harness stand-in executed as the agent uid; it makes no claim about Kilo (the signed Kilo
canary proves that separately).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from hunter.automation.issue_agent_kilo_container import PYTHON_IMAGE, KiloContainerIsolation

pytestmark = pytest.mark.skipif(
    os.environ.get("HUNTER_RUN_REAL_BROKER_E2E") != "1", reason="requires Docker and the provisioned broker image"
)
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts/issue_agent"
CLIENT = (
    "import json,socket,sys\n"
    "s=socket.socket(socket.AF_UNIX);s.settimeout(70);s.connect('/run/hunter/broker.sock')\n"
    "s.sendall(json.dumps({'command':sys.stdin.read()}).encode());sys.stdout.write(s.recv(65536).decode())\n"
)
FILES = {
    "app.py": "VALUE = 1\n",
    "test_app.py": "from app import VALUE\n\n\ndef test_value():\n    assert VALUE >= 1\n",
    "test_busy.py": "def test_busy():\n    while True:\n        pass\n",
    "test_mem.py": (
        "def test_mem():\n    b = bytearray(1536 * 1024 * 1024)\n"
        "    for i in range(0, len(b), 4096):\n        b[i] = 1\n"
    ),
    "test_pid.py": (
        "import subprocess\n\n\ndef test_fork_flood_is_bounded():\n    procs = []\n    try:\n"
        "        for _ in range(200):\n            try:\n                procs.append(subprocess.Popen(['sleep', '20']))\n"
        "            except OSError:\n                break\n        assert len(procs) < 100\n"
        "    finally:\n        for p in procs:\n            p.kill()\n"
    ),
}


def _docker(*args: str, stdin: str = "", timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], input=stdin, capture_output=True, text=True, timeout=timeout, check=False)


@pytest.fixture(scope="module")
def session(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("broker-hosted")
    workspace, home = root / "workspace", root / "home"
    workspace.mkdir()
    home.mkdir()
    (home / "hunter-task.txt").write_text("task\n")
    for name, text in FILES.items():
        (workspace / name).write_text(text)
    isolation = KiloContainerIsolation(SCRIPTS)
    isolation.prepare(workspace, home)
    yield isolation, workspace
    isolation.stop()
    print("BROKER_TRACE", json.dumps(isolation.trace))


def send(isolation: KiloContainerIsolation, command: str) -> dict:
    done = _docker(
        "run", "--rm", "-i", "--network", "none", "--user", "65534:65532", "--read-only", "--cap-drop", "ALL",
        "--mount", f"type=volume,src={isolation.socket_volume},dst=/run/hunter,readonly",
        PYTHON_IMAGE, "python", "-c", CLIENT, stdin=command,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def agent_edit(isolation: KiloContainerIsolation, workspace: Path, code: str) -> None:
    done = _docker(
        "run", "--rm", "--network", "none", "--user", "65534:65532", "--cap-drop", "ALL",
        "--mount", f"type=bind,src={workspace},dst=/workspace", PYTHON_IMAGE, "python", "-c", code,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr


def test_git_inspection_is_read_only_task_scoped_and_allowlisted(session) -> None:
    isolation, workspace = session
    agent_edit(isolation, workspace, "open('/workspace/app.py','w').write('VALUE = 42\\n')")
    status = send(isolation, "git status --short")
    assert status["exit"] == 0 and " M app.py" in status["stdout"], status
    diff = send(isolation, "git diff")
    assert "-VALUE = 1" in diff["stdout"] and "+VALUE = 42" in diff["stdout"], diff
    for denied in ("git log", "git push", "git config --list", "git diff --no-index app.py test_app.py"):
        assert send(isolation, denied)["exit"] == 126, denied
    assert not (workspace / ".git").exists()  # nothing was written into the host workspace


def test_process_limit_is_enforced(session) -> None:
    isolation, _ = session
    result = send(isolation, "python -m pytest -q test_pid.py")
    assert result["exit"] == 0, result  # the test itself fails if more than 99 processes could be started


def test_memory_limit_kills_the_command_but_the_broker_survives(session) -> None:
    isolation, _ = session
    result = send(isolation, "python -m pytest -q test_mem.py")
    assert result["exit"] != 0, result
    assert send(isolation, "python -m pytest -q test_app.py")["exit"] == 0


def test_command_timeout_is_fail_closed_and_session_continues(session) -> None:
    isolation, _ = session
    started = time.monotonic()
    result = send(isolation, "python -m pytest -q test_busy.py")
    elapsed = time.monotonic() - started
    assert result["exit"] == 126 and 20 < elapsed < 70, (result, elapsed)
    assert send(isolation, "python -m pytest -q test_app.py")["exit"] == 0
