"""Conservative, deterministic change-impact selector for focused CI smoke tests.

This is a fast-fail optimization only: it never replaces required full-suite proof.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


REVIEW_REQUEST_PATH = ".hunter/pre-ready-hostile-review.json"


def classify_review_metadata_only(changed: list[str]) -> bool:
    """Conservative metadata-only signal; never an authorization to skip proof.

    Empty/unknown diffs and mixed security or code changes always return False.
    Consumers must independently validate exact-head/base and trusted receipts.
    """
    return bool(changed) and all(path == REVIEW_REQUEST_PATH for path in changed)


def classify_cross_head_equivalence_candidate(
    *, changed: list[str], base_unchanged: bool, definitions_unchanged: bool, toolchain_unchanged: bool
) -> bool:
    """Signal only: an eligible metadata diff is NOT a reusable proof.

    The canonical proof authority must separately bind new HEAD, run/attempt,
    job, digest and provenance before any required context can be satisfied.
    """
    return classify_review_metadata_only(changed) and base_unchanged and definitions_unchanged and toolchain_unchanged


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
    # Fixed git subcommand; validated hashes cannot become options or shell syntax.
    command = (
        "git diff --name-only --diff-filter=ACDMRTUXB " + shlex.quote(args.base) + " " + shlex.quote(args.head) + " --"
    )
    result = subprocess.run(shlex.split(command), cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode:
        print("[Hunter CI Impact] FULL REQUIRED: git diff unavailable", flush=True)
        return 2
    available = {str(p.relative_to(ROOT)) for p in (ROOT / "tests").rglob("test_*.py")}
    changed = result.stdout.splitlines()
    if classify_review_metadata_only(changed):
        print(
            "[Hunter CI Impact] REVIEW-METADATA-ONLY: advisory classification; authoritative full proof still required",
            flush=True,
        )
    full, tests, reason = select_tests(changed, available)
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
