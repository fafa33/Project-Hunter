"""Bounded, unprivileged RED/GREEN replay for newly resolved reviewer findings.

Runs only in the existing exact-head hosted preflight, never in a privileged
pull_request_target job. Historic records are pinned, not silently exempted.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGACY_IDS = frozenset({"RFD-372-001", "RFD-372-002", "RFD-372-003", "RFD-372-004", "RFD-416-001"})
SHA = re.compile(r"^[0-9a-f]{40}$")
TIMEOUT_SECONDS = 75


def run(args: list[str], *, cwd: Path, timeout: int = TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)


def validate_proof(finding: dict, *, root: Path = ROOT) -> list[str]:
    """Verify actual failing baseline and passing current test, not a written claim."""
    ident = finding.get("id", "unknown")
    if (
        ident in LEGACY_IDS
        or finding.get("validation_state") != "validated"
        or finding.get("resolution_state") != "resolved"
    ):
        return []
    if finding.get("classification") not in {"recurrence", "new_systemic_defect", "duplicate"}:
        return []
    errors: list[str] = []
    proof = finding.get("executable_proof")
    if not isinstance(proof, dict):
        return [f"{ident}: missing executable_proof; supply vulnerable_sha, red_test, green_test, adversarial_tests"]
    sha = proof.get("vulnerable_sha")
    red = proof.get("red_test")
    green = proof.get("green_test")
    adversarial = proof.get("adversarial_tests")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        errors.append(f"{ident}: vulnerable_sha must be an immutable 40-character commit SHA")
    if not isinstance(red, str) or not red.startswith("tests/") or "::test_" not in red:
        errors.append(f"{ident}: red_test must be a specific tests/ pytest selector")
    if not isinstance(green, str) or green != red:
        errors.append(f"{ident}: green_test must match red_test exactly")
    if (
        not isinstance(adversarial, list)
        or not adversarial
        or len(adversarial) > 6
        or any(not isinstance(x, str) or not x.startswith("tests/") or "::test_" not in x for x in adversarial)
    ):
        errors.append(f"{ident}: provide 1-6 specific adversarial pytest selectors")
    if errors:
        return errors
    # Reject malicious pytest argument injection and filesystem escape.
    for selector in [red, *adversarial]:
        path = selector.split("::", 1)[0]
        candidate = (root / path).resolve()
        if not candidate.is_relative_to((root / "tests").resolve()) or not candidate.is_file():
            return [f"{ident}: invalid pytest target {selector}"]
    with tempfile.TemporaryDirectory(prefix="hunter-finding-red-") as temp:
        baseline = Path(temp)
        archive = run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=root, timeout=15)
        if archive.returncode:
            return [f"{ident}: vulnerable_sha unavailable; fetch the exact baseline commit"]
        added = run(["git", "worktree", "add", "--detach", str(baseline / "repo"), sha], cwd=root, timeout=20)
        if added.returncode:
            return [f"{ident}: baseline worktree unavailable: {added.stderr[-300:]}"]
        try:
            # Copy the regression test file from the fixed HEAD into the vulnerable
            # checkout so RED and GREEN execute identical test code.
            import shutil

            test_file = red.split("::", 1)[0]
            destination = baseline / "repo" / test_file
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / test_file, destination)
            red_result = run([sys.executable, "-m", "pytest", "-q", "--tb=short", red], cwd=baseline / "repo")
            if red_result.returncode == 0:
                errors.append(f"{ident}: RED did not fail on vulnerable_sha")
            elif red_result.returncode != 1 or " failed" not in red_result.stdout:
                errors.append(f"{ident}: RED was not a genuine assertion failure (exit {red_result.returncode})")
        finally:
            run(["git", "worktree", "remove", "--force", str(baseline / "repo")], cwd=root, timeout=20)
    current = run([sys.executable, "-m", "pytest", "-q", "--tb=short", green, *adversarial], cwd=root)
    if current.returncode:
        errors.append(f"{ident}: GREEN/adversarial tests failed: {current.stdout[-500:]} {current.stderr[-200:]}")
    return errors


def main() -> int:
    findings = json.loads((ROOT / "docs/REVIEWER_FINDING_DISPOSITIONS.json").read_text())["findings"]
    errors = []
    for finding in findings:
        try:
            errors.extend(validate_proof(finding))
        except subprocess.TimeoutExpired:
            errors.append(f"{finding.get('id')}: proof timed out (bounded); simplify tests or repair baseline")
    for error in errors:
        print("[Finding Proof] FAIL:", error)
    if not errors:
        print("[Finding Proof] PASS: bounded RED/GREEN/adversarial proofs")
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
