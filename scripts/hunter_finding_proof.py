"""Bounded, unprivileged RED/GREEN replay for newly resolved reviewer findings.

Runs only in the existing exact-head hosted preflight, never in a privileged
pull_request_target job. Historic records are pinned, not silently exempted.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
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


def sandbox_prefix(source: Path, view: Path) -> list[str]:
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
    # Interpreter trees under a masked prefix stay visible, read-only.
    for path in sorted({str(Path(sys.prefix).resolve()), str(Path(sys.base_prefix).resolve())}):
        if path.startswith((*MASKED_TREES, SANDBOX_TMP)):
            command += ["--ro-bind", path, path]
    # The tree under test is presented at one fixed path for every phase, so RED and GREEN see identical
    # paths and no git metadata (snapshots carry none); a test cannot tell which phase it is in.
    command += ["--ro-bind", str(source.resolve()), str(view.resolve())]
    command += ["--unshare-net", "--unshare-pid", "--die-with-parent", "--chdir", str(view.resolve())]
    return command


def run(args: list[str], *, cwd: Path, timeout: int = TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)


def run_pytest(selectors: list[str], *, source: Path, view: Path) -> subprocess.CompletedProcess[str]:
    """Run selectors with a scrubbed environment and no network; never inherits the preflight process."""
    command = [
        *sandbox_prefix(source, view),
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
        cwd=source,
        env=scrubbed_env(SANDBOX_TMP),
        text=True,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


def snapshot_tracked(root: Path, destination: Path) -> None:
    """Copy the tracked files of ``root`` (no .git) into ``destination``, preserving symlinks as symlinks.

    Fails closed when a tracked path is absolute, climbs out with ``..``, or has a symlink ancestor inside the
    snapshot: a candidate-controlled tracked symlink must not redirect a later copy outside ``destination``.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, timeout=30, check=True
    ).stdout.split(b"\0")
    base = destination.resolve()
    for raw in filter(None, listing):
        relative = raw.decode("utf-8", "surrogateescape")
        parts = Path(relative).parts
        if Path(relative).is_absolute() or ".." in parts:
            raise ValueError(f"tracked path escapes the snapshot: {relative!r}")
        ancestor = destination
        for part in parts[:-1]:
            ancestor = ancestor / part
            if ancestor.is_symlink():
                raise ValueError(f"tracked path traverses a symlink: {relative!r}")
        source = root / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve().is_relative_to(base):
            raise ValueError(f"tracked path escapes the snapshot: {relative!r}")
        if source.is_symlink():
            os.symlink(os.readlink(source), target)
        elif source.is_file():
            shutil.copy2(source, target)


def extract_baseline(root: Path, sha: str, destination: Path) -> str | None:
    """Export the exact baseline commit (no .git) with tarfile's data filter; return an error text or None."""
    archive = subprocess.run(["git", "archive", sha], cwd=root, capture_output=True, timeout=60, check=False)
    if archive.returncode:
        return f"baseline export failed: {archive.stderr[-200:]!r}"
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
            bundle.extractall(destination, filter="data")
    except (tarfile.TarError, OSError) as error:
        return f"baseline export rejected: {error}"
    return None


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
    if run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=root, timeout=15).returncode:
        return [f"{ident}: vulnerable_sha unavailable; fetch the exact baseline commit"]
    with tempfile.TemporaryDirectory(prefix="hunter-finding-") as temp:
        red_tree = Path(temp) / "red"
        green_tree = Path(temp) / "green"
        red_tree.mkdir()
        green_tree.mkdir()
        problem = extract_baseline(root, sha, red_tree)
        if problem:
            return [f"{ident}: {problem}"]
        # Copy the regression test file from the fixed HEAD into the vulnerable tree so RED and GREEN execute
        # identical test code. Never follow a baseline-controlled symlink, including parent components.
        test_file = red.split("::", 1)[0]
        destination = red_tree / test_file
        if any(part.is_symlink() for part in [destination, *destination.parents] if red_tree in (part, *part.parents)):
            return [f"{ident}: baseline test destination contains a symlink"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.resolve().is_relative_to(red_tree.resolve()):
            return [f"{ident}: baseline test destination escapes the exported tree"]
        shutil.copy2(root / test_file, destination)
        red_result = run_pytest([red], source=red_tree, view=root)
        if red_result.returncode == 0:
            errors.append(f"{ident}: RED did not fail on vulnerable_sha")
        elif red_result.returncode != 1 or " failed" not in red_result.stdout:
            errors.append(f"{ident}: RED was not a genuine assertion failure (exit {red_result.returncode})")
        snapshot_tracked(root, green_tree)
        current = run_pytest([green, *adversarial], source=green_tree, view=root)
        if current.returncode:
            errors.append(f"{ident}: GREEN/adversarial tests failed: {current.stdout[-500:]} {current.stderr[-200:]}")
    return errors


def collect_errors(root: Path = ROOT) -> list[str]:
    """Validate every disposition of ``root``; the trusted lane calls this from its own (base) copy of this module."""
    findings = json.loads((root / "docs/REVIEWER_FINDING_DISPOSITIONS.json").read_text())["findings"]
    errors: list[str] = []
    for finding in findings:
        try:
            errors.extend(validate_proof(finding, root=root))
        except IsolationUnavailable as unavailable:
            errors.append(f"{finding.get('id')}: proof refused, {unavailable}")
        except ValueError as refused:
            errors.append(f"{finding.get('id')}: proof refused, {refused}")
        except subprocess.TimeoutExpired:
            errors.append(f"{finding.get('id')}: proof timed out (bounded); simplify tests or repair baseline")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded RED/GREEN replay for resolved reviewer findings")
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository whose dispositions and tests are proven.")
    root = parser.parse_args().root.resolve()
    errors = collect_errors(root)
    for error in errors:
        print("[Finding Proof] FAIL:", error)
    if not errors:
        print("[Finding Proof] PASS: bounded RED/GREEN/adversarial proofs")
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
