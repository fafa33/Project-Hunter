"""Disposable macOS Seatbelt filesystem probe; never enables Codex fallback.

Run with an already authenticated sudo session. Any launcher/setup failure is
reported as failure, never counted as proof that a forbidden action was denied.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


def main() -> int:
    if os.uname().sysname != "Darwin":
        print("BLOCKED: macOS required")
        return 1
    with tempfile.TemporaryDirectory(
        prefix="hunter-codex-probe-", dir="/private/tmp"
    ) as tmp:  # NOSONAR: disposable macOS probe root
        root = Path(tmp)
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
