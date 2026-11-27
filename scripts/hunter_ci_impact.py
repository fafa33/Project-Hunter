"""Deterministic change-impact selection and pre-publication obligations.

This is a fast-fail optimization only: it never replaces required full-suite
proof. The preventive-obligation half derives executable known-defect checks
from exact candidate content and actually runs them at the repository-owned
push boundary. A recorded selector alone is never evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import hunter_pre_ready_review as review
import hunter_validation_receipt as receipts
from hunter_workflow_state import (
    PREVENTIVE_OBLIGATION_SCHEMA as OBLIGATION_SCHEMA,
)
from hunter_workflow_state import (
    load_preventive_obligations as load_obligations,
)
from hunter_workflow_state import (
    path_matches_scope_entry,
)

ROOT = Path(__file__).resolve().parents[1]
_REVIEW_REQUEST_PATH = ".hunter/pre-ready-hostile-review.json"


def classify_review_metadata_only(changed: list[str]) -> bool:
    """Conservative metadata-only signal; never an authorization to skip proof.

    Empty/unknown diffs and mixed security or code changes always return False.
    Consumers must independently validate exact-head/base and trusted receipts.
    """
    return bool(changed) and all(path == _REVIEW_REQUEST_PATH for path in changed)


def classify_cross_head_equivalence_candidate(
    *, changed: list[str], base_unchanged: bool, definitions_unchanged: bool, toolchain_unchanged: bool
) -> bool:
    """Signal only: an eligible metadata diff is NOT reusable proof.

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


# --- Preventive obligations -------------------------------------------------


def applicable_obligations(
    obligations: tuple[dict[str, Any], ...], changed_paths: tuple[str, ...]
) -> tuple[dict[str, Any], ...]:
    """Return obligations whose declared applicability covers a changed path."""
    return tuple(
        obligation
        for obligation in obligations
        if any(
            path_matches_scope_entry(path, str(entry))
            for path in changed_paths
            for entry in obligation["changed_paths"]
            if isinstance(entry, str) and entry.strip()
        )
    )


def _selector_file(selector: str) -> str:
    path, separator, _test = selector.partition("::")
    return path if separator else selector


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def evidence_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()


def _evidence_record(
    *,
    obligations: tuple[dict[str, Any], ...],
    content_identity: str,
    toolchain: dict[str, str],
    selectors: tuple[str, ...],
    exit_code: int,
    error: str = "",
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema": OBLIGATION_SCHEMA,
        "content_identity": content_identity,
        "obligations": sorted(str(obligation["id"]) for obligation in obligations),
        "selectors": list(selectors),
        "test_inputs": [{"selector": selector, "exists": False} for selector in selectors],
        "toolchain": toolchain,
        "toolchain_identity": receipts.toolchain_identity(toolchain),
        "outcome": "passed" if exit_code == 0 else "blocked",
        "exit_code": exit_code,
        "trusted_proof": False,
    }
    if error:
        record["error"] = error
    record["evidence_digest"] = evidence_digest(record)
    return record


def execute_obligations(
    root: Path,
    obligations: tuple[dict[str, Any], ...],
    *,
    cwd: Path | None = None,
) -> tuple[dict[str, Any], int]:
    """Run applicable selectors against exact content and bind the outcome."""
    workspace = cwd or root
    try:
        content_identity = receipts.content_identity(workspace)
        toolchain = receipts.measured_toolchain()
    except Exception as exc:  # noqa: BLE001 - absent evidence must fail closed
        selectors = tuple(sorted({selector for item in obligations for selector in item["selectors"]}))
        return (
            _evidence_record(
                obligations=obligations,
                content_identity="",
                toolchain={},
                selectors=selectors,
                exit_code=2,
                error=f"{type(exc).__name__}: {exc}",
            ),
            2,
        )
    selectors = tuple(sorted({selector for item in obligations for selector in item["selectors"]}))
    missing = [selector for selector in selectors if not (workspace / _selector_file(selector)).is_file()]
    if missing:
        return (
            _evidence_record(
                obligations=obligations,
                content_identity=content_identity,
                toolchain=toolchain,
                selectors=selectors,
                exit_code=2,
                error="focused test selectors are missing: " + ", ".join(missing),
            ),
            2,
        )
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    completed = subprocess.run(
        (sys.executable, "-m", "pytest", "-q", *selectors),
        cwd=workspace,
        env=env,
        check=False,
    )
    record = _evidence_record(
        obligations=obligations,
        content_identity=content_identity,
        toolchain=toolchain,
        selectors=selectors,
        exit_code=completed.returncode,
    )
    record["test_inputs"] = [
        {"selector": selector, "exists": (workspace / _selector_file(selector)).is_file()} for selector in selectors
    ]
    record["evidence_digest"] = evidence_digest(record)
    return record, completed.returncode


def enforce_preventive_obligations(base: str, head: str, *, cwd: Path | None = None) -> int:
    """Existing push-boundary seam: derive and execute exact-content obligations."""
    workspace = cwd or ROOT
    obligations, error = load_obligations(workspace)
    if error:
        print(f"[Hunter Preventive Obligations] BLOCKED: {error}", file=sys.stderr)
        return 2
    try:
        changes = review.local_changes(base, head, cwd=workspace)
    except Exception as exc:  # noqa: BLE001 - missing git evidence must fail closed
        print(f"[Hunter Preventive Obligations] BLOCKED: exact content evidence unavailable ({exc})", file=sys.stderr)
        return 2
    changed_paths = tuple(
        sorted({path for change in review.target_changes(changes) for path in change.affected_paths()})
    )
    applicable = applicable_obligations(obligations, changed_paths)
    if not applicable:
        print("[Hunter Preventive Obligations] PASS: no applicable known-defect obligations in this exact range")
        return 0
    record, code = execute_obligations(workspace, applicable, cwd=workspace)
    print(
        "[Hunter Preventive Obligations] " + ("BLOCKED" if code else "PASS") + ": " + json.dumps(record, sort_keys=True)
    )
    return code


def obligations_main() -> int:
    parser = argparse.ArgumentParser(description="Execute applicable preventive obligations.")
    parser.add_argument("--base", help="Exact base SHA of the governed range.")
    parser.add_argument("--head", default="HEAD", help="Exact candidate head (default: HEAD).")
    parser.add_argument("--root", default=str(ROOT), help="Repository root containing the obligation data.")
    parser.add_argument("--changed-path", action="append", default=[], help="Explicit changed path (repeatable).")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    obligations, error = load_obligations(root)
    if error:
        print(f"[Hunter Preventive Obligations] BLOCKED: {error}", file=sys.stderr)
        return 2
    if args.changed_path:
        applicable = applicable_obligations(obligations, tuple(args.changed_path))
        if not applicable:
            print("[Hunter Preventive Obligations] PASS: no applicable known-defect obligations")
            return 0
        record, code = execute_obligations(root, applicable, cwd=root)
    else:
        if not args.base:
            parser.error("--base is required unless --changed-path is supplied")
        return enforce_preventive_obligations(args.base, args.head, cwd=root)
    print(
        "[Hunter Preventive Obligations] " + ("BLOCKED" if code else "PASS") + ": " + json.dumps(record, sort_keys=True)
    )
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--run-focused", action="store_true")
    parser.add_argument("--preventive-obligations", action="store_true")
    args = parser.parse_args()
    if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (args.base, args.head)):
        print("[Hunter CI Impact] FULL REQUIRED: base/head must be exact 40-hex commit IDs", flush=True)
        return 2
    ancestry_command = "git merge-base --is-ancestor " + shlex.quote(args.base) + " " + shlex.quote(args.head)
    ancestry = subprocess.run(shlex.split(ancestry_command), cwd=ROOT, capture_output=True, check=False)
    if ancestry.returncode != 0:
        print("[Hunter CI Impact] FULL REQUIRED: base is not a verified ancestor of head", flush=True)
        return 2
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
    if args.preventive_obligations:
        return enforce_preventive_obligations(args.base, args.head, cwd=ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
