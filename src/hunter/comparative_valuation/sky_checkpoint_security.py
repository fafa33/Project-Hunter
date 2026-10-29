"""Constrain local SKY audit checkpoints to a dedicated, private workspace."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path


def checkpoint_path(path: Path) -> Path:
    """Reject arbitrary CLI paths and symlinks, including existing checkpoint targets."""
    root = Path(os.environ.get("HUNTER_SKY_CHECKPOINT_DIR", str(Path.home() / ".local/state/hunter/sky")))
    if not root.is_absolute() or not path.is_absolute():
        raise ValueError("absolute SKY checkpoint path and root required")
    if root.is_symlink() or path.is_symlink() or path.parent.is_symlink():
        raise ValueError("symlink checkpoint paths are forbidden")
    if re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}\.json", path.name) is None or ".." in path.name:
        raise ValueError("checkpoint must be a named JSON file")
    if path.parent != root or path.name in (".", ".."):
        raise ValueError("checkpoint must be directly within the configured SKY checkpoint directory")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.resolve(strict=True) != root or root.stat().st_mode & 0o077:
        raise ValueError("SKY checkpoint directory must be private and non-symlinked")
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise ValueError("unsafe checkpoint target")
    return root / path.name


def _validated_checkpoint_name(name: str) -> str:
    """Only a single portable filename may cross the directory-fd IO boundary."""
    if re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}\.json", name) is None or ".." in name:
        raise ValueError("invalid SKY checkpoint filename")
    return name


def _private_directory_fd() -> int:
    """Anchor checkpoint operations to a verified private directory inode."""
    directory = Path(os.environ.get("HUNTER_SKY_CHECKPOINT_DIR", str(Path.home() / ".local/state/hunter/sky")))
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(directory, flags)
    try:
        stat = os.fstat(fd)
        if stat.st_mode & 0o077 or os.fstat(fd).st_ino != directory.stat().st_ino:
            raise ValueError("SKY checkpoint directory changed or is not private")
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_checkpoint(path: Path) -> dict:
    checked = checkpoint_path(path)
    name = _validated_checkpoint_name(checked.name)
    directory_fd = _private_directory_fd()
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(name, flags, dir_fd=directory_fd)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            if os.fstat(handle.fileno()).st_nlink != 1:
                raise ValueError("unsafe checkpoint target")
            state = json.load(handle)
    finally:
        os.close(directory_fd)
    if not isinstance(state, dict):
        raise ValueError("invalid SKY checkpoint state")
    return state


def write_checkpoint(path: Path, state: dict) -> None:
    checked = checkpoint_path(path)
    name = _validated_checkpoint_name(checked.name)
    serialized = json.dumps(state, sort_keys=True, indent=2) + "\n"
    directory_fd = _private_directory_fd()
    temporary_name = None
    try:
        # Secure temporary file is created in the already verified directory.
        fd, temporary = tempfile.mkstemp(prefix=".sky-", suffix=".tmp", dir=checked.parent)
        temporary_name = Path(temporary).name
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            checkpoint_path(checked)
            os.replace(temporary_name, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            temporary_name = None
        finally:
            if temporary_name is not None:
                os.unlink(temporary_name, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)
