"""ADR 0039 L6 (RD-3): the promotion delta is a deterministic pure function, and every refusal is load-bearing.

Each refusal is paired with a positive case, because a guard that never fires is indistinguishable from a
guard that is not there: the empty-entry, unknown-family and unsorted-entry refusals in particular are the ones
that stop a partially computed promotion from being written into the governed registry.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from hunter.automation import issue_agent_remediation as remediation

REGISTRY_PATH, DISPOSITIONS_PATH = remediation.PROMOTION_PATHS
FINDING = "1" * 64
OTHER = "2" * 64

REGISTRY: dict[str, Any] = {
    "version": 1,
    "purpose": "canonical registry",
    "defects": [],
    "families": [
        {
            "id": "DFF-001",
            "title": "an-already-proven-family",
            "invariant": "an already proven invariant that holds on every guard surface",
            "applicability": {"changed_paths": ["src/hunter/"], "rationale": "the guard surface"},
            "prevention": {"mechanism": "ADR 0039 L3.2", "boundary": "review"},
            "regression_evidence": ["tests/test_other.py::test_the_previous_proof"],
            "lifecycle": "regression-tested",
            "sources": ["PR #590"],
        }
    ],
}
DISPOSITIONS: dict[str, Any] = {"version": 1, "purpose": "canonical dispositions", "findings": []}


def _canonical(document: object) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(root)], check=True)
    (root / "src" / "hunter").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / REGISTRY_PATH).write_text(_canonical(REGISTRY))
    (root / DISPOSITIONS_PATH).write_text(_canonical(DISPOSITIONS))
    (root / "src" / "hunter" / "guard.py").write_text("VALUE = 1\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=s", "-c", "user.email=s@s", "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    return root, _git(root, "rev-parse", "HEAD")


def entry(finding_id: str = FINDING, **changes: Any) -> dict[str, Any]:
    value = {
        "finding_id": finding_id,
        "guard": "src/hunter/guard.py",
        "regression_tests": ["tests/test_guard.py::test_the_guard_holds"],
        "provenance": {
            "pull_request_number": 600,
            "reviewed_head_sha": "d" * 40,
            "reviewer": "codex[bot]",
            "comment_id": 4242,
        },
        "family_id": "DFF-001",
    }
    value.update(changes)
    return value


def promote(
    repo: Path, head: str, *, group: dict[str, Any], proposal: dict[str, Any], finding: dict[str, Any]
) -> dict[str, bytes]:
    return remediation.promote(repo=repo, group=group, proposal=proposal, finding=finding)


GROUP = {"parent_authorization_id": "hunter-issue-agent-authorization:" + "a" * 64, "pull_request_number": 600}
FINDING_PROVENANCE = {
    "finding_id": FINDING,
    "path": "src/hunter/guard.py",
    "pull_request_number": 600,
    "reviewed_head_sha": "d" * 40,
    "reviewer": "codex[bot]",
    "comment_id": 4242,
}
NEW_FAMILY = {"new_family": {"title": "a-brand-new-invariant", "invariant": "a brand new proven invariant"}}


# --- the delta itself --------------------------------------------------------------------------------------


def test_a_matched_family_gains_the_regression_evidence_and_one_disposition(repo: tuple[Path, str]) -> None:
    root, head = repo
    delta = remediation.promotion_delta(
        root, base_sha=head, entries=[entry() | {"regression_tests": ["tests/test_guard.py::test_the_guard_holds"]}]
    )
    assert set(delta) == set(remediation.PROMOTION_PATHS)
    registry = json.loads(delta[REGISTRY_PATH])
    assert registry["families"][0]["regression_evidence"] == [
        "tests/test_other.py::test_the_previous_proof",
        "tests/test_guard.py::test_the_guard_holds",
    ]
    findings = json.loads(delta[DISPOSITIONS_PATH])["findings"]
    assert len(findings) == 1
    record = findings[0]
    assert record["id"] == f"RFD-600-{FINDING[:12]}"
    assert record["classification"] == "recurrence" and record["mapped_defect_id"] == "DFF-001"
    assert record["validation_state"] == "validated" and record["resolution_state"] == "resolved"
    assert record["source_provenance"] == {
        "reviewer": "codex[bot]",
        "pr_number": 600,
        "reference": f"PR #600 review comment 4242 at head {'d' * 40}",
    }
    assert record["guard_reference"] == "src/hunter/guard.py"
    assert record["test_reference"] == "tests/test_guard.py::test_the_guard_holds"


def test_the_delta_is_byte_identical_on_every_derivation(repo: tuple[Path, str]) -> None:
    root, head = repo
    first = remediation.promotion_delta(root, base_sha=head, entries=[entry()])
    second = remediation.promotion_delta(root, base_sha=head, entries=[entry()])
    assert first == second


@pytest.mark.parametrize("path", [REGISTRY_PATH, DISPOSITIONS_PATH])
def test_the_promotion_never_reformats_a_governed_file(repo: tuple[Path, str], path: str) -> None:
    """The delta's serialization is the repository's canonical one, so promoting changes only the promotion.

    If the two ever diverged, every remediation would produce a whole-file diff on the governed registry and
    the reviewer could no longer read the promotion as the change it is.
    """

    root, _head = repo
    base = (root / path).read_text(encoding="utf-8")
    assert _canonical(json.loads(base)) == base


def test_a_proven_regression_is_appended_once_and_never_duplicated(repo: tuple[Path, str]) -> None:
    root, head = repo
    delta = remediation.promotion_delta(root, base_sha=head, entries=[entry()])
    already = json.loads(delta[REGISTRY_PATH])["families"][0]["regression_evidence"]
    assert already == ["tests/test_other.py::test_the_previous_proof", "tests/test_guard.py::test_the_guard_holds"]
    assert len(already) == len(set(already))


def test_a_new_family_gets_the_next_identity_and_the_smallest_truthful_record(
    repo: tuple[Path, str],
) -> None:
    root, head = repo
    new_entry = {key: value for key, value in entry().items() if key != "family_id"}
    new_entry["new_family"] = {
        "title": NEW_FAMILY["new_family"]["title"],
        "invariant": NEW_FAMILY["new_family"]["invariant"],
        "changed_paths": ["src/hunter/guard.py"],
    }
    delta = remediation.promotion_delta(root, base_sha=head, entries=[new_entry])
    registry = json.loads(delta[REGISTRY_PATH])
    created = registry["families"][-1]
    assert created["id"] == "DFF-002" and created["lifecycle"] == "regression-tested"
    assert created["title"] == NEW_FAMILY["new_family"]["title"]
    assert created["invariant"] == NEW_FAMILY["new_family"]["invariant"]
    assert created["regression_evidence"] == ["tests/test_guard.py::test_the_guard_holds"]
    record = json.loads(delta[DISPOSITIONS_PATH])["findings"][-1]
    assert record["classification"] == "new_systemic_defect"
    assert record["mapped_defect_id"] == "DFF-002"
    assert record["mapped_defect_class"] == created["title"]


def test_two_proven_findings_promote_into_one_delta(repo: tuple[Path, str]) -> None:
    root, head = repo
    entries = [entry(), entry(OTHER, family_id="DFF-001")]
    delta = remediation.promotion_delta(root, base_sha=head, entries=entries)
    assert len(json.loads(delta[DISPOSITIONS_PATH])["findings"]) == 2
    evidence = json.loads(delta[REGISTRY_PATH])["families"][0]["regression_evidence"]
    assert evidence.count("tests/test_guard.py::test_the_guard_holds") == 1


# --- the refusals ------------------------------------------------------------------------------------------


def test_a_delta_without_a_proven_finding_is_refused(repo: tuple[Path, str]) -> None:
    """An empty promotion rewrites the governed files for no reason; it must never be written at all."""

    root, head = repo
    with pytest.raises(remediation.PromotionRefused, match="at least one proven finding"):
        remediation.promotion_delta(root, base_sha=head, entries=[])


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([entry(OTHER), entry(FINDING)], "unique and sorted"),
        ([entry(FINDING), entry(FINDING)], "unique and sorted"),
    ],
)
def test_unsorted_or_duplicated_entries_are_refused(
    repo: tuple[Path, str], entries: Sequence[dict[str, Any]], message: str
) -> None:
    root, head = repo
    with pytest.raises(remediation.PromotionRefused, match=message):
        remediation.promotion_delta(root, base_sha=head, entries=list(entries))


def test_a_family_that_does_not_exist_at_the_reviewed_head_is_refused(repo: tuple[Path, str]) -> None:
    root, head = repo
    with pytest.raises(remediation.PromotionRefused, match="does not exist at the reviewed head"):
        remediation.promotion_delta(root, base_sha=head, entries=[entry(family_id="DFF-404")])


def test_a_registry_without_any_family_identity_is_refused(repo: tuple[Path, str]) -> None:
    root, head = repo
    (root / REGISTRY_PATH).write_text(_canonical({**REGISTRY, "families": []}))
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=s", "-c", "user.email=s@s", "-c", "commit.gpgsign=false", "commit", "-qm", "empty")
    empty = _git(root, "rev-parse", "HEAD")
    with pytest.raises(remediation.PromotionRefused, match="no family identity"):
        remediation.promotion_delta(
            root,
            base_sha=empty,
            entries=[
                {k: v for k, v in entry().items() if k != "family_id"}
                | {"new_family": {"title": "t", "invariant": "i", "changed_paths": ["src/hunter/guard.py"]}}
            ],
        )


def test_a_governed_file_that_is_missing_at_the_reviewed_head_is_refused(
    repo: tuple[Path, str],
) -> None:
    root, head = repo
    (root / DISPOSITIONS_PATH).unlink()
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=s", "-c", "user.email=s@s", "-c", "commit.gpgsign=false", "commit", "-qm", "drop")
    with pytest.raises(Exception, match="could not read"):
        remediation.promotion_delta(root, base_sha=_git(root, "rev-parse", "HEAD"), entries=[entry()])


def test_a_governed_file_that_is_not_json_is_refused(repo: tuple[Path, str]) -> None:
    root, head = repo
    (root / DISPOSITIONS_PATH).write_text("not json\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=s", "-c", "user.email=s@s", "-c", "commit.gpgsign=false", "commit", "-qm", "break")
    with pytest.raises(remediation.PromotionRefused, match="not JSON"):
        remediation.promotion_delta(root, base_sha=_git(root, "rev-parse", "HEAD"), entries=[entry()])


def test_a_registry_without_the_expected_shape_is_refused(repo: tuple[Path, str]) -> None:
    root, head = repo
    (root / REGISTRY_PATH).write_text(_canonical({"version": 1, "purpose": "no families key"}))
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=s", "-c", "user.email=s@s", "-c", "commit.gpgsign=false", "commit", "-qm", "reshape")
    with pytest.raises(remediation.PromotionRefused, match="expected shapes"):
        remediation.promotion_delta(root, base_sha=_git(root, "rev-parse", "HEAD"), entries=[entry()])


# --- the provenance binding --------------------------------------------------------------------------------


def test_promote_refuses_a_proposal_that_names_another_finding(repo: tuple[Path, str]) -> None:
    root, head = repo
    with pytest.raises(remediation.PromotionRefused, match="does not name the finding"):
        promote(
            root,
            head,
            group=GROUP | {"bound_head_sha": head},
            proposal={
                "finding_id": OTHER,
                "disposition": {"family_id": "DFF-001"},
                "regression_tests": ["tests/test_guard.py::test_the_guard_holds"],
            },
            finding=FINDING_PROVENANCE,
        )


def test_promote_refuses_a_finding_observed_on_another_pull_request(repo: tuple[Path, str]) -> None:
    root, head = repo
    with pytest.raises(remediation.PromotionRefused, match="not observed on the remediated pull request"):
        promote(
            root,
            head,
            group=GROUP | {"bound_head_sha": head},
            proposal={
                "finding_id": FINDING,
                "disposition": {"family_id": "DFF-001"},
                "regression_tests": ["tests/test_guard.py::test_the_guard_holds"],
            },
            finding=FINDING_PROVENANCE | {"pull_request_number": 601},
        )


def test_promote_binds_the_reviewed_head_and_never_another_one(repo: tuple[Path, str]) -> None:
    root, head = repo
    proposal = {
        "finding_id": FINDING,
        "disposition": {"family_id": "DFF-001"},
        "regression_tests": ["tests/test_guard.py::test_the_guard_holds"],
    }
    delta = promote(root, head, group=GROUP | {"bound_head_sha": head}, proposal=proposal, finding=FINDING_PROVENANCE)
    record = json.loads(delta[DISPOSITIONS_PATH])["findings"][0]
    assert record["source_provenance"]["pr_number"] == 600
    # A head with no governed files cannot be promoted from: the delta is refused, never guessed.
    with pytest.raises(Exception, match="could not read"):
        promote(root, head, group=GROUP | {"bound_head_sha": "0" * 40}, proposal=proposal, finding=FINDING_PROVENANCE)
