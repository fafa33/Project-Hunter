"""Fixed-scope macOS workspace ownership utility (not an installation script)."""

from __future__ import annotations

import os
import pwd
import re
import stat
import sys
from pathlib import Path

WORKROOT_PARENT = Path("/usr/local/var/hunter/workroots")
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


def prepare(root: str, *, reclaim: bool = False) -> None:
    prefix = str(WORKROOT_PARENT) + "/"
    if not root.startswith(prefix):
        raise ValueError("invalid workspace path")
    workroot_name = root[len(prefix) :]
    if re.fullmatch(r"hunter-ia\.[A-Za-z0-9]{8}", workroot_name, flags=re.ASCII) is None:
        raise ValueError("invalid workspace path")
    if os.geteuid() != 0:
        raise PermissionError("requires administrative installation")
    helper = os.stat(__file__, follow_symlinks=False)
    if helper.st_uid != 0 or helper.st_mode & 0o022:
        raise PermissionError("helper must be owned by root and not writable")
    account = pwd.getpwnam(EXECUTOR)
    runner = pwd.getpwnam("farhadafshari")
    parent = os.open(WORKROOT_PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent_stat = os.fstat(parent)
        if parent_stat.st_uid != runner.pw_uid or stat.S_IMODE(parent_stat.st_mode) != 0o711:
            raise ValueError("unsafe workspace parent")
        root_fd = _open_directory(workroot_name, parent)
        try:
            metadata = os.fstat(root_fd)
            if metadata.st_uid != runner.pw_uid or metadata.st_mode & 0o022:
                raise ValueError("unsafe workspace root")
            for name in ("workspace", "home"):
                child = _open_directory(name, root_fd)
                try:
                    target = runner if reclaim else account
                    _change_tree(child, target.pw_uid, target.pw_gid, metadata.st_dev)
                finally:
                    os.close(child)
        finally:
            os.close(root_fd)
    finally:
        os.close(parent)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("prepare", "reclaim"):
        return 2
    try:
        prepare(argv[1], reclaim=argv[0] == "reclaim")
    except (OSError, ValueError, KeyError):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
