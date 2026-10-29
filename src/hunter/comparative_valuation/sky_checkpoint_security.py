"""Constrain local SKY audit checkpoints to a dedicated, private workspace."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def checkpoint_path(path: Path) -> Path:
    """Reject arbitrary CLI paths and symlinks, including existing checkpoint targets."""
    root = Path(os.environ.get("HUNTER_SKY_CHECKPOINT_DIR", str(Path.home() / ".local/state/hunter/sky")))
    if not root.is_absolute() or not path.is_absolute():
        raise ValueError("absolute SKY checkpoint path and root required")
    if root.is_symlink() or path.is_symlink() or path.parent.is_symlink():
        raise ValueError("symlink checkpoint paths are forbidden")
    if not path.name.endswith(".json") or path.name in (".json", "..json"):
        raise ValueError("checkpoint must be a named JSON file")
    if path.parent != root or path.name in (".", ".."):
        raise ValueError("checkpoint must be directly within the configured SKY checkpoint directory")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.resolve(strict=True) != root or root.stat().st_mode & 0o077:
        raise ValueError("SKY checkpoint directory must be private and non-symlinked")
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise ValueError("unsafe checkpoint target")
    return path


def read_checkpoint(path: Path) -> dict:
    path = checkpoint_path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        state = json.load(handle)
    if not isinstance(state, dict):
        raise ValueError("invalid SKY checkpoint state")
    return state


def write_checkpoint(path: Path, state: dict) -> None:
    path = checkpoint_path(path)
    serialized = json.dumps(state, sort_keys=True, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".sky-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        checkpoint_path(path)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
