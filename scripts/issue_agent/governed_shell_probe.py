"""Issue #682: fail-closed container boundary smoke test (not production agent wiring)."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def probe(image: str = "python:3.12-slim") -> None:
    with tempfile.TemporaryDirectory(prefix="hunter-682-") as root:
        workspace = Path(root) / "workspace"
        workspace.mkdir()
        (workspace / "test_task.py").write_text(
            "def test_agent_result():\n    assert (2 + 2) == 4\n", encoding="utf-8"
        )
        command = [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "64", "--memory", "256m", "--cpus", "0.5",
            "--user", "65534:65534", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            "--mount", f"type=bind,src={workspace},dst=/workspace,readonly",
            "--workdir", "/workspace", "--entrypoint", "python", image,
            "-c", "import test_task; test_task.test_agent_result(); print('isolated test passed')",
        ]
        subprocess.run(command, check=True, timeout=60)


if __name__ == "__main__":
    probe()
