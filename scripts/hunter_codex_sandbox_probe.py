"""Disposable macOS Seatbelt filesystem probe; never enables Codex fallback.

Run with an already authenticated sudo session. Any launcher/setup failure is
reported as failure, never counted as proof that a forbidden action was denied.
"""

from __future__ import annotations

import os
import pwd
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

AGENT_USER = "hunteragent"
AS_AGENT = ["/usr/bin/sudo", "-n", "-u", AGENT_USER, "--"]
# Paths arrive as sandbox parameters, never spliced into the profile text.
PROFILE = (
    "(version 1)\n"
    "(deny default)\n"
    "(allow process*)\n"
    "(allow sysctl-read)\n"
    '(allow file-read* (literal "/") (subpath "/usr") (subpath "/System") '
    '(subpath "/Library") (subpath "/bin") (subpath "/sbin") '
    '(subpath (param "WORKSPACE")))\n'
    '(allow file-write* (subpath (param "WORKSPACE")))\n'
)


def as_agent(argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*AS_AGENT, *argv], input=stdin, capture_output=True, text=True, timeout=10, check=False)


def verified_agent_root(reported: str, agent_uid: int) -> Path:
    """Accept only one absolute, agent-owned, private (0700) real directory as the probe root."""
    lines = reported.splitlines()
    if len(lines) != 1 or not Path(lines[0]).is_absolute():
        raise RuntimeError("agent reported an unusable probe directory")
    root = Path(lines[0]).resolve(strict=True)  # sandbox matching needs the real path
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != agent_uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError("probe directory is not a private directory owned by the agent")
    return root


@contextmanager
def agent_probe_directory(agent_uid: int) -> Iterator[Path]:
    """hunteragent atomically allocates its own 0700 directory; nothing is shared with other users."""
    made = as_agent(["/usr/bin/mktemp", "-d", "-t", "hunter-codex-probe"])
    if made.returncode != 0:
        raise RuntimeError("agent could not allocate a private probe directory")
    root = verified_agent_root(made.stdout, agent_uid)
    try:
        yield root
    finally:
        as_agent(["/bin/rm", "-rf", "--", str(root)])


def main() -> int:
    if os.uname().sysname != "Darwin":
        print("BLOCKED: macOS required")
        return 1
    try:
        agent_uid = pwd.getpwnam(AGENT_USER).pw_uid
        with agent_probe_directory(agent_uid) as root:
            return run_cases(root)
    except (KeyError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"BLOCKED: probe setup failed: {type(exc).__name__}: {exc}")
        return 1


def run_cases(root: Path) -> int:
    workspace = root / "workspace"
    private = root / "private.txt"
    # The agent owns everything, so Unix allows these; only the sandbox can deny them.
    for argv, stdin in (
        (["/bin/mkdir", "-m", "0700", str(workspace)], None),
        (["/usr/bin/tee", str(private)], "DISPOSABLE_PRIVATE_PROBE\n"),
    ):
        if as_agent(argv, stdin).returncode != 0:
            raise RuntimeError(f"agent setup failed: {argv[0]}")
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
                    "-D",
                    f"WORKSPACE={workspace}",
                    "-p",
                    PROFILE,
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
