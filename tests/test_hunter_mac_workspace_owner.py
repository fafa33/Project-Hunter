"""Adversarial checks for the fixed-scope macOS ownership helper."""

import importlib.util
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hunter_mac_workspace_owner.py"
spec = importlib.util.spec_from_file_location("hunter_mac_workspace_owner", SCRIPT)
assert spec and spec.loader
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@pytest.mark.parametrize(
    "root",
    [
        "/etc",
        "/private/tmp/hunter-ia.x/../../etc",
        "/private/tmp/hunter-ia.",
        "/private/tmp/hunter-ia.x/other",
        "/tmp/hunter-ia.x",
    ],
)
def test_rejects_unapproved_workroots(root):
    with pytest.raises(ValueError, match="invalid workspace path"):
        helper.prepare(root)


def test_symlink_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "link").symlink_to(outside)
    fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    try:
        helper._change_tree(fd, os.getuid(), os.getgid(), os.fstat(fd).st_dev)
    finally:
        os.close(fd)
    assert outside.read_text() == "untouched"


def test_hardlink_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    os.link(outside, workspace / "linked")
    fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match="unsafe workspace entry"):
            helper._change_tree(fd, os.getuid(), os.getgid(), os.fstat(fd).st_dev)
    finally:
        os.close(fd)
    assert outside.read_text() == "untouched"
