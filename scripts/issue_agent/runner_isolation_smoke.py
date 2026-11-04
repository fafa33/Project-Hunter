"""Exercise the exact production Runner Isolation adapter, not a parallel launcher."""

from __future__ import annotations

import tempfile
from pathlib import Path

from hunter.automation.issue_agent_kilo_container import KiloContainerIsolation
from hunter.automation.issue_agent_roles import kilo_invocation


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="hunter-682-runner-") as directory:
        root = Path(directory)
        workspace, home = root / "workspace", root / "home"
        workspace.mkdir()
        home.mkdir()
        (workspace / "test_sample.py").write_text("def test_sample():\n    assert 2 + 2 == 5\n")
        prompt = (
            b"In /workspace/test_sample.py there is a deliberately broken assertion. "
            b"First use the bash tool to run exactly `python -m pytest -q test_sample.py` and observe failure. "
            b"Then use the edit tool to change only the incorrect expected number from 5 to 4 in that file. "
            b"Then use the bash tool to run exactly `python -m pytest -q test_sample.py` again. "
            b"Do not use other bash commands. Report both test results."
        )
        invocation = kilo_invocation(
            ("kilo", "run", "--model", "kilo/kilo-auto/free"), prompt=prompt, home=home, workspace=workspace
        )
        isolation = KiloContainerIsolation(Path(__file__).resolve().parent)
        isolation.prepare(workspace, home)
        try:
            code = isolation.run(
                invocation.argv,
                cwd=workspace,
                public_env=invocation.public_env,
                secret_env={},
                stdin=invocation.stdin,
                timeout=110,
            )
            broker_logs = isolation._docker("logs", isolation.broker).stdout.decode(errors="replace")
        finally:
            isolation.stop()
        print("SMOKE_BROKER", broker_logs[-900:], flush=True)
        if (
            code
            or "assert 2 + 2 == 4" not in (workspace / "test_sample.py").read_text()
            or broker_logs.count("BROKER_EXECUTED:") != 2
            or "EXIT: 1" not in broker_logs
            or "EXIT: 0" not in broker_logs
        ):
            raise RuntimeError("Runner isolation did not complete real repair")
        print("RUNNER_ISOLATION_REAL_KILO_REPAIR_PASS")


if __name__ == "__main__":
    main()
