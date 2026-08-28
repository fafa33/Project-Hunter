from __future__ import annotations

import subprocess
from pathlib import Path

HOOKS_PATH = ".githooks"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        ("git", "config", "core.hooksPath", HOOKS_PATH),
        cwd=root,
        check=False,
    )
    if completed.returncode != 0:
        return completed.returncode
    print(f"Hunter git hooks enabled via core.hooksPath={HOOKS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
