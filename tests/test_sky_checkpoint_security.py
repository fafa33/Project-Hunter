"""Security regression tests for SKY checkpoint filesystem boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hunter.comparative_valuation.sky_checkpoint_security import checkpoint_path, read_checkpoint, write_checkpoint


def test_private_checkpoint_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "sky"
    monkeypatch.setenv("HUNTER_SKY_CHECKPOINT_DIR", str(root))
    path = root / "audit.json"
    write_checkpoint(path, {"next": 2})
    assert read_checkpoint(path) == {"next": 2}
    assert root.stat().st_mode & 0o077 == 0
    assert json.loads(path.read_text()) == {"next": 2}


@pytest.mark.parametrize("name", ["../outside.json", "subdir/audit.json", "../sky-other/audit.json"])
def test_rejects_traversal_and_nested_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    root = tmp_path / "sky"
    monkeypatch.setenv("HUNTER_SKY_CHECKPOINT_DIR", str(root))
    with pytest.raises(ValueError):
        checkpoint_path(root / name)


def test_rejects_symlink_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "sky"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("HUNTER_SKY_CHECKPOINT_DIR", str(root))
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret": true}')
    (root / "audit.json").symlink_to(outside)
    with pytest.raises(ValueError):
        read_checkpoint(root / "audit.json")
    with pytest.raises(ValueError):
        write_checkpoint(root / "audit.json", {"next": 1})
    assert outside.read_text() == '{"secret": true}'


def test_rejects_world_readable_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "sky"
    root.mkdir(mode=0o755)
    monkeypatch.setenv("HUNTER_SKY_CHECKPOINT_DIR", str(root))
    with pytest.raises(ValueError):
        write_checkpoint(root / "audit.json", {})


def test_rejects_hardlinked_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    root = tmp_path / "sky"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("HUNTER_SKY_CHECKPOINT_DIR", str(root))
    original = tmp_path / "original.json"
    original.write_text("{}")
    os.link(original, root / "audit.json")
    with pytest.raises(ValueError):
        checkpoint_path(root / "audit.json")
