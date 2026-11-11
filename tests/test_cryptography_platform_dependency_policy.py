"""Issue #559: Intel macOS x86_64 must always resolve a cryptography release with a real wheel.

Upstream cryptography dropped the Intel macOS (x86_64) wheel at 49.0.0 -- every later release is
``arm64``-only; the last ``universal2`` (and so the last Intel-compatible) release was 48.0.1. The
owner's Intel Mac is a required execution target, and the ``--only-binary ':all:'`` boundary stays:
this is deterministic environment-marker selection, never a source build or a relaxed version
range. ``pyproject.toml`` and ``requirements/ci-constraints.txt`` carry matching markers so they
cannot disagree, and this test derives "matching" from the markers' own text, not a hand-copied
assumption of what they say.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parents[1]
DARWIN_X86_64 = {"sys_platform": "darwin", "platform_machine": "x86_64"}
OTHER_TARGETS = [
    {"sys_platform": "linux", "platform_machine": "x86_64"},
    {"sys_platform": "darwin", "platform_machine": "arm64"},
    {"sys_platform": "win32", "platform_machine": "AMD64"},
]


def _pyproject_cryptography_requirements() -> list[Requirement]:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return [Requirement(spec) for spec in document["project"]["dependencies"] if spec.startswith("cryptography")]


def _constraints_cryptography_lines() -> list[tuple[str, str | None]]:
    """``(version, marker_text)`` for every ``cryptography==`` line in the pinned constraints file."""
    lines = (ROOT / "requirements" / "ci-constraints.txt").read_text(encoding="utf-8").splitlines()
    found: list[tuple[str, str | None]] = []
    for line in lines:
        match = re.match(r"^cryptography==([0-9.]+)\s*(?:;\s*(.+))?$", line.strip())
        if match:
            found.append((match.group(1), match.group(2)))
    return found


def test_pyproject_declares_exactly_two_platform_scoped_cryptography_entries() -> None:
    requirements = _pyproject_cryptography_requirements()
    assert len(requirements) == 2
    assert all(req.marker is not None for req in requirements)


def test_pyproject_entries_select_the_right_release_for_darwin_x86_64_and_everything_else() -> None:
    requirements = _pyproject_cryptography_requirements()
    for environment in (DARWIN_X86_64, *OTHER_TARGETS):
        matching = [req for req in requirements if req.marker.evaluate(environment)]
        assert len(matching) == 1, (environment, matching)
    darwin_match = next(req for req in requirements if req.marker.evaluate(DARWIN_X86_64))
    assert darwin_match.specifier == SpecifierSet(">=48.0.1,<49")
    for environment in OTHER_TARGETS:
        other_match = next(req for req in requirements if req.marker.evaluate(environment))
        assert other_match.specifier == SpecifierSet(">=50.0.0,<51")


def test_macos_x86_64_never_resolves_to_a_release_without_a_compatible_wheel() -> None:
    """The one invariant this issue exists to guarantee, stated directly."""
    requirements = _pyproject_cryptography_requirements()
    darwin_match = next(req for req in requirements if req.marker.evaluate(DARWIN_X86_64))
    assert "50.0.0" not in str(darwin_match.specifier)  # 50.x publishes no x86_64/universal2 wheel


def test_ci_constraints_pin_exactly_two_platform_scoped_cryptography_releases() -> None:
    lines = _constraints_cryptography_lines()
    assert len(lines) == 2
    by_version = {version: marker for version, marker in lines}
    assert by_version.get("48.0.1") is not None
    assert by_version.get("50.0.0") is not None


def test_pyproject_and_ci_constraints_markers_cannot_disagree() -> None:
    """The two files must select the same release for the same environment -- derived, not assumed."""
    requirements = _pyproject_cryptography_requirements()
    constraints = _constraints_cryptography_lines()
    for environment in (DARWIN_X86_64, *OTHER_TARGETS):
        pyproject_version = str(next(req for req in requirements if req.marker.evaluate(environment)).specifier)
        constraint_versions = [
            version for version, marker_text in constraints if marker_text and Marker(marker_text).evaluate(environment)
        ]
        assert len(constraint_versions) == 1, (environment, constraint_versions)
        assert constraint_versions[0] in pyproject_version


def test_binary_only_installation_boundary_is_unchanged() -> None:
    """Issue #559 forbids a source-build fallback; the install command itself must stay untouched."""
    workflow = (ROOT / ".github" / "workflows" / "hunter-canonicalization-auto.yml").read_text(encoding="utf-8")
    assert "--only-binary ':all:'" in workflow
    assert "--no-binary" not in workflow
