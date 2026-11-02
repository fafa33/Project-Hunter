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
