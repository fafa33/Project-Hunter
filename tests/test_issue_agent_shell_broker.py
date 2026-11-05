import os
import tempfile
import threading
from pathlib import Path
from subprocess import CompletedProcess

from hunter.automation.issue_agent_shell_broker import ShellBroker, client, serve_one


def test_broker_dispatches_only_to_fixed_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    short_dir = tempfile.TemporaryDirectory(prefix="hb-", dir="/tmp")
    socket_path = Path(short_dir.name) / "s"
    calls = []

    def fake(command, *, workspace):
        calls.append((command, workspace))
        return CompletedProcess([], 0, "test passed", "")

    with ShellBroker(socket_path, workspace, os.getuid()) as broker:
        worker = threading.Thread(
            target=serve_one,
            args=(broker.listener,),
            kwargs={"workspace": workspace, "agent_uid": os.getuid(), "runner": fake},
        )
        worker.start()
        reply = client(socket_path, "python -m pytest -q")
        worker.join(timeout=3)
    assert reply["exit"] == 0
    assert calls == [("python -m pytest -q", workspace)]
    assert not socket_path.exists()
    short_dir.cleanup()


def test_broker_rejects_wrong_uid(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with (
        tempfile.TemporaryDirectory(prefix="hb-", dir="/tmp") as short_dir,
        ShellBroker(Path(short_dir) / "s", workspace, os.getuid() + 1) as broker,
    ):
        worker = threading.Thread(target=broker.handle_once)
        worker.start()
        try:
            reply = client(broker.socket_path, "git status")
        except (OSError, ValueError):
            reply = {"exit": 126}
        worker.join(timeout=3)
        assert reply["exit"] == 126
