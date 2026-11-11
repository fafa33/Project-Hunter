"""Issue #559: Intel macOS x86_64 must install a cryptography release with no known CVEs.

Upstream cryptography dropped the Intel macOS (x86_64) wheel at 49.0.0 -- every later release
ships arm64-only macOS wheels. The three advisories GitHub's dependency-review flagged against the
newest wheel-having release (48.0.1) -- GHSA-g6cj-pr64-35w5, GHSA-jwv3-5hgf-82ww,
GHSA-m2h6-j472-rp4c -- are all fixed only at 49.0.0 or later, so no cryptography release is both
Intel-wheel-compatible and patched. Pinning an older wheel-having release would trade the install
failure for known, unpatched, HIGH-severity CVEs.

The resolution keeps the same patched 50.0.0 release on every platform (no version skew, no
security trade-off) and instead builds exactly that one package from source on the one self-hosted
Intel macOS runner that lacks a wheel for it -- verified separately to succeed there with the
runner's pinned Rust toolchain. ``--only-binary ':all:'`` stays the default for everything else;
this is a named, narrow, deliberate exception, not a loosened boundary.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHED_VERSION = "50.0.0"
CANONICALIZATION_WORKFLOW = ROOT / ".github" / "workflows" / "hunter-canonicalization-auto.yml"


def _pyproject_cryptography_spec() -> str:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    specs = [spec for spec in document["project"]["dependencies"] if spec.startswith("cryptography")]
    assert len(specs) == 1, "exactly one cryptography entry: no platform split, no version skew"
    return specs[0]


def _constraints_cryptography_lines() -> list[str]:
    lines = (ROOT / "requirements" / "ci-constraints.txt").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip().startswith("cryptography==")]


def _canonicalization_install_line() -> str:
    text = CANONICALIZATION_WORKFLOW.read_text(encoding="utf-8")
    (line,) = (line for line in text.splitlines() if re.search(r"pip install .*--only-binary", line))
    return line.strip()


def test_pyproject_pins_one_unconditional_patched_cryptography_release() -> None:
    spec = _pyproject_cryptography_spec()
    assert ";" not in spec, "no platform marker: the same patched release applies everywhere"
    assert spec == "cryptography>=50.0.0,<51"


def test_ci_constraints_pin_exactly_the_same_patched_release_everywhere() -> None:
    lines = _constraints_cryptography_lines()
    assert lines == [f"cryptography=={PATCHED_VERSION}"]


def test_pyproject_and_ci_constraints_cannot_disagree() -> None:
    spec = _pyproject_cryptography_spec()
    (constraint,) = _constraints_cryptography_lines()
    pinned_version = constraint.removeprefix("cryptography==")
    assert f">={pinned_version}," in spec


def test_canonicalization_install_keeps_binary_only_as_the_default() -> None:
    line = _canonicalization_install_line()
    assert "--only-binary ':all:'" in line


def test_canonicalization_install_exempts_only_cryptography_from_binary_only() -> None:
    """The narrow, deliberate exception this issue requires -- and nothing wider than it."""
    line = _canonicalization_install_line()
    no_binary_args = re.findall(r"--no-binary[= ]('[^']*'|\S+)", line)
    assert no_binary_args == ["cryptography"], no_binary_args


def test_no_other_workflow_carries_a_no_binary_override() -> None:
    """This is a one-runner, one-package exception; it must not have spread anywhere else."""
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        if path != CANONICALIZATION_WORKFLOW:
            assert "no-binary" not in path.read_text(encoding="utf-8"), path
