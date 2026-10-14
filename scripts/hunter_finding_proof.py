"""Bounded, unprivileged RED/GREEN replay for newly resolved reviewer findings.

Runs only in the existing exact-head hosted preflight, never in a privileged
pull_request_target job. Historic records are pinned, not silently exempted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Historic records are exempt only while byte-for-byte (canonical JSON) unchanged: the exemption is pinned to
# a content fingerprint, so rewriting a legacy ID to describe a new finding requires executable proof.
LEGACY_FINGERPRINTS = {
    "RFD-372-001": "020634b0b477df227ae1f234347a9a12a724f35fbf4b380f1cf644facd77a166",
    "RFD-372-002": "5aa8e5c590712870a62fb12f7a24df3ea9b624a447b00c8beb539e456f055447",
    "RFD-372-003": "c9c223182f4ab00f7b8607b8285d0f36649ab6d5cbf63a2f1af99c08753ef163",
    "RFD-372-004": "f9a76949b121b887f3ad4b51108e4b7efee35a80e1d4c44c552493d9ef42a765",
    "RFD-416-001": "9e67e06f305597cab0e620547ba810496c1218af9df4ffc97ecdb485c76e6432",
}
SHA = re.compile(r"^[0-9a-f]{40}$")
TIMEOUT_SECONDS = 75


def finding_fingerprint(finding: dict) -> str:
    canonical = json.dumps(finding, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_pinned_legacy(finding: dict) -> bool:
    expected = LEGACY_FINGERPRINTS.get(str(finding.get("id")))
    return expected is not None and expected == finding_fingerprint(finding)


def reviewed_head_sha(finding: dict) -> object:
    provenance = finding.get("source_provenance")
    return provenance.get("reviewed_head_sha") if isinstance(provenance, dict) else None


class IsolationUnavailable(RuntimeError):
    """No no-network sandbox is available; the proof must fail closed rather than run unisolated."""


def scrubbed_env(home: str) -> dict[str, str]:
    """Only what pytest needs; no workflow credentials, tokens or proxy settings reach selectors."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": home,
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }


# Mount point of the sandbox's own tmpfs. It is created inside bwrap's private mount namespace (--unshare-*),
# so it is never the host's /tmp: nothing written there can reach the host or the trusted workspace.
SANDBOX_TMP = "/tmp"  # NOSONAR S108 - private tmpfs in the sandbox mount namespace, reviewed safe
# Credential-bearing host trees are masked with empty tmpfs mounts; only what the run needs is re-bound.
MASKED_TREES = ("/home", "/root", "/run")


TRUSTED_BWRAP_PATHS = ("/usr/bin/bwrap", "/bin/bwrap")


def trusted_bwrap() -> str | None:
    """A root-owned, non-group/world-writable bwrap at a fixed system path; never resolved from ambient PATH.

    A preceding candidate workflow step can prepend a directory to PATH (GITHUB_PATH) and plant a fake binary.
    """
    for candidate in TRUSTED_BWRAP_PATHS:
        path = Path(candidate)
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        info = path.stat()
        if info.st_uid == 0 and not info.st_mode & 0o022:
            return str(path.resolve())
    return None


def sandbox_prefix(cwd: Path) -> list[str]:
    """bwrap argv: read-only root, private tmpfs, no network, host credential trees masked.

    Mount order matters: masks and the private tmpfs come first, then the working tree and interpreter are
    re-bound read-only on top, so a baseline worktree that lives under the host's /tmp (hidden by the private
    tmpfs) and a checkout under /home remain visible, read-only, and nothing else of those trees is.
    """
    bwrap = trusted_bwrap()
    if not bwrap:
        # No weaker fallback: a network-only namespace leaves the workspace writable to candidate-selected tests.
        raise IsolationUnavailable("bwrap is required for filesystem- and network-isolated proof runs")
    command = [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    for tree in MASKED_TREES:
        command += ["--tmpfs", tree]
    command += ["--tmpfs", SANDBOX_TMP]
    keep = {str(cwd.resolve()), str(Path(sys.prefix).resolve()), str(Path(sys.base_prefix).resolve())}
    for path in sorted(keep):
        if path.startswith((*MASKED_TREES, SANDBOX_TMP)):
            command += ["--ro-bind", path, path]
    command += ["--unshare-net", "--unshare-pid", "--die-with-parent", "--chdir", str(cwd.resolve())]
    return command


def run(args: list[str], *, cwd: Path, timeout: int = TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)


def run_pytest(selectors: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run selectors with a scrubbed environment and no network; never inherits the preflight process."""
    command = [
        *sandbox_prefix(cwd),
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        "-p",
        "no:cacheprovider",
        *selectors,
    ]
    return subprocess.run(
        command,
        cwd=cwd,
        env=scrubbed_env(SANDBOX_TMP),
        text=True,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


def validate_proof(finding: dict, *, root: Path = ROOT) -> list[str]:
    """Verify actual failing baseline and passing current test, not a written claim."""
    ident = finding.get("id", "unknown")
    if (
        is_pinned_legacy(finding)
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
        or len(set(adversarial)) != len(adversarial)
        or red in adversarial
        or any(not isinstance(x, str) or not x.startswith("tests/") or "::test_" not in x for x in adversarial)
    ):
        errors.append(f"{ident}: provide 1-6 specific adversarial pytest selectors")
    if isinstance(sha, str) and reviewed_head_sha(finding) != sha:
        errors.append(f"{ident}: vulnerable_sha must equal source_provenance.reviewed_head_sha of the reviewed finding")
    if errors:
        return errors
    assert isinstance(sha, str) and isinstance(red, str) and isinstance(green, str)
    assert isinstance(adversarial, list)
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
            test_file = red.split("::", 1)[0]
            destination = baseline / "repo" / test_file
            # Never follow a baseline-controlled symlink, including parent components.
            baseline_root = baseline / "repo"
            if any(
                part.is_symlink()
                for part in [destination, *destination.parents]
                if part == baseline_root or baseline_root in part.parents
            ):
                return [f"{ident}: baseline test destination contains a symlink"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.resolve().is_relative_to(baseline_root.resolve()):
                return [f"{ident}: baseline test destination escapes worktree"]
            shutil.copy2(root / test_file, destination)
            red_result = run_pytest([red], cwd=baseline / "repo")
            if red_result.returncode == 0:
                errors.append(f"{ident}: RED did not fail on vulnerable_sha")
            elif red_result.returncode != 1 or " failed" not in red_result.stdout:
                errors.append(f"{ident}: RED was not a genuine assertion failure (exit {red_result.returncode})")
        finally:
            run(["git", "worktree", "remove", "--force", str(baseline / "repo")], cwd=root, timeout=20)
    current = run_pytest([green, *adversarial], cwd=root)
    if current.returncode:
        errors.append(f"{ident}: GREEN/adversarial tests failed: {current.stdout[-500:]} {current.stderr[-200:]}")
    return errors


def main() -> int:
    findings = json.loads((ROOT / "docs/REVIEWER_FINDING_DISPOSITIONS.json").read_text())["findings"]
    errors = []
    for finding in findings:
        try:
            errors.extend(validate_proof(finding))
        except IsolationUnavailable as unavailable:
            errors.append(f"{finding.get('id')}: proof refused, {unavailable}")
        except subprocess.TimeoutExpired:
            errors.append(f"{finding.get('id')}: proof timed out (bounded); simplify tests or repair baseline")
    for error in errors:
        print("[Finding Proof] FAIL:", error)
    if not errors:
        print("[Finding Proof] PASS: bounded RED/GREEN/adversarial proofs")
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
