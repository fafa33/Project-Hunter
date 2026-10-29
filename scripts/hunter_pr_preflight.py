from __future__ import annotations

import argparse
import json
import os
import re
import stat
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

REPORT_DIR = Path(".hunter") / "preflight-reports"

NORMAL_QUALITY_GATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Architecture Index Guard", ("python", "scripts/hunter_architecture_index_preflight.py")),
    ("Artifact Guard", ("python", "scripts/hunter_artifact_preflight.py")),
    ("Defect Prevention Guard", ("python", "scripts/hunter_defect_prevention_preflight.py")),
    ("Ruff", ("ruff", "check", ".")),
    ("Black", ("black", "--workers", "1", "--check", "--diff", ".")),
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


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def validate_and_resolve_report_path(requested_path: Path | str, root: Path) -> Path:
    """Validate and resolve report output path strictly inside APPROVED_REPORT_DIR.

    Rejects:
    - Absolute paths outside the approved report root
    - Directory traversal ('..')
    - Symlink components or symlink targets
    - Directories or non-regular file targets
    - Overwriting protected repository files or .git metadata
    """
    approved_root = (root / REPORT_DIR).resolve()
    req = Path(requested_path)

    if ".." in req.parts:
        raise ValueError(f"Report path {requested_path!r} contains forbidden '..' traversal")
    if req.is_absolute():
        raw = req
    elif req.parts and req.parts[0] == ".hunter":
        raw = root / req
    else:
        raw = root / REPORT_DIR / req

    if not raw.resolve().is_relative_to(approved_root):
        raise ValueError(f"Report path {requested_path!r} resolves outside approved report root {approved_root}")

    # Inspect the original path, before resolve() can erase symlink components.
    current = Path(raw.anchor)
    for component in raw.parts[1:]:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"Report path {requested_path!r} contains symlink component at {current}")

    candidate = raw.resolve()
    try:
        candidate.relative_to(approved_root)
    except ValueError as exc:
        raise ValueError(
            f"Report path {requested_path!r} resolves outside approved report root {approved_root}"
        ) from exc
    if candidate.exists() and not candidate.is_file():
        raise ValueError(f"Report path {requested_path!r} is not a regular file")

    return candidate


def safe_write_report(report_data: dict[str, Any], resolved_path: Path) -> None:
    """Write report data safely using O_NOFOLLOW / O_TRUNC secure file operations."""
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(report_data, indent=2) + "\n").encode("utf-8")
    # Walk parents using directory descriptors, refusing symlinks at every hop.
    # The final open is relative to the pinned parent descriptor, so a parent
    # rename/substitution cannot redirect the write to an attacker-controlled tree.
    components = resolved_path.absolute().parts[1:]
    opened: list[int] = []
    try:
        directory = os.open(resolved_path.anchor, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        opened.append(directory)
        for component in components[:-1]:
            directory = os.open(
                component,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory,
            )
            opened.append(directory)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(components[-1], flags, 0o600, dir_fd=directory)
        with os.fdopen(fd, "wb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError(f"Report target is not a regular file: {resolved_path}")
            handle.write(payload)
    except OSError as exc:
        raise RuntimeError(f"Failed to securely write report to {resolved_path}: {exc}") from exc
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


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


def _extract_relevant_file_paths(text: str, root: Path) -> list[str]:
    """Extract relevant repository file paths in linear time with bounded input size and token length.

    Avoids super-linear regex backtracking and unsafe symlink traversal.
    """
    paths: set[str] = set()
    bounded_text = text[:1_000_000]
    root_resolved = root.resolve()

    delimiters = r"[\s:,\"\''\(\)\[\]<>=;]+"
    tokens = re.split(delimiters, bounded_text)

    allowed_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./-")

    for token in tokens:
        if not token or len(token) > 256 or "." not in token:
            continue
        if ".." in token or token.startswith("/") or token.startswith("\\"):
            continue
        if not set(token).issubset(allowed_chars):
            continue

        try:
            p = Path(token)
            full = root / p
            if full.is_file() and not full.is_symlink() and not full.parent.is_symlink():
                resolved = full.resolve()
                if resolved.is_relative_to(root_resolved):
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
            try:
                completed = subprocess.run(
                    tuple(command),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout_seconds,
                )
            except TypeError:
                completed = subprocess.run(tuple(command), check=False)

            elapsed = time.monotonic() - gate_start
            print(f"[Hunter Pre-PR] TIMING: {name} {elapsed:.2f}s", flush=True)

            stdout_str = getattr(completed, "stdout", "") or ""
            stderr_str = getattr(completed, "stderr", "") or ""
            returncode = getattr(completed, "returncode", 1)

            if stdout_str:
                print(stdout_str.rstrip(), flush=True)
            if stderr_str:
                print(stderr_str.rstrip(), flush=True)

            combined_text = stdout_str + "\n" + stderr_str
            relevant_files = _extract_relevant_file_paths(combined_text, root)

            if returncode == 0:
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
                err_detail = stderr_str.strip() or stdout_str.strip() or f"Exited {returncode}"
                print(f"[Hunter Pre-PR] FAIL: {name} exited {returncode}", flush=True)
                result = {
                    "name": name,
                    "command": list(command),
                    "outcome": "failed",
                    "exit_code": returncode,
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
        resolved_dest = validate_and_resolve_report_path(report_path, root)
        safe_write_report(report, resolved_dest)
        print(f"[Hunter Pre-PR] REPORT WRITTEN: {resolved_dest}", flush=True)

    first_failed = next((r["exit_code"] for r in results if r["outcome"] == "failed" and r["exit_code"] is not None), 1)
    exit_code = 0 if overall_passed else first_failed
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
    root = _repository_root()

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
                if report_path is not None:
                    import hunter_validation_receipt as receipts

                    receipt, _err = receipts.load(receipts.receipt_path(root))
                    if receipt is not None:
                        report_data = {
                            "schema": "hunter.preflight-report/1",
                            "mode": AGGREGATE_MODE,
                            "overall_outcome": "passed",
                            "total_duration": 0.0,
                            "head_sha": receipt.head_sha,
                            "content_identity": receipt.content_identity,
                            "definition_identity": receipt.definition_identity,
                            "toolchain_identity": receipt.toolchain_identity,
                            "contract_identity": getattr(receipt, "contract_identity", ""),
                            "receipt_reference": {
                                "produced_at": receipt.produced_at.isoformat(),
                                "produced_by": receipt.produced_by,
                                "lane": receipt.lane,
                            },
                            "gates": [
                                {
                                    "name": name,
                                    "command": list(command),
                                    "outcome": "reused",
                                    "exit_code": 0,
                                    "duration": 0.0,
                                    "error_details": "Reused from valid validation receipt",
                                    "relevant_file_paths": [],
                                }
                                for name, command in NORMAL_QUALITY_GATES
                            ],
                        }
                        resolved_dest = validate_and_resolve_report_path(report_path, root)
                        safe_write_report(report_data, resolved_dest)
                        print(f"[Hunter Pre-PR] REPORT WRITTEN (REUSED): {resolved_dest}", flush=True)
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

    try:
        return run_preflight(
            mode=mode,
            reuse_receipt=args.reuse_receipt,
            record_receipt=args.record_receipt,
            report_path=args.report_file,
        )
    except (ValueError, RuntimeError) as err:
        print(f"[Hunter Pre-PR] ERROR: {err}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
