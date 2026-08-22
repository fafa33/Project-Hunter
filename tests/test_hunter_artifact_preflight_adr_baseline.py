"""Cutoff-bound accepted-ADR accounting for pinned FULL audits.

A FULL architecture audit is pinned to an exact reviewed revision and evidence
cutoff. Requiring it to structurally account for an ADR accepted *after* that
cutoff is impossible to satisfy honestly: the decision did not exist in the
audit's evidence namespace. Judging a pinned historical artifact against current
state is the substitution ADR 0020 and ADR 0033 prohibit, and left uncorrected it
makes every later ADR acceptance retroactively invalidate an already-merged
audit.

These fixtures are paired deliberately. The relaxation must apply *only* to
decisions the audit could not have seen; an ADR accepted at or before the
baseline, and every unresolvable baseline, must still be enforced strictly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import hunter_artifact_preflight
import pytest
from test_hunter_artifact_preflight import _good_audit

BASELINE_ADRS = ("0001", "0002")
LATER_ADR = "0003"
ABSENT_REVISION = "f" * 40
CANONICAL_FIXTURE_REVISION = "0123456789abcdef0123456789abcdef01234567"


def _adr_index(*accepted: str) -> str:
    rows = "\n".join(f"| [{adr}]({adr}-example.md) | Example {adr} | Accepted | Example scope |" for adr in accepted)
    return (
        "# Architecture Decision Records\n\n## Index\n\n| ADR | Title | Status | Primary Scope |\n| --- | --- | --- | --- |\n"
        + rows
        + "\n"
    )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


@pytest.fixture
def pinned_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A repo whose baseline commit accepted only BASELINE_ADRS."""
    repo = tmp_path / "repo"
    (repo / "docs" / "ADR").mkdir(parents=True)
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    index = repo / "docs" / "ADR" / "README.md"
    index.write_text(_adr_index(*BASELINE_ADRS), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    baseline = _git(repo, "rev-parse", "HEAD")

    # A later commit accepts one more ADR the pinned audit could not have seen.
    index.write_text(_adr_index(*BASELINE_ADRS, LATER_ADR), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "accept later ADR")

    monkeypatch.setattr(hunter_artifact_preflight, "ROOT", repo)
    return baseline


def _audit(revision: str, *accounted: str, baseline: str | None = None) -> str:
    """A canonically valid FULL audit, optionally declaring a repository-evidence baseline.

    Built from the shared valid-audit fixture so this module tests only the
    baseline-resolution boundary, not a private copy of the audit template.

    `revision` is the reviewed artifact's revision; `baseline` is the separately
    declared repository-evidence namespace. They are deliberately distinct.
    """
    text = _good_audit(accepted_adrs=tuple(accounted))
    if baseline is not None:
        text = text.replace(
            f"- Reviewed revision: `{CANONICAL_FIXTURE_REVISION}`\n",
            f"- Reviewed revision: `{CANONICAL_FIXTURE_REVISION}`\n" f"- Repository evidence baseline: `{baseline}`\n",
        )
    return text.replace(CANONICAL_FIXTURE_REVISION, revision)


def _adr_errors(errors: list[str], adr: str) -> list[str]:
    return [error for error in errors if f"ADR {adr}" in error]


def test_adr_accepted_after_the_proven_baseline_is_not_required(pinned_repo: str) -> None:
    """The positive half: a canonically valid pinned audit is not falsely rejected."""
    errors = hunter_artifact_preflight.validate_audit_text(
        _audit(CANONICAL_FIXTURE_REVISION, *BASELINE_ADRS, baseline=pinned_repo),
        accepted_adrs=[*BASELINE_ADRS, LATER_ADR],
    )

    assert _adr_errors(errors, LATER_ADR) == []
    assert errors == []


def test_adr_accepted_at_the_baseline_is_still_required(pinned_repo: str) -> None:
    """The negative half: the relaxation must not become a blanket exemption."""
    errors = hunter_artifact_preflight.validate_audit_text(
        _audit(CANONICAL_FIXTURE_REVISION, BASELINE_ADRS[0], baseline=pinned_repo),
        accepted_adrs=[*BASELINE_ADRS, LATER_ADR],
    )

    assert _adr_errors(errors, BASELINE_ADRS[1])


def test_unresolvable_baseline_falls_back_to_the_stricter_current_set(pinned_repo: str) -> None:
    """An unresolvable revision must require more accounting, never less."""
    errors = hunter_artifact_preflight.validate_audit_text(
        _audit(CANONICAL_FIXTURE_REVISION, *BASELINE_ADRS, baseline=ABSENT_REVISION),
        accepted_adrs=[*BASELINE_ADRS, LATER_ADR],
    )

    assert _adr_errors(errors, LATER_ADR)


def test_missing_revision_declaration_falls_back_to_the_stricter_current_set(pinned_repo: str) -> None:
    text = _audit(pinned_repo, *BASELINE_ADRS).replace(f"- Reviewed revision: `{pinned_repo}`\n", "")

    errors = hunter_artifact_preflight.validate_audit_text(text, accepted_adrs=[*BASELINE_ADRS, LATER_ADR])

    assert _adr_errors(errors, LATER_ADR)


def test_absent_baseline_declaration_falls_back_to_the_stricter_current_set(pinned_repo: str) -> None:
    resolved = hunter_artifact_preflight.accepted_adrs_at_baseline(None, current=[*BASELINE_ADRS, LATER_ADR])

    assert resolved == [*BASELINE_ADRS, LATER_ADR]


def test_baseline_resolution_reads_the_index_at_that_revision(pinned_repo: str) -> None:
    resolved = hunter_artifact_preflight.accepted_adrs_at_baseline(pinned_repo, current=[*BASELINE_ADRS, LATER_ADR])

    assert resolved == list(BASELINE_ADRS)
