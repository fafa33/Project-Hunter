"""Disposable macOS Seatbelt filesystem probe; never enables Codex fallback.

Run with an already authenticated sudo session. Any launcher/setup failure is
reported as failure, never counted as proof that a forbidden action was denied.
"""

from __future__ import annotations

import os
import secrets
import shutil
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def verified_shared_temp_root() -> Path:
    """Permit cross-user probes only under a root-owned sticky directory."""
    parent = Path("/private/tmp")
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise RuntimeError("probe parent must be a root-owned directory")
    if info.st_mode & 0o002 and not info.st_mode & stat.S_ISVTX:
        raise RuntimeError("probe parent must have the sticky bit")
    return parent


@contextmanager
def isolated_probe_directory(parent: Path) -> Iterator[Path]:
    """Atomically create an unpredictable, private child under a verified sticky parent."""
    for _ in range(5):
        root = parent / ("hunter-codex-probe-" + secrets.token_hex(16))
        try:
            root.mkdir(mode=0o700)
            break
        except FileExistsError:
            continue
    else:
        raise RuntimeError("unable to allocate an isolated probe directory")
    try:
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("unsafe probe directory creation")
        yield root
    finally:
        shutil.rmtree(root)


def main() -> int:
    if os.uname().sysname != "Darwin":
        print("BLOCKED: macOS required")
        return 1
    try:
        parent = verified_shared_temp_root()
    except (OSError, RuntimeError) as exc:
        print(f"BLOCKED: unsafe shared probe parent: {exc}")
        return 1
    # A random 128-bit name and atomic 0700 mkdir prevent shared-parent races.
    # Permissions are opened only after ownership and mode verification.
    with isolated_probe_directory(parent) as root:
        root.chmod(0o755)
        workspace = root / "workspace"
        workspace.mkdir(mode=0o755)
        workspace.chmod(0o777)  # Disposable test directory only; not production.
        private = root / "private.txt"
        private.write_text("DISPOSABLE_PRIVATE_PROBE\n")
        private.chmod(0o644)  # Must be denied by sandbox, not Unix permissions.
        profile = root / "probe.sb"
        profile.write_text(
            "(version 1)\n"
            "(deny default)\n"
            "(allow process*)\n"
            "(allow sysctl-read)\n"
            '(allow file-read* (literal "/") (subpath "/usr") (subpath "/System") '
            '(subpath "/Library") (subpath "/bin") (subpath "/sbin") '
            f'(subpath "{workspace}"))\n'
            f'(allow file-write* (subpath "{workspace}"))\n'
        )
        profile.chmod(0o644)
        cases = (
            ("identity", ["/usr/bin/id", "-u"], True),
            ("workspace-write", ["/usr/bin/touch", str(workspace / "test")], True),
            ("private-read", ["/bin/cat", str(private)], False),
            ("outside-write", ["/usr/bin/touch", str(root / "outside")], False),
        )
        ok = True
        for name, command, should_succeed in cases:
            try:
                result = subprocess.run(
                    [
                        "/usr/bin/sudo",
                        "-n",
                        "-u",
                        "hunteragent",
                        "--",
                        "/usr/bin/sandbox-exec",
                        "-f",
                        str(profile),
                        *command,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"{name}: SETUP_ERROR {type(exc).__name__}")
                ok = False
                continue
            stderr = result.stderr.lower()
            setup_error = result.returncode in (65, 126, 127) or any(
                phrase in stderr
                for phrase in (
                    "password is required",
                    "permission denied: sandbox",
                    "sandbox.sb: permission denied",
                    "probe.sb: permission denied",
                    "operation not permitted: sandbox-exec",
                )
            )
            passed = not setup_error and (result.returncode == 0) == should_succeed
            if name == "identity":
                passed = passed and result.stdout.strip() == "502"
            if name == "private-read" and "DISPOSABLE_PRIVATE_PROBE" in result.stdout:
                passed = False
            print(f"{name}: {'PASS' if passed else 'FAIL'} (exit={result.returncode})")
            if not passed:
                print("diagnostic:", result.stderr[:250])
            ok &= passed
        print("SANDBOX_PROBE=" + ("PASS" if ok else "BLOCKED"))
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
