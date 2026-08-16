from __future__ import annotations

import json
from pathlib import Path

from hunter_governance_review.__main__ import _write_summary
from hunter_governance_review.contracts import (
    ChangedFile,
    DeterministicResult,
    Finding,
    FindingClassification,
    Outcome,
    PullRequest,
    ReviewPair,
    Severity,
)
from hunter_governance_review.decision import Decision
from hunter_governance_review.deterministic import (
    ValidationContext,
    run_deterministic_engine,
)


def _pair() -> ReviewPair:
    return ReviewPair(
        repository="fafa33/Project-Hunter",
        pull_request_number=275,
        source_branch="governance/issue-274-repeat-defect-hardening",
        source_head_sha="a" * 40,
        target_branch="main",
        target_base_sha="b" * 40,
        workflow_run_id="test",
        review_timestamp="2026-08-16T00:00:00+00:00",
    )


def test_systemic_finding_serialization_preserves_required_metadata() -> None:
    finding = Finding(
        validator_id="V-040",
        title="acceptance-criteria matrix is missing",
        severity=Severity.BLOCKING,
        detail="matrix missing",
        classification=FindingClassification.SYSTEMIC,
        classification_evidence="V-040 is a deterministic reusable PR-contract validator.",
        reusable_boundary="CanonicalPRContract acceptance-matrix validation",
    )
    payload = finding.to_dict()
    assert payload["classification"] == "systemic"
    assert payload["classification_evidence"]
    assert payload["reusable_boundary"]
    assert payload["classification_complete"] is True
    assert isinstance(payload["classification_complete"], bool)
    assert finding.classification_complete
    assert "classification=systemic" in finding.render()


def test_isolated_finding_serialization_is_supported() -> None:
    finding = Finding(
        validator_id="TEST",
        title="one-off test finding",
        severity=Severity.BLOCKING,
        detail="specific instance",
        classification=FindingClassification.ISOLATED,
        classification_evidence=("Independent review determined the defect is contribution-specific."),
    )
    assert finding.classification_complete
    assert finding.to_dict()["classification"] == "isolated"
    assert finding.to_dict()["reusable_boundary"] is None


def test_incomplete_blocking_classification_fails_closed() -> None:
    finding = Finding("V-020", "body invalid", Severity.BLOCKING, "detail")
    result = DeterministicResult(findings=[finding])
    serialized_finding = finding.to_dict()
    assert result.blocking
    assert not finding.classification_complete
    assert result.classification_errors
    assert result.to_dict()["classification_errors"]
    assert serialized_finding["classification_complete"] is False
    assert isinstance(serialized_finding["classification_complete"], bool)


def test_deterministic_blocker_is_systemic_at_reusable_boundary() -> None:
    pr = PullRequest(
        number=275,
        title="governance hardening",
        body="",
        state="open",
        draft=False,
        head_ref_name="governance/issue-274-repeat-defect-hardening",
        head_oid="a" * 40,
        base_ref_name="main",
        base_oid="b" * 40,
        mergeable="MERGEABLE",
        changed_files=1,
        url="https://github.com/fafa33/Project-Hunter/pull/275",
    )
    result = run_deterministic_engine(
        ValidationContext(
            pr=pr,
            files=[ChangedFile("docs/test.md", "modified", 1, 1)],
        )
    )
    blockers = [finding for finding in result.findings if finding.severity is Severity.BLOCKING]
    assert blockers
    assert all(finding.classification is FindingClassification.SYSTEMIC for finding in blockers)
    assert all(finding.classification_complete for finding in blockers)
    assert all(finding.reusable_boundary for finding in blockers)


def test_summary_contains_human_and_structured_classification(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    finding = Finding(
        validator_id="V-050",
        title="readiness missing",
        severity=Severity.BLOCKING,
        detail="missing declaration",
        classification=FindingClassification.SYSTEMIC,
        classification_evidence=("Deterministic readiness validator exposed a reusable contract failure."),
        reusable_boundary="CanonicalPRContract readiness validation",
    )
    result = DeterministicResult(findings=[finding])
    _write_summary(
        {"GITHUB_STEP_SUMMARY": str(summary)},
        decision=Decision(Outcome.CHANGES_REQUIRED, "blocking finding"),
        pair=_pair(),
        deterministic=result,
        context=None,
        published_state="failure",
    )
    text = summary.read_text(encoding="utf-8")
    assert "classification=systemic" in text
    assert "Deterministic finding metadata" in text
    fenced = text.split("```json\n", 1)[1].split("\n```", 1)[0]
    payload = json.loads(fenced)
    assert payload["findings"][0]["classification"] == "systemic"
    assert payload["findings"][0]["classification_complete"] is True
