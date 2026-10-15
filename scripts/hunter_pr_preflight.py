from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

NORMAL_MODE = "normal"
TESTS_FIRST_RED_MODE = "tests-first-red"
AGGREGATE_MODE = "aggregate"
PREFLIGHT_MODES = (NORMAL_MODE, TESTS_FIRST_RED_MODE, AGGREGATE_MODE)
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

# Default gate dependencies for aggregate mode.
# Independent static/linting gates run regardless of each other.
# Pytest depends on basic structural and defect prevention guards passing.
DEFAULT_GATE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "Pytest": ("Architecture Index Guard", "Artifact Guard", "Defect Prevention Guard"),
}

PUSH_SAFETY_GATES: tuple[tuple[str, tuple[str, ...]], ...] = TESTS_FIRST_HYGIENE_GATES
QUALITY_GATES = NORMAL_QUALITY_GATES


def run_quality_gates(gates: Sequence[tuple[str, Sequence[str]]] = QUALITY_GATES) -> int:
    """Run deterministic quality gates in order, failing fast."""
    lane_start = time.monotonic()
    for name, command in gates:
        gate_start = time.monotonic()
        printable = " ".join(command)
        print(f"[Hunter Pre-PR] {name}: {printable}", flush=True)
        completed = subprocess.run(tuple(command), check=False)
        elapsed = time.monotonic() - gate_start
        print(f"[Hunter Pre-PR] TIMING: {name} {elapsed:.2f}s", flush=True)
        if completed.returncode != 0:
            print(f"[Hunter Pre-PR] TIMING: lane {time.monotonic() - lane_start:.2f}s", flush=True)
            print(f"[Hunter Pre-PR] FAIL: {name} exited {completed.returncode}", flush=True)
            return completed.returncode
        print(f"[Hunter Pre-PR] PASS: {name}", flush=True)
    print(f"[Hunter Pre-PR] TIMING: lane {time.monotonic() - lane_start:.2f}s", flush=True)
    return 0


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _extract_relevant_file_paths(text: str, root: Path) -> list[str]:
    paths: set[str] = set()
    for token in re.findall(r"([A-Za-z0-9_./-]+\.[A-Za-z0-9_]+)", text):
        clean_token = token.split(":")[0]
        try:
            p = Path(clean_token)
            if not p.is_absolute() and (root / p).is_file():
                paths.add(p.as_posix())
        except Exception:
            pass
    return sorted(paths)


def run_aggregate_gates(
    gates: Sequence[tuple[str, Sequence[str]]] = QUALITY_GATES,
    dependencies: Mapping[str, Sequence[str]] | None = None,
    report_path: Path | str | None = None,
    timeout_seconds: float | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run quality gates in aggregate mode, collecting all failure outcomes."""
    if dependencies is None:
        dependencies = DEFAULT_GATE_DEPENDENCIES

    root = _repository_root()
    lane_start = time.monotonic()
    results: list[dict[str, Any]] = []
    failed_or_blocked_gate_names: set[str] = set()

    for name, command in gates:
        gate_deps = dependencies.get(name, ())
        blocking_deps = [dep for dep in gate_deps if dep in failed_or_blocked_gate_names]

        if blocking_deps:
            failed_or_blocked_gate_names.add(name)
            result = {
                "name": name,
                "command": list(command),
                "outcome": "blocked",
                "exit_code": None,
                "duration": 0.0,
                "error_details": f"Blocked by failed or blocked dependent gate(s): {', '.join(blocking_deps)}",
                "relevant_file_paths": [],
            }
            results.append(result)
            print(f"[Hunter Pre-PR] BLOCKED: {name} (depends on {', '.join(blocking_deps)})", flush=True)
            continue

        printable = " ".join(command)
        print(f"[Hunter Pre-PR] {name}: {printable}", flush=True)
        gate_start = time.monotonic()

        try:
            completed = subprocess.run(
                tuple(command),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
            elapsed = time.monotonic() - gate_start
            print(f"[Hunter Pre-PR] TIMING: {name} {elapsed:.2f}s", flush=True)

            if completed.stdout:
                print(completed.stdout.rstrip(), flush=True)
            if completed.stderr:
                print(completed.stderr.rstrip(), flush=True)

            combined_text = (completed.stdout or "") + "\n" + (completed.stderr or "")
            relevant_files = _extract_relevant_file_paths(combined_text, root)

            if completed.returncode == 0:
                print(f"[Hunter Pre-PR] PASS: {name}", flush=True)
                result = {
                    "name": name,
                    "command": list(command),
                    "outcome": "passed",
                    "exit_code": 0,
                    "duration": round(elapsed, 2),
                    "error_details": "",
                    "relevant_file_paths": relevant_files,
                }
            else:
                failed_or_blocked_gate_names.add(name)
                err_detail = completed.stderr.strip() or completed.stdout.strip() or f"Exited {completed.returncode}"
                print(f"[Hunter Pre-PR] FAIL: {name} exited {completed.returncode}", flush=True)
                result = {
                    "name": name,
                    "command": list(command),
                    "outcome": "failed",
                    "exit_code": completed.returncode,
                    "duration": round(elapsed, 2),
                    "error_details": err_detail,
                    "relevant_file_paths": relevant_files,
                }
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - gate_start
            failed_or_blocked_gate_names.add(name)
            print(f"[Hunter Pre-PR] TIMING: {name} {elapsed:.2f}s (TIMEOUT)", flush=True)
            print(f"[Hunter Pre-PR] FAIL: {name} timed out after {timeout_seconds}s", flush=True)
            result = {
                "name": name,
                "command": list(command),
                "outcome": "failed",
                "exit_code": 124,
                "duration": round(elapsed, 2),
                "error_details": f"Gate timed out after {timeout_seconds}s",
                "relevant_file_paths": [],
            }
        except Exception as exc:
            elapsed = time.monotonic() - gate_start
            failed_or_blocked_gate_names.add(name)
            print(f"[Hunter Pre-PR] FAIL: {name} interrupted or failed ({exc})", flush=True)
            result = {
                "name": name,
                "command": list(command),
                "outcome": "failed",
                "exit_code": 1,
                "duration": round(elapsed, 2),
                "error_details": f"Execution error: {exc}",
                "relevant_file_paths": [],
            }

        results.append(result)

    total_duration = round(time.monotonic() - lane_start, 2)
    overall_passed = all(r["outcome"] == "passed" for r in results)

    head_sha = None
    try:
        completed_sha = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed_sha.returncode == 0:
            head_sha = completed_sha.stdout.strip()
    except Exception:
        pass

    report = {
        "schema": "hunter.preflight-report/1",
        "mode": AGGREGATE_MODE,
        "overall_outcome": "passed" if overall_passed else "failed",
        "total_duration": total_duration,
        "head_sha": head_sha,
        "gates": results,
    }

    print(f"[Hunter Pre-PR] TIMING: lane {total_duration:.2f}s", flush=True)
    print("=" * 60, flush=True)
    print("HUNTER PREFLIGHT CONSOLIDATED FAILURE SUMMARY", flush=True)
    print("=" * 60, flush=True)
    for r in results:
        status_str = r["outcome"].upper()
        dur_str = f"{r['duration']:.2f}s"
        print(f"  - {r['name']:<30} [{status_str:<7}] ({dur_str})", flush=True)
        if r["outcome"] != "passed" and r["error_details"]:
            first_line = r["error_details"].splitlines()[0]
            print(f"      Details: {first_line[:100]}", flush=True)
    print("=" * 60, flush=True)

    if report_path is not None:
        p = Path(report_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"[Hunter Pre-PR] REPORT WRITTEN: {p}", flush=True)

    exit_code = 0 if overall_passed else 1
    return exit_code, report


def _reuse_blocker() -> str | None:
    """Why a recorded receipt may not stand in for this run, or ``None``."""
    import hunter_validation_receipt as receipts

    return receipts.reuse_blocker(_repository_root())


def _record_receipt() -> None:
    """Record that this exact identity passed the full lane."""
    import hunter_validation_receipt as receipts

    root = _repository_root()
    try:
        receipts.record(root, head_sha=receipts.resolve_head_sha(root), produced_by="local-preflight")
    except (receipts.ValidationEvidenceUnavailable, OSError) as exc:
        print(f"[Hunter Pre-PR] NOTE: full-lane receipt was not recorded ({exc})", flush=True)
        return
    print("[Hunter Pre-PR] RECORDED: full-lane receipt for this exact content identity", flush=True)


def run_preflight(
    *,
    mode: str = NORMAL_MODE,
    reuse_receipt: bool = False,
    record_receipt: bool = False,
    report_path: Path | str | None = None,
) -> int:
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

    if mode == AGGREGATE_MODE:
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
        exit_code, _report = run_aggregate_gates(NORMAL_QUALITY_GATES, report_path=report_path)
        if exit_code == 0:
            print("[Hunter Pre-PR] PASS: all deterministic repository-local gates (aggregate mode)", flush=True)
            if record_receipt:
                _record_receipt()
        return exit_code

    if mode == TESTS_FIRST_RED_MODE:
        red_lane_start = time.monotonic()
        hygiene_result = run_quality_gates(TESTS_FIRST_HYGIENE_GATES)
        if hygiene_result != 0:
            return hygiene_result

        name, command = PYTEST_GATE
        printable = " ".join(command)
        print(f"[Hunter Pre-PR] {name} (expected RED): {printable}", flush=True)
        pytest_start = time.monotonic()
        completed = subprocess.run(tuple(command), check=False)
        print(f"[Hunter Pre-PR] TIMING: Pytest (expected RED) {time.monotonic() - pytest_start:.2f}s", flush=True)
        print(f"[Hunter Pre-PR] TIMING: tests-first-red lane {time.monotonic() - red_lane_start:.2f}s", flush=True)
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
    if mode in (NORMAL_MODE, AGGREGATE_MODE):
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
            "Ruff/Black/Mypy/Pytest green (fails fast); aggregate executes all independent gates "
            "and produces a consolidated report; tests-first-red requires hygiene gates green "
            "and Pytest exit 1."
        ),
    )
    parser.add_argument(
        "--aggregate",
        action="store_true",
        help="Run preflight in aggregate execution mode (shorthand for --mode aggregate).",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        metavar="PATH",
        help="Path to save the JSON validation report to in aggregate mode.",
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
            "After a passing run, record a receipt binding this proof to the exact content, "
            "validation-definition and toolchain identity it was produced under."
        ),
    )
    parser.add_argument(
        "--reuse-receipt",
        action="store_true",
        help=(
            "Skip the run when a recorded receipt already covers this exact identity. "
            "Anything that does not match exactly re-runs the full lane."
        ),
    )
    args = parser.parse_args()

    mode = AGGREGATE_MODE if args.aggregate else args.mode

    if args.list:
        for name, command in _gates_for_listing(mode):
            print(f"{name}: {' '.join(command)}")
        return 0

    return run_preflight(
        mode=mode,
        reuse_receipt=args.reuse_receipt,
        record_receipt=args.record_receipt,
        report_path=args.report_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
