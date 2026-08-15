from __future__ import annotations

import argparse
import subprocess
from collections.abc import Sequence
from typing import Literal

PreflightMode = Literal["normal", "tests-first-red"]

HYGIENE_GATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Ruff", ("ruff", "check", ".")),
    ("Black", ("black", "--check", "--diff", ".")),
    ("Mypy", ("mypy",)),
)
NORMAL_QUALITY_GATES: tuple[tuple[str, tuple[str, ...]], ...] = HYGIENE_GATES + (
    ("Pytest", ("pytest",)),
)
TESTS_FIRST_RED_GATES = HYGIENE_GATES
QUALITY_GATES = NORMAL_QUALITY_GATES


def run_quality_gates(gates: Sequence[tuple[str, Sequence[str]]] = NORMAL_QUALITY_GATES) -> int:
    """Run deterministic repository-local gates, failing at the first defect."""
    for name, command in gates:
        printable = " ".join(command)
        print(f"[Hunter Pre-PR] {name}: {printable}", flush=True)
        completed = subprocess.run(tuple(command), check=False)
        if completed.returncode != 0:
            print(f"[Hunter Pre-PR] FAIL: {name} exited {completed.returncode}", flush=True)
            return completed.returncode
        print(f"[Hunter Pre-PR] PASS: {name}", flush=True)
    return 0


def run_preflight_mode(mode: PreflightMode) -> int:
    """Run the exact gate set for one governed pre-PR state."""
    gates = NORMAL_QUALITY_GATES if mode == "normal" else TESTS_FIRST_RED_GATES
    result = run_quality_gates(gates)
    if result != 0:
        return result
    if mode == "tests-first-red":
        print(
            "[Hunter Pre-PR] PASS: tests-first hygiene is clean; expected RED tests remain separately attributable",
            flush=True,
        )
    else:
        print("[Hunter Pre-PR] PASS: all deterministic repository-local gates", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the exact repository-local deterministic quality gates shared by "
            "feature-branch preflight and GitHub CI."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("normal", "tests-first-red"),
        default="normal",
        help=(
            "normal requires Ruff, Black, Mypy, and Pytest green; tests-first-red "
            "requires hygiene green while expected RED tests remain separately attributable"
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the ordered gate commands without executing them.",
    )
    args = parser.parse_args()
    mode: PreflightMode = args.mode

    gates = NORMAL_QUALITY_GATES if mode == "normal" else TESTS_FIRST_RED_GATES
    if args.list:
        for name, command in gates:
            print(f"{name}: {' '.join(command)}")
        return 0

    return run_preflight_mode(mode)


if __name__ == "__main__":
    raise SystemExit(main())
