"""Conservative, deterministic change-impact selector for focused CI smoke tests.

This is a fast-fail optimization only: it never replaces required full-suite proof.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def select_tests(changed: list[str], available: set[str]) -> tuple[bool, tuple[str, ...], str]:
    """Return full-proof requirement, focused tests and audit reason."""
    if not changed:
        return True, (), "empty diff or unknown provenance"
    selected: set[str] = set()
    for path in changed:
        if path.startswith("tests/") and path.endswith(".py") and path in available:
            selected.add(path)
        elif path.startswith("scripts/") and path.endswith(".py"):
            stem = Path(path).stem
            matches = {p for p in available if Path(p).stem in {f"test_{stem}", f"test_{stem.removeprefix('hunter_')}"}}
            if not matches:
                return True, (), f"unmapped script: {path}"
            selected.update(matches)
        else:
            return True, (), f"cross-cutting or unclassified path: {path}"
    return False, tuple(sorted(selected)), "mapped focused tests; full proof still required by policy"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--run-focused", action="store_true")
    args = parser.parse_args()
    if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (args.base, args.head)):
        print("[Hunter CI Impact] FULL REQUIRED: base/head must be exact 40-hex commit IDs", flush=True)
        return 2
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACDMRTUXB", args.base, args.head, "--"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        print("[Hunter CI Impact] FULL REQUIRED: git diff unavailable", flush=True)
        return 2
    available = {str(p.relative_to(ROOT)) for p in (ROOT / "tests").rglob("test_*.py")}
    full, tests, reason = select_tests(result.stdout.splitlines(), available)
    print(f"[Hunter CI Impact] {'FULL REQUIRED' if full else 'FOCUSED FIRST'}: {reason}", flush=True)
    if tests:
        print("[Hunter CI Impact] selected: " + " ".join(tests), flush=True)
        if args.run_focused and not full:
            env = dict(os.environ)
            env.pop("PYTEST_ADDOPTS", None)
            completed = subprocess.run([sys.executable, "-m", "pytest", "-q", *tests], cwd=ROOT, env=env, check=False)
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
