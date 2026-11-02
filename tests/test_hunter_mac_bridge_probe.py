from unittest.mock import patch

import pytest

from hunter.automation.mac_bridge_probe import receipt

SHA = "a" * 40


def test_readonly_receipt(tmp_path):
    with patch("platform.system", return_value="Darwin"), patch("subprocess.check_output", return_value=SHA + "\n"):
        result = receipt("fafa33/Project-Hunter", SHA, "macOS", root=tmp_path)
    assert result["outcome"] == "PASS" and result["mode"] == "READ_ONLY"
    assert result["trusted_head"] == SHA


@pytest.mark.parametrize(
    "repo,sha,osname",
    [("evil/repo", SHA, "macOS"), ("fafa33/Project-Hunter", "bad", "macOS"), ("fafa33/Project-Hunter", SHA, "Linux")],
)
def test_fail_closed_inputs(repo, sha, osname, tmp_path):
    with patch("platform.system", return_value="Darwin"):
        with pytest.raises(ValueError):
            receipt(repo, sha, osname, root=tmp_path)


def test_stale_head_fails(tmp_path):
    with (
        patch("platform.system", return_value="Darwin"),
        patch("subprocess.check_output", return_value="b" * 40 + "\n"),
    ):
        with pytest.raises(ValueError, match="stale"):
            receipt("fafa33/Project-Hunter", SHA, "macOS", root=tmp_path)


def test_cli_bootstraps_without_installed_hunter_package():
    """Regression: a bare self-hosted runner must not need the Hunter package."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(repo_root / "scripts" / "hunter_mac_bridge_probe.py"),
            "--repository",
            "untrusted/repo",
            "--head",
            SHA,
        ],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "untrusted repository" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr
