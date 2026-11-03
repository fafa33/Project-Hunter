"""Read-only, fail-closed prerequisite check for a dedicated macOS Codex executor.

This is not a runner activation mechanism or proof of sandbox containment.
"""

from __future__ import annotations

import argparse
import os
import platform
import pwd
import stat
import subprocess
from pathlib import Path


def verify_principal(user: str, *, current_uid: int, owner_home: Path) -> list[str]:
    failures: list[str] = []
    try:
        account = pwd.getpwnam(user)
    except KeyError:
        return ["ISOLATION_ACCOUNT_MISSING"]
    if account.pw_uid in (0, current_uid):
        failures.append("ISOLATION_UID_NOT_DISTINCT")
    if not Path(account.pw_dir).is_dir():
        failures.append("ISOLATION_HOME_MISSING")
    # Home permissions must deny traversal to other users, not just deny
    # listing. ACLs must be independently audited before activation.
    try:
        mode = owner_home.stat().st_mode
        if mode & (stat.S_IXGRP | stat.S_IXOTH):
            failures.append("OWNER_HOME_TRAVERSABLE")
    except OSError:
        failures.append("OWNER_HOME_UNVERIFIED")
    return failures


def check(user: str, owner_home: Path) -> list[str]:
    # The sudo target is a fixed, provisioned principal, never caller input.
    if user not in {"hunteragent", "hunterexecutor"}:
        return ["UNAPPROVED_EXECUTOR_USER"]
    failures = []
    if platform.system() != "Darwin":
        failures.append("NOT_MACOS")
    failures.extend(verify_principal(user, current_uid=os.getuid(), owner_home=owner_home))
    if not Path("/usr/bin/sandbox-exec").is_file():
        failures.append("SANDBOX_BINARY_MISSING")
    # Never read auth.json: only inspect metadata. Credentials must belong to
    # the isolated user, not be copied from the runner's personal account.
    try:
        isolated = pwd.getpwnam(user)
        auth = Path(isolated.pw_dir) / ".codex" / "auth.json"
        if not auth.is_file():
            failures.append("DEDICATED_CODEX_AUTH_MISSING")
        else:
            info = auth.stat()
            if info.st_uid != isolated.pw_uid or (stat.S_IMODE(info.st_mode) & 0o077):
                failures.append("DEDICATED_CODEX_AUTH_PERMISSIONS")
    except (KeyError, OSError):
        failures.append("DEDICATED_CODEX_AUTH_UNVERIFIED")
    # No password prompt; no privilege change and no secret values passed.
    try:
        probe = subprocess.run(
            ("/usr/bin/sudo", "-n", "-u", user, "--", "/usr/bin/id", "-u"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        if probe.returncode != 0:
            failures.append("NONINTERACTIVE_SUDO_UNVERIFIED")
    except (OSError, subprocess.TimeoutExpired):
        failures.append("NONINTERACTIVE_SUDO_UNVERIFIED")
    # Even an empty failure list is NOT proof of sandbox isolation or credential
    # safety; explicit adversarial tests and a trusted runner review are required.
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default="hunteragent")
    parser.add_argument("--owner-home", type=Path, required=True)
    args = parser.parse_args()
    failures = check(args.user, args.owner_home)
    print("mac_codex_preflight=" + ("BLOCKED:" + ",".join(failures) if failures else "PREREQUISITES_ONLY"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
