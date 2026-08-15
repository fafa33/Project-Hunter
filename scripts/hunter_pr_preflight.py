from __future__ import annotations

import argparse
import subprocess
from collections.abc import Sequence

QUALITY_GATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Ruff", ("ruff", "check", ".")),
    ("Black", ("black", "--check", "--diff", ".")),
    ("Mypy", ("mypy",)),
    ("Pytest", ("pytest",)),
)


def run_quality_gates(gates: Sequence[tuple[str, Sequence[str]]] = QUALITY_GATES) -> int:
    """Run the repository-local deterministic quality gates, failing fast."""
    for name, command in gates:
        printable = " ".join(command)
        print(f"[Hunter Pre-PR] {name}: {printable}", flush=True)
        completed = subprocess.run(tuple(command), check=False)
        if completed.returncode != 0:
            print(f"[Hunter Pre-PR] FAIL: {name} exited {completed.returncode}", flush=True)
            return completed.returncode
        print(f"[Hunter Pre-PR] PASS: {name}", flush=True)
    print(f"[Hunter Pre-PR] PASS: all deterministic repository-local gates", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the exact repository-local deterministic quality gates shared by "
            "feature-branch preflight and GitHub CI."
        )
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the ordered gate commands without executing them.",
    )
    args = parser.parse_args()

    if args.list:
        for name, command in QUALITY_GATES:
            print(f"{name}: {' '.join(command)}")
        return 0

    return run_quality_gates()


if __name__ == "__main__":
    raise SystemExit(main())
