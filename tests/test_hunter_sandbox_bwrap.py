"""Real-bubblewrap tests of the sandbox that candidate code runs in (no mocks of the isolation itself).

Locally, and on any lane that has not provisioned bubblewrap, the tests skip. A workflow that provisions
bubblewrap records that by creating ``$RUNNER_TEMP/hunter-bwrap-provisioned``; on such a lane an unusable sandbox
fails collection instead of skipping, because a silently skipped isolation test would be a hole in the gate.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import hunter_defect_prevention_preflight as prevention
import hunter_finding_proof as proof
import pytest

PROVISIONED_MARKER = "hunter-bwrap-provisioned"


def _provisioned() -> bool:
    runner_temp = os.environ.get("RUNNER_TEMP")
    return bool(runner_temp) and (Path(runner_temp) / PROVISIONED_MARKER).exists()


def _usable() -> bool:
    if not proof.trusted_bwrap():
        return False
    with tempfile.TemporaryDirectory() as scratch:
        view = Path(scratch)
        probe = subprocess.run(
            [*proof.sandbox_prefix(view, view, writable=True), "true"], capture_output=True, check=False
        )
    return probe.returncode == 0


if not _usable():
    if _provisioned():
        raise RuntimeError("bubblewrap was provisioned for this job but is unusable; refusing to skip isolation tests")
    pytestmark = pytest.mark.skip(reason="bubblewrap with user namespaces is unavailable")


def _inside(code: str, tmp_path: Path, *, env: dict[str, str] | None = None, overlays=()):
    """Run Python ``code`` in a private writable copy of ``tmp_path/'cand'`` bound at its own path."""
    candidate = tmp_path / "cand"
    candidate.mkdir(exist_ok=True)
    with prevention.private_candidate_copy(candidate) as copy:
        argv = [
            *proof.sandbox_prefix(copy, candidate, writable=True, overlays=overlays),
            sys.executable,
            "-c",
            code,
        ]
        result = subprocess.run(
            argv,
            cwd=copy,
            env=env if env is not None else prevention.gate_environment(os.environ),
            capture_output=True,
            text=True,
            check=False,
        )
        return result, copy


def test_home_and_credential_trees_are_masked(tmp_path: Path):
    secret = Path.home() / ".hunter-bwrap-secret"
    secret.write_text("credential")
    try:
        result, _ = _inside(
            f"import os; print(os.path.exists({str(secret)!r}), os.listdir('/root') if os.path.isdir('/root') else [])",
            tmp_path,
        )
    finally:
        secret.unlink(missing_ok=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split(None, 1)[0] == "False"
    assert result.stdout.strip().endswith("[]")


def test_tokens_and_runner_secrets_are_not_in_the_candidate_environment(monkeypatch, tmp_path: Path):
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_RUNTIME_TOKEN", "HUNTER_ISSUE_AGENT_PUBLISHER_PUSH_TOKEN"):
        monkeypatch.setenv(name, "secret-value")
    result, _ = _inside("import os; print(sorted(os.environ))", tmp_path)
    assert result.returncode == 0, result.stderr
    assert "TOKEN" not in result.stdout and "secret-value" not in result.stdout


def test_host_filesystem_is_read_only_and_tmp_is_private(tmp_path: Path):
    host_marker = Path("/tmp") / f"hunter-bwrap-marker-{os.getpid()}"
    code = (
        "import os\n"
        "def writable(path):\n"
        "    try:\n"
        "        open(path, 'w').close()\n"
        "    except OSError:\n"
        "        return False\n"
        "    return True\n"
        f"print(writable('/usr/hunter-probe'), writable('/etc/hunter-probe'), writable({str(host_marker)!r}))\n"
    )
    result, _ = _inside(code, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "False", "True"]  # /tmp is writable, but only the private tmpfs
    assert not host_marker.exists()


def test_writes_land_in_the_private_copy_never_the_original(tmp_path: Path):
    result, _ = _inside(
        "open('written-by-candidate.txt', 'w').write('x'); import os; print(os.path.exists('written-by-candidate.txt'))",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"  # it landed in the throwaway copy...
    assert not (tmp_path / "cand" / "written-by-candidate.txt").exists()  # ...and never in the original


def test_symlinks_cannot_reach_masked_secrets_or_write_outside(tmp_path: Path):
    secret = Path.home() / ".hunter-bwrap-link-secret"
    secret.write_text("credential")
    candidate = tmp_path / "cand"
    candidate.mkdir()
    (candidate / "read-link").symlink_to(secret)
    (candidate / "write-link").symlink_to("/etc/hunter-link-write")
    (candidate / "up-link").symlink_to("../../../../../../../etc/hostname")
    try:
        code = (
            "import os\n"
            "readable = os.path.exists('read-link')\n"
            "try:\n"
            "    open('write-link', 'w').close()\n"
            "    wrote = True\n"
            "except OSError:\n"
            "    wrote = False\n"
            "print(readable, wrote)\n"
        )
        result, _ = _inside(code, tmp_path)
    finally:
        secret.unlink(missing_ok=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "False"]
    assert not Path("/etc/hunter-link-write").exists()


def test_the_network_is_unreachable_even_on_loopback(tmp_path: Path):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        code = (
            "import socket\n"
            "try:\n"
            f"    socket.create_connection(('127.0.0.1', {port}), timeout=3)\n"
            "    print('connected')\n"
            "except OSError:\n"
            "    print('blocked')\n"
        )
        result, _ = _inside(code, tmp_path)
    finally:
        server.close()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked"


def test_no_capabilities_survive_inside(tmp_path: Path):
    result, _ = _inside(
        "print([line.split()[1] for line in open('/proc/self/status') if line.startswith('CapEff')][0])", tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip(), 16) == 0


def test_guard_logic_comes_from_the_trusted_baseline_not_the_candidate(tmp_path: Path):
    trusted = tmp_path / "trusted-guard.py"
    trusted.write_text("print('trusted-guard-ran')\n")
    candidate = tmp_path / "cand"
    (candidate / "scripts").mkdir(parents=True)
    (candidate / "scripts" / "guard.py").write_text("print('candidate-no-op')\n")
    result, _ = _inside(
        "import runpy; runpy.run_path('scripts/guard.py')",
        tmp_path,
        overlays=[(trusted, candidate / "scripts" / "guard.py")],
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "trusted-guard-ran"


def test_a_failing_gate_reports_its_exit_status(tmp_path: Path):
    candidate = tmp_path / "cand"
    candidate.mkdir()
    code = prevention.execute_gate(
        [sys.executable, "-c", "raise SystemExit(7)"],
        copy=candidate,
        view=candidate,
        env=prevention.gate_environment(os.environ),
        overlays=[],
    )
    assert code == 7


def test_the_controller_fails_closed_when_isolation_is_unavailable(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(proof, "trusted_bwrap", lambda: None)
    monkeypatch.setattr(prevention.finding_proof, "collect_errors", lambda root: [])
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    assert prevention.run_candidate_quality_gates(tmp_path) == 2
    assert "never run unisolated" in capsys.readouterr().out
