from __future__ import annotations

import argparse
import subprocess
from collections.abc import Sequence

NORMAL_MODE = "normal"
TESTS_FIRST_RED_MODE = "tests-first-red"
PREFLIGHT_MODES = (NORMAL_MODE, TESTS_FIRST_RED_MODE)
PYTEST_TEST_FAILURE_EXIT = 1

NORMAL_QUALITY_GATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Artifact Guard", ("python", "scripts/hunter_artifact_preflight.py")),
    ("Ruff", ("ruff", "check", ".")),
    ("Black", ("black", "--check", "--diff", ".")),
    ("Mypy", ("mypy",)),
    ("Pytest", ("pytest",)),
)

TESTS_FIRST_HYGIENE_GATES: tuple[tuple[str, tuple[str, ...]], ...] = NORMAL_QUALITY_GATES[:-1]
PYTEST_GATE = NORMAL_QUALITY_GATES[-1]

# Compatibility for existing imports and callers: the canonical default remains
# the full normal feature-branch gate set.
QUALITY_GATES = NORMAL_QUALITY_GATES


def run_quality_gates(gates: Sequence[tuple[str, Sequence[str]]] = QUALITY_GATES) -> int:
    """Run deterministic quality gates in order, failing fast."""
    for name, command in gates:
        printable = " ".join(command)
        print(f"[Hunter Pre-PR] {name}: {printable}", flush=True)
        completed = subprocess.run(tuple(command), check=False)
        if completed.returncode != 0:
            print(f"[Hunter Pre-PR] FAIL: {name} exited {completed.returncode}", flush=True)
            return completed.returncode
        print(f"[Hunter Pre-PR] PASS: {name}", flush=True)
    return 0


def run_preflight(*, mode: str = NORMAL_MODE) -> int:
    """Run one explicit pre-PR mode without weakening the normal gate."""
    if mode == NORMAL_MODE:
        result = run_quality_gates(NORMAL_QUALITY_GATES)
        if result == 0:
            print("[Hunter Pre-PR] PASS: all deterministic repository-local gates", flush=True)
        return result

    if mode == TESTS_FIRST_RED_MODE:
        hygiene_result = run_quality_gates(TESTS_FIRST_HYGIENE_GATES)
        if hygiene_result != 0:
            return hygiene_result

        name, command = PYTEST_GATE
        printable = " ".join(command)
        print(f"[Hunter Pre-PR] {name} (expected RED): {printable}", flush=True)
        completed = subprocess.run(tuple(command), check=False)
        if completed.returncode == 0:
            print(
                "[Hunter Pre-PR] FAIL: tests-first-red was declared but Pytest is green; "
                "remove the RED declaration and use normal mode.",
                flush=True,
            )
            return 2
        if completed.returncode != PYTEST_TEST_FAILURE_EXIT:
            print(
                f"[Hunter Pre-PR] FAIL: Pytest exited {completed.returncode}; only exit 1 "
                "(tests failed) is a valid declared RED state.",
                flush=True,
            )
            return completed.returncode

        print(
            "[Hunter Pre-PR] EXPECTED RED: Pytest exited 1; artifact guard, Ruff, Black, "
            "and Mypy are clean.",
            flush=True,
        )
        print("[Hunter Pre-PR] PASS: tests-first RED hygiene contract", flush=True)
        return 0

    raise ValueError(f"Unsupported preflight mode: {mode}")


def _gates_for_listing(mode: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if mode == NORMAL_MODE:
        return NORMAL_QUALITY_GATES
    if mode == TESTS_FIRST_RED_MODE:
        return TESTS_FIRST_HYGIENE_GATES + (("Pytest (expected RED)", PYTEST_GATE[1]),)
    raise ValueError(f"Unsupported preflight mode: {mode}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the exact repository-local deterministic quality and artifact gates "
            "shared by feature-branch preflight and GitHub CI."
        )
    )
    parser.add_argument(
        "--mode",
        choices=PREFLIGHT_MODES,
        default=NORMAL_MODE,
        help=(
            "normal requires Artifact Guard/Ruff/Black/Mypy/Pytest green; "
            "tests-first-red requires Artifact Guard/Ruff/Black/Mypy green and "
            "Pytest exit 1 from intentionally failing tests."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the ordered gate commands for the selected mode without executing them.",
    )
    args = parser.parse_args()

    if args.list:
        for name, command in _gates_for_listing(args.mode):
            print(f"{name}: {' '.join(command)}")
        return 0

    return run_preflight(mode=args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
