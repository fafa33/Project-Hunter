from pathlib import Path

import pytest

from hunter_trusted_formatter_bootstrap import bootstrap_plan, read_black_pin


def _constraints(tmp_path: Path, name: str, *pins: str) -> Path:
    """Create a minimal constraints file containing the requested Black pins."""
    path = tmp_path / name
    path.write_text("\n".join(pins) + "\n", encoding="utf-8")
    return path


def test_matching_black_pin_requires_no_bootstrap() -> None:
    """Keep the trusted formatter unchanged when candidate and trusted pins match."""
    assert bootstrap_plan("24.10.0", "24.10.0") == ()


def test_allowlisted_black_upgrade_has_fully_pinned_closure() -> None:
    """Bootstrap the approved formatter with its new dependency pinned exactly."""
    assert bootstrap_plan("24.10.0", "26.3.1") == ("black==26.3.1", "pytokens==0.4.0")


def test_unapproved_black_upgrade_fails_closed() -> None:
    """Reject formatter upgrades that are not explicitly trusted."""
    with pytest.raises(ValueError, match="not an approved"):
        bootstrap_plan("24.10.0", "26.4.0")


def test_missing_black_pin_fails_closed(tmp_path: Path) -> None:
    """Reject constraints without an exact Black pin."""
    path = _constraints(tmp_path, "missing.txt", "ruff==0.15.21")
    with pytest.raises(ValueError, match="exactly one exact Black pin"):
        read_black_pin(path)


def test_duplicate_black_pins_fail_closed(tmp_path: Path) -> None:
    """Reject ambiguous constraints with more than one exact Black pin."""
    path = _constraints(tmp_path, "duplicate.txt", "black==24.10.0", "black==26.3.1")
    with pytest.raises(ValueError, match="exactly one exact Black pin"):
        read_black_pin(path)


def test_workflow_wires_trusted_selector_and_no_deps_install() -> None:
    """Keep a small structural assertion for the trusted workflow wiring."""
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-trusted-preflight-upgrade.yml"
    ).read_text(encoding="utf-8")

    assert "python scripts/hunter_trusted_formatter_bootstrap.py" in workflow
    assert "candidate/requirements/ci-constraints.txt" in workflow
    assert "--no-deps" in workflow
