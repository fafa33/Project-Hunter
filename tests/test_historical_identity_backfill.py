"""Regression: merged historical review findings must enter the signed knowledge chain."""

from __future__ import annotations

import copy

import hunter_issue_agent_lifecycle as lifecycle
import pytest

from hunter.automation import issue_agent_knowledge as knowledge

PR = 579
COMMENT = 4222875872
ORIGINAL = "7bc7e3d1cdb6adddf44fcb26b77b3eb05e9ffcc9"
REPOSITORY_ID = 1292945327


def inputs():
    comments = [{"id": COMMENT, "original_commit_id": ORIGINAL, "commit_id": ORIGINAL, "path": "src/x.py"}]
    observations = [
        {
            "event_id": f"review-comment-{COMMENT}",
            "source_pr": PR,
            "path": "src/x.py",
            "reviewed_head_sha": "f" * 40,
            "reviewed_base_sha": "a" * 40,
            "source_event_head_sha": ORIGINAL,
            "provider": "github-review",
            "reviewer": "copilot-pull-request-reviewer[bot]",
            "line": 12,
            "message": "Historical authenticated finding must be learned.",
        }
    ]
    expected = {COMMENT: {"reviewed_head": ORIGINAL}}
    return comments, observations, expected


def backfill(view=None, *, comments=None, observations=None, expected=None):
    base_comments, base_observations, base_expected = inputs()
    return lifecycle._historical_ingestion_writes(
        view=view or knowledge.KnowledgeView(repository_id=REPOSITORY_ID),
        original_comments=base_comments if comments is None else comments,
        observations=base_observations if observations is None else observations,
        expected_findings=base_expected if expected is None else expected,
        number=PR,
        repository_id=REPOSITORY_ID,
    )


def test_missing_copilot_finding_is_ingested_with_original_review_sha():
    writes = backfill()
    assert len(writes) == 1
    assert writes[0].kind == "finding_ingested"
    assert writes[0].evidence["provenance"]["reviewed_head_sha"] == ORIGINAL
    assert writes[0].evidence["finding_id"] == knowledge.finding_id(REPOSITORY_ID, PR, COMMENT)


def test_existing_legacy_final_head_identity_is_not_rewritten():
    writes = backfill()
    evidence = copy.deepcopy(writes[0].evidence)
    evidence["provenance"]["reviewed_head_sha"] = "f" * 40
    view = knowledge.KnowledgeView(repository_id=REPOSITORY_ID)
    view.findings[evidence["finding_id"]] = knowledge.Finding(evidence)
    assert backfill(view) == []


@pytest.mark.parametrize("change", ["missing", "unexpected", "stale", "path", "observation"])
def test_missing_or_spoofed_historical_source_fails_closed(change):
    comments, observations, expected = inputs()
    if change == "missing":
        comments.clear()
    elif change == "unexpected":
        comments.append({"id": 999, "original_commit_id": ORIGINAL, "path": "src/x.py"})
    elif change == "stale":
        comments[0]["original_commit_id"] = "f" * 40
    elif change == "path":
        comments[0]["path"] = "src/other.py"
    else:
        observations.clear()
    with pytest.raises(lifecycle.LifecycleRefused, match="COVERAGE_GAP|STALE_HISTORICAL_SOURCE"):
        backfill(comments=comments, observations=observations, expected=expected)
