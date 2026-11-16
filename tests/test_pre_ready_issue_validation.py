"""A stale review request cannot borrow another PR's governing Issue."""

import json
from types import SimpleNamespace

import hunter_pre_ready_review as review
import pytest


@pytest.mark.parametrize(
    "payload,problem",
    [
        ({"number": 603, "pull_request": {}, "body": "## Acceptance Criteria\n- One"}, "pull request"),
        ({"number": 603, "body": "## Acceptance Criteria\n- Required proof"}, "does not cover"),
    ],
)
def test_review_request_rejects_wrong_governing_issue(monkeypatch, payload, problem):
    def fake_run(command, **kwargs):
        assert command[-1].endswith("/issues/603")
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    monkeypatch.setattr(review.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match=problem):
        review.validate_request_governing_issue("603", {"acceptance_criteria": []})


def test_review_request_accepts_verified_issue_criteria(monkeypatch):
    monkeypatch.setattr(
        review.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout=json.dumps({"number": 603, "body": "## Acceptance Criteria\n- Required proof"})
        ),
    )
    review.validate_request_governing_issue("603", {"acceptance_criteria": [{"criterion": "Required proof"}]})


def test_issue_without_criteria_section_imposes_no_coverage_obligation(monkeypatch):
    """The truthful reading, identical to the hosted verifier: no section, no obligation.

    The request still binds to the verified Issue identity; this is the same
    reading `read_issue_acceptance_criteria` feeds the trusted controller, so a
    writer can mint for a governing Issue that words its acceptance section
    differently without weakening anything admission will measure.
    """

    monkeypatch.setattr(
        review.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"number": 585, "body": "## Acceptance\nFix proceeds without manual rebuilding."}),
        ),
    )
    assert review.validate_request_governing_issue("585", {"acceptance_criteria": []}) == ()
