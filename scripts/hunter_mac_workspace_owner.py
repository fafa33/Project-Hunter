"""Fixed-scope macOS workspace ownership utility (not an installation script)."""

from __future__ import annotations

import os
import pwd
import stat
import sys
from pathlib import Path

WORKROOT_PARENT = Path("/private/tmp")
EXECUTOR = "hunterexecutor"


def _open_directory(name: str, parent_fd: int) -> int:
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)


def _change_tree(fd: int, uid: int, gid: int, device: int) -> None:
    if os.fstat(fd).st_dev != device:
        raise ValueError("cross-device workspace")
    for name in os.listdir(fd):
        entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISLNK(entry.st_mode):
            continue
        if stat.S_ISDIR(entry.st_mode):
            child = _open_directory(name, fd)
            try:
                _change_tree(child, uid, gid, device)
            finally:
                os.close(child)
        elif stat.S_ISREG(entry.st_mode) and entry.st_nlink == 1:
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                opened = os.fstat(child)
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_dev != device:
                    raise ValueError("unsafe workspace file")
                os.fchown(child, uid, gid)
            finally:
                os.close(child)
        else:
            raise ValueError("unsafe workspace entry")
    os.fchown(fd, uid, gid)


def prepare(root: str) -> None:
    if not root.startswith("/private/tmp/hunter-ia.") or "/" in root[len("/private/tmp/") :] or root.endswith("."):
        raise ValueError("invalid workspace path")
    if os.geteuid() != 0:
        raise PermissionError("requires administrative installation")
    helper = os.stat(__file__, follow_symlinks=False)
    if helper.st_uid != 0 or helper.st_mode & 0o022:
        raise PermissionError("helper must be owned by root and not writable")
    account = pwd.getpwnam(EXECUTOR)
    runner = pwd.getpwnam("farhadafshari")
    parent = os.open("/private/tmp", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        root_fd = _open_directory(root[len("/private/tmp/") :], parent)
        try:
            metadata = os.fstat(root_fd)
            if metadata.st_uid != runner.pw_uid or metadata.st_mode & 0o022:
                raise ValueError("unsafe workspace root")
            for name in ("workspace", "home"):
                child = _open_directory(name, root_fd)
                try:
                    _change_tree(child, account.pw_uid, account.pw_gid, metadata.st_dev)
                finally:
                    os.close(child)
        finally:
            os.close(root_fd)
    finally:
        os.close(parent)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "prepare":
        return 2
    try:
        prepare(argv[1])
    except (OSError, ValueError, KeyError):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
