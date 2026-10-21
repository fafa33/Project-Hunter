"""Regression coverage for non-authoritative historical proof accounting."""

from __future__ import annotations

import pytest

from hunter.automation.historical_finding_coverage import coverage
from hunter.automation.issue_agent_knowledge import Finding, KnowledgeView


def view() -> KnowledgeView:
    result = KnowledgeView(repository_id=1)
    result.findings["f" * 64] = Finding(
        {"provenance": {"pull_request_number": 579, "comment_id": 123, "path": "src/x.py"}}
    )
    return result


def test_resolved_github_thread_does_not_fabricate_proof() -> None:
    report = coverage(
        view(),
        pull_requests=[579],
        github_threads={(579, 123): {"pull_request_number": 579, "comment_id": 123, "resolved": True}},
    )
    assert report["examined"] == 1
    assert report["proof_gap"] == 1
    assert report["findings"][0]["status"] == "github-resolved-without-signed-proof"
    assert report["findings"][0]["regression_tests"] == []


def test_missing_external_evidence_is_not_marked_resolved() -> None:
    report = coverage(view(), pull_requests=[579], github_threads={})
    assert report["counts"] == {"source-unavailable": 1}
    assert report["proof_gap"] == 1


def test_signed_proof_is_separate_from_resolution() -> None:
    known = view()
    known.findings["f" * 64].proven = {"regression_tests": ["tests/test_x.py::test_fix"]}
    report = coverage(
        known,
        pull_requests=[579],
        github_threads={(579, 123): {"pull_request_number": 579, "comment_id": 123, "resolved": True}},
    )
    assert report["signed_proven"] == 1
    assert report["findings"][0]["status"] == "proven-awaiting-ledger-resolution"


def test_mismatched_thread_identity_fails_closed() -> None:
    with pytest.raises(ValueError, match="identity mismatch"):
        coverage(
            view(),
            pull_requests=[579],
            github_threads={(579, 123): {"pull_request_number": 581, "comment_id": 123, "resolved": True}},
        )


def test_untrusted_resolution_type_fails_closed() -> None:
    with pytest.raises(ValueError, match="boolean"):
        coverage(
            view(),
            pull_requests=[579],
            github_threads={(579, 123): {"pull_request_number": 579, "comment_id": 123, "resolved": "true"}},
        )


def test_empty_scope_refused() -> None:
    with pytest.raises(ValueError, match="positive PR"):
        coverage(view(), pull_requests=[], github_threads={})


def test_historical_assessment_is_visible_but_does_not_reduce_proof_gap() -> None:
    known = view()
    known.findings["f" * 64].historical_assessment = {"disposition": "historical-fix-evidence"}
    report = coverage(
        known,
        pull_requests=[579],
        github_threads={(579, 123): {"pull_request_number": 579, "comment_id": 123, "resolved": True}},
    )
    assert report["historical_assessed"] == 1
    assert report["proof_gap"] == 1
    assert report["findings"][0]["signed_proof"] is False
    assert report["findings"][0]["historical_disposition"] == "historical-fix-evidence"


def test_signed_ledger_proof_remains_authoritative_if_github_thread_disappears() -> None:
    known = view()
    known.findings["f" * 64].proven = {"regression_tests": ["tests/test_x.py::test_fix"]}
    report = coverage(known, pull_requests=[579], github_threads={})
    assert report["findings"][0]["status"] == "proven-awaiting-ledger-resolution"
    assert report["signed_proven"] == 1


def test_live_thread_missing_from_signed_ledger_fails_closed() -> None:
    with pytest.raises(ValueError, match="missing from signed ledger"):
        coverage(
            KnowledgeView(repository_id=1),
            pull_requests=[579],
            github_threads={(579, 123): {"pull_request_number": 579, "comment_id": 123, "resolved": False}},
        )


def test_partially_ingested_pr_cannot_hide_unanchored_review() -> None:
    with pytest.raises(ValueError, match="missing from signed ledger"):
        coverage(
            view(),
            pull_requests=[579],
            github_threads={
                (579, 123): {"pull_request_number": 579, "comment_id": 123, "resolved": True},
                (579, 124): {"pull_request_number": 579, "comment_id": 124, "resolved": False},
            },
        )


def test_unknown_live_thread_cannot_hide_behind_signed_proof() -> None:
    known = view()
    known.findings["f" * 64].proven = {"regression_tests": ["tests/test_x.py::test_fix"]}
    with pytest.raises(ValueError, match="missing from signed ledger"):
        coverage(
            known,
            pull_requests=[579],
            github_threads={(579, 124): {"pull_request_number": 579, "comment_id": 124, "resolved": True}},
        )


def test_unselected_pr_threads_do_not_change_selected_scope() -> None:
    report = coverage(
        view(),
        pull_requests=[579],
        github_threads={(581, 124): {"pull_request_number": 581, "comment_id": 124, "resolved": False}},
    )
    assert report["examined"] == 1
    assert report["proof_gap"] == 1
