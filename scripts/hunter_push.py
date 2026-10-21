#!/usr/bin/env python3
"""Portable, fail-closed Hunter Git push launcher with a persistent toolchain.

Usage: python3 scripts/hunter_push.py [git-push-arguments]
Provision once per host: python3 scripts/hunter_push.py --setup
"""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

TOOLS = ("python", "ruff", "black", "mypy")
DEFAULT_HOME = Path.home() / ".local" / "share" / "hunter" / "push-venv"


def environment_path() -> Path:
    return Path(os.environ.get("HUNTER_PUSH_VENV", str(DEFAULT_HOME))).expanduser()


def hook_bound() -> bool:
    root = Path(__file__).resolve().parents[1]
    hook = root / ".githooks" / "pre-push"
    result = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        result.returncode == 0 and result.stdout.strip() == ".githooks" and hook.is_file() and os.access(hook, os.X_OK)
    )


def setup(target: Path) -> int:
    if not (target / "bin" / "python").is_file():
        venv.EnvBuilder(with_pip=True).create(target)
    root = Path(__file__).resolve().parents[1]
    result = subprocess.call([str(target / "bin" / "python"), "-m", "pip", "install", "-e", f"{root}[dev]"])
    if result != 0:
        return result
    return subprocess.call([sys.executable, str(root / "scripts" / "install_hunter_git_hooks.py")])


def main(argv: list[str]) -> int:
    target = environment_path()
    if argv == ["--setup"]:
        return setup(target)
    missing = [name for name in TOOLS if not (target / "bin" / name).exists()]
    if missing:
        print(
            "Hunter push toolchain missing: " + ", ".join(missing) + "\nRun: python3 scripts/hunter_push.py --setup",
            file=sys.stderr,
        )
        return 70
    if not hook_bound():
        print("Hunter pre-push hook is not bound; run: python3 scripts/hunter_push.py --setup", file=sys.stderr)
        return 70
    env = os.environ.copy()
    env["PATH"] = (
        str(target / "bin") + os.pathsep + str(Path.home() / ".local" / "bin") + os.pathsep + env.get("PATH", "")
    )
    return subprocess.call(["git", "push", *argv], env=env)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
