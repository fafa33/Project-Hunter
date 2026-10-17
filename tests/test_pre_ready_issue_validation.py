"""A stale review request cannot borrow another PR's governing Issue."""

import json
from types import SimpleNamespace

import hunter_pre_ready_review as review
import pytest


@pytest.mark.parametrize(
    "payload,problem",
    [
        ({"number": 603, "pull_request": {}, "body": "## Acceptance Criteria\n- One"}, "pull request"),
        ({"number": 603, "body": "No acceptance section"}, "no Acceptance Criteria"),
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
