from __future__ import annotations

import argparse
import subprocess
from collections.abc import Sequence
from pathlib import Path

NORMAL_MODE = "normal"
TESTS_FIRST_RED_MODE = "tests-first-red"
PREFLIGHT_MODES = (NORMAL_MODE, TESTS_FIRST_RED_MODE)
PYTEST_TEST_FAILURE_EXIT = 1

NORMAL_QUALITY_GATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Architecture Index Guard", ("python", "scripts/hunter_architecture_index_preflight.py")),
    ("Artifact Guard", ("python", "scripts/hunter_artifact_preflight.py")),
    ("Defect Prevention Guard", ("python", "scripts/hunter_defect_prevention_preflight.py")),
    ("Ruff", ("ruff", "check", ".")),
    ("Black", ("black", "--check", "--diff", ".")),
    ("Mypy", ("mypy",)),
    ("Pytest", ("pytest",)),
)

TESTS_FIRST_HYGIENE_GATES: tuple[tuple[str, tuple[str, ...]], ...] = NORMAL_QUALITY_GATES[:-1]
PYTEST_GATE = NORMAL_QUALITY_GATES[-1]

# The deterministic gates the repository-owned push boundary runs. Issue #415:
# the full repository suite is deliberately absent from it. Nothing that suite
# discovers requires rewriting already-published history to repair, and the
# authoritative exact-head proof is owned by the hosted branch preflight --
# see docs/VALIDATION_STAGE_CONTRACT.json for the full ownership contract.
PUSH_SAFETY_GATES: tuple[tuple[str, tuple[str, ...]], ...] = TESTS_FIRST_HYGIENE_GATES

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


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _reuse_blocker() -> str | None:
    """Why a recorded receipt may not stand in for this run, or ``None``.

    Imported here rather than at module scope: the receipt module reads this
    module's gate chain to derive the validation-definition identity, so the
    dependency only closes in one direction at call time.
    """
    import hunter_validation_receipt as receipts

    return receipts.reuse_blocker(_repository_root())


def _record_receipt() -> None:
    """Record that this exact identity passed the full lane.

    Best effort by design. A proof that could not be written down is still a
    proof, so a cache that fails to persist must not turn a green run red --
    the only thing lost is the chance to skip an identical repeat.
    """
    import hunter_validation_receipt as receipts

    root = _repository_root()
    try:
        receipts.record(root, head_sha=receipts.resolve_head_sha(root), produced_by="local-preflight")
    except (receipts.ValidationEvidenceUnavailable, OSError) as exc:
        print(f"[Hunter Pre-PR] NOTE: full-lane receipt was not recorded ({exc})", flush=True)
        return
    print("[Hunter Pre-PR] RECORDED: full-lane receipt for this exact content identity", flush=True)


def run_preflight(*, mode: str = NORMAL_MODE, reuse_receipt: bool = False, record_receipt: bool = False) -> int:
    """Run one explicit pre-PR mode without weakening the normal gate."""
    if mode == NORMAL_MODE:
        if reuse_receipt:
            blocker = _reuse_blocker()
            if blocker is None:
                print(
                    "[Hunter Pre-PR] REUSE: a recorded receipt already covers this exact content, "
                    "validation definition and toolchain; the full lane is not re-run.",
                    flush=True,
                )
                return 0
            print(f"[Hunter Pre-PR] NO-REUSE: {blocker}", flush=True)
        result = run_quality_gates(NORMAL_QUALITY_GATES)
        if result == 0:
            print("[Hunter Pre-PR] PASS: all deterministic repository-local gates", flush=True)
            if record_receipt:
                _record_receipt()
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
            "[Hunter Pre-PR] EXPECTED RED: Pytest exited 1; architecture index guard, artifact guard, "
            "defect prevention guard, Ruff, Black, and Mypy are clean.",
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
            "normal requires Architecture Index Guard/Artifact Guard/Defect Prevention Guard/"
            "Ruff/Black/Mypy/Pytest green; tests-first-red requires those deterministic hygiene "
            "gates except Pytest green and accepts only Pytest exit 1 from intentionally failing tests."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the ordered gate commands for the selected mode without executing them.",
    )
    parser.add_argument(
        "--record-receipt",
        action="store_true",
        help=(
            "After a passing normal run, record a receipt binding this proof to the exact content, "
            "validation-definition and toolchain identity it was produced under."
        ),
    )
    parser.add_argument(
        "--reuse-receipt",
        action="store_true",
        help=(
            "Skip the normal run when a recorded receipt already covers this exact identity. "
            "Anything that does not match exactly re-runs the full lane."
        ),
    )
    args = parser.parse_args()

    if args.list:
        for name, command in _gates_for_listing(args.mode):
            print(f"{name}: {' '.join(command)}")
        return 0

    return run_preflight(mode=args.mode, reuse_receipt=args.reuse_receipt, record_receipt=args.record_receipt)


if __name__ == "__main__":
    raise SystemExit(main())
