"""Guard the cross-user sandbox probe: setup lives in an agent-owned private directory, never a shared one."""

import importlib.util
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts/hunter_codex_sandbox_probe.py"
spec = importlib.util.spec_from_file_location("hunter_codex_sandbox_probe", MODULE)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def _done(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def _private_dir(tmp_path: Path) -> Path:
    root = tmp_path / "probe"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def test_a_private_directory_owned_by_the_agent_is_accepted(tmp_path: Path) -> None:
    root = _private_dir(tmp_path)
    assert probe.verified_agent_root(f"{root}\n", os.getuid()) == root.resolve()


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o701, 0o777, 0o1777])
def test_a_directory_readable_or_traversable_by_others_is_refused(tmp_path: Path, mode: int) -> None:
    root = _private_dir(tmp_path)
    root.chmod(mode)
    with pytest.raises(RuntimeError, match="private directory owned by the agent"):
        probe.verified_agent_root(str(root), os.getuid())


def test_a_directory_owned_by_anyone_but_the_agent_is_refused(tmp_path: Path) -> None:
    root = _private_dir(tmp_path)
    with pytest.raises(RuntimeError, match="owned by the agent"):
        probe.verified_agent_root(str(root), os.getuid() + 1)


def test_a_regular_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_text("")
    path.chmod(0o700)
    with pytest.raises(RuntimeError, match="private directory"):
        probe.verified_agent_root(str(path), os.getuid())


@pytest.mark.parametrize("reported", ["", "relative/dir", "/a\n/b", "\n"])
def test_an_unusable_report_is_refused(reported: str) -> None:
    with pytest.raises(RuntimeError, match="unusable"):
        probe.verified_agent_root(reported, os.getuid())


def test_a_missing_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        probe.verified_agent_root(str(tmp_path / "absent"), os.getuid())


def test_the_agent_allocates_and_removes_its_own_directory(tmp_path: Path) -> None:
    root = _private_dir(tmp_path)
    with patch.object(probe, "as_agent", return_value=_done(f"{root}\n")) as agent:
        with probe.agent_probe_directory(os.getuid()) as allocated:
            assert allocated == root.resolve()
    allocate, cleanup = (call.args[0] for call in agent.call_args_list)
    assert allocate == ["/usr/bin/mktemp", "-d", "-t", "hunter-codex-probe"]
    assert cleanup == ["/bin/rm", "-rf", "--", str(root.resolve())]


def test_the_directory_is_removed_even_when_a_case_raises(tmp_path: Path) -> None:
    root = _private_dir(tmp_path)
    with patch.object(probe, "as_agent", return_value=_done(str(root))) as agent:
        with pytest.raises(RuntimeError, match="case failure"):
            with probe.agent_probe_directory(os.getuid()):
                raise RuntimeError("case failure")
    assert agent.call_args_list[-1].args[0][:3] == ["/bin/rm", "-rf", "--"]


def test_an_unverified_path_is_never_removed(tmp_path: Path) -> None:
    root = _private_dir(tmp_path)
    root.chmod(0o755)
    with patch.object(probe, "as_agent", return_value=_done(str(root))) as agent:
        with pytest.raises(RuntimeError):
            with probe.agent_probe_directory(os.getuid()):
                pytest.fail("an unverified directory must never be used")
    assert agent.call_count == 1


def test_a_failed_allocation_is_a_setup_failure_not_a_denial() -> None:
    with patch.object(probe, "as_agent", return_value=_done(returncode=1)):
        with pytest.raises(RuntimeError, match="could not allocate"):
            with probe.agent_probe_directory(os.getuid()):
                pytest.fail("no directory was allocated")


def test_every_setup_command_runs_as_the_agent() -> None:
    with patch.object(probe.subprocess, "run", return_value=_done()) as run:
        probe.as_agent(["/usr/bin/true"])
    assert run.call_args.args[0] == ["/usr/bin/sudo", "-n", "-u", "hunteragent", "--", "/usr/bin/true"]


def test_paths_reach_the_profile_only_as_parameters() -> None:
    assert '(param "WORKSPACE")' in probe.PROFILE
    assert "/private" not in probe.PROFILE and "/tmp" not in probe.PROFILE


def test_a_setup_failure_reports_blocked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(probe.os, "uname") as uname, patch.object(probe.pwd, "getpwnam", side_effect=KeyError("x")):
        uname.return_value.sysname = "Darwin"
        assert probe.main() == 1
    assert "BLOCKED: probe setup failed" in capsys.readouterr().out
