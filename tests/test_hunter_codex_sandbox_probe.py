"""Guard the cross-user sandbox probe's temporary directory trust boundary."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts/hunter_codex_sandbox_probe.py"
spec = importlib.util.spec_from_file_location("hunter_codex_sandbox_probe", MODULE)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_probe_requires_root_owned_sticky_parent():
    with patch.object(probe.Path, "lstat") as fake:
        fake.return_value.st_mode = 0o40777
        fake.return_value.st_uid = 0
        with pytest.raises(RuntimeError, match="sticky"):
            probe.verified_shared_temp_root()
        fake.return_value.st_mode = 0o41777
        fake.return_value.st_uid = 501
        with pytest.raises(RuntimeError, match="root-owned"):
            probe.verified_shared_temp_root()
        fake.return_value.st_uid = 0
        assert probe.verified_shared_temp_root() == Path("/private/tmp")


def test_isolated_probe_directory_is_private_and_cleaned(tmp_path):
    with probe.isolated_probe_directory(tmp_path) as root:
        assert root.parent == tmp_path
        assert root.name.startswith("hunter-codex-probe-")
        assert root.stat().st_mode & 0o777 == 0o700
        assert root.is_dir()
    assert not root.exists()


def test_isolated_probe_directory_cleans_on_exception(tmp_path):
    with pytest.raises(RuntimeError, match="test failure"):
        with probe.isolated_probe_directory(tmp_path) as root:
            raise RuntimeError("test failure")
    assert not root.exists()
