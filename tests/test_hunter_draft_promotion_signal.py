import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure scripts directory is in PYTHONPATH so we can import hunter_draft_promotion_signal
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

import hunter_draft_promotion_signal


class MockGitHubServer:
    def __init__(self):
        self.pulls = {}
        self.check_runs = {}
        self.statuses = {}
        self.comments = {}
        self.published = []
        self.patched_bodies = {}

    def request_json(self, method, path, payload=None):
        clean_path = path.split("?")[0]

        if method == "POST":
            if clean_path.startswith("statuses/"):
                sha = clean_path.split("/")[-1]
                self.published.append((sha, payload["state"], payload["description"]))
                return {}
            if clean_path.startswith("issues/") and clean_path.endswith("/comments"):
                pr_num = int(clean_path.split("/")[1])
                self.comments.setdefault(pr_num, []).append(
                    {"id": len(self.comments.get(pr_num, [])) + 1, "body": payload["body"]}
                )
                return {}
            return {}

        if method == "PATCH":
            if clean_path.startswith("pulls/"):
                pr_num = int(clean_path.split("/")[1])
                self.patched_bodies[pr_num] = payload["body"]
                if pr_num in self.pulls:
                    self.pulls[pr_num]["body"] = payload["body"]
                return {}

        # GET requests
        if clean_path.startswith("pulls/"):
            pr_num = int(clean_path.split("/")[1])
            return self.pulls.get(pr_num, {})

        if clean_path.startswith("commits/"):
            parts = clean_path.split("/")
            sha = parts[1]
            sub_resource = parts[2]
            if sub_resource == "check-runs":
                return {"check_runs": self.check_runs.get(sha, [])}
            if sub_resource == "status":
                return {"statuses": self.statuses.get(sha, [])}

        if clean_path.startswith("issues/") and clean_path.endswith("/comments"):
            pr_num = int(clean_path.split("/")[1])
            return self.comments.get(pr_num, [])

        if clean_path == "pulls":
            return list(self.pulls.values())

        return {}


@pytest.fixture
def gh():
    server = MockGitHubServer()
    hunter_draft_promotion_signal.repo = "fafa33/Project-Hunter"
    hunter_draft_promotion_signal.token = "fake-token"
    hunter_draft_promotion_signal.run_url = "https://github.com/fafa33/Project-Hunter/actions/runs/1"

    with (
        patch("hunter_draft_promotion_signal.request_json", side_effect=server.request_json),
        patch("hunter_draft_promotion_signal.review_feedback_blockers", return_value=[]),
    ):
        yield server


def green_pr(number, sha, body, draft=True):
    return {"number": number, "draft": draft, "head": {"sha": sha}, "body": body}


def green_checks(gh, sha):
    gh.check_runs[sha] = [
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 1},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 2},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 3},
    ]
    gh.statuses[sha] = [{"context": "Hunter Governance Review", "state": "success", "id": 1}]


# ====================================================================================
# parse_readiness_declaration: the silent-crash regression case and fail-closed guards
# ====================================================================================


def test_single_declaration_line_does_not_crash():
    """Regression: a PR body carrying only the one checked declaration line (no
    unchecked CHANGES REQUIRED/BLOCKED lines) must parse successfully instead of
    raising RuntimeError. This is the exact shape that previously crashed the
    workflow silently (no status update, no comment, no error surfaced to the PR).
    """
    body = "## Implementer readiness declaration\n\n- [x] `READY FOR REVIEW` — all good.\n"
    matches, checked_label = hunter_draft_promotion_signal.parse_readiness_declaration(body)
    assert len(matches) == 1
    assert checked_label == "READY FOR REVIEW"


def test_all_three_lines_one_checked_still_works():
    """The original, template-shaped body (all three labels present, one checked)
    must continue to work exactly as before.
    """
    body = "- [ ] `READY FOR REVIEW` — a\n" "- [x] `CHANGES REQUIRED` — b\n" "- [ ] `BLOCKED` — c\n"
    matches, checked_label = hunter_draft_promotion_signal.parse_readiness_declaration(body)
    assert len(matches) == 3
    assert checked_label == "CHANGES REQUIRED"


def test_no_declaration_lines_fails_closed():
    with pytest.raises(RuntimeError, match="No implementer readiness declaration found"):
        hunter_draft_promotion_signal.parse_readiness_declaration("Just a description, no checkboxes.")


def test_zero_checked_fails_closed():
    body = "- [ ] `READY FOR REVIEW` — a\n- [ ] `CHANGES REQUIRED` — b\n"
    with pytest.raises(RuntimeError, match="missing, ambiguous, or unparseable"):
        hunter_draft_promotion_signal.parse_readiness_declaration(body)


def test_multiple_checked_fails_closed():
    body = "- [x] `READY FOR REVIEW` — a\n- [x] `BLOCKED` — b\n"
    with pytest.raises(RuntimeError, match="missing, ambiguous, or unparseable"):
        hunter_draft_promotion_signal.parse_readiness_declaration(body)


def test_duplicated_label_fails_closed():
    body = "- [x] `READY FOR REVIEW` — a\n- [ ] `READY FOR REVIEW` — duplicate\n"
    with pytest.raises(RuntimeError, match="duplicated label"):
        hunter_draft_promotion_signal.parse_readiness_declaration(body)


# ====================================================================================
# synchronize_ready_metadata: no-op vs. rewrite vs. fail-closed (no partial mutation)
# ====================================================================================


def test_synchronize_single_line_ready_is_a_noop(gh):
    """The crash-case body is already synchronized (READY FOR REVIEW is checked),
    so no PATCH should be issued.
    """
    body = "- [x] `READY FOR REVIEW` — all good.\n"
    hunter_draft_promotion_signal.synchronize_ready_metadata(123, body)
    assert 123 not in gh.patched_bodies


def test_synchronize_rewrites_to_ready_for_review(gh):
    body = "- [ ] `READY FOR REVIEW` — a\n- [x] `CHANGES REQUIRED` — b\n- [ ] `BLOCKED` — c\n"
    hunter_draft_promotion_signal.synchronize_ready_metadata(123, body)
    assert 123 in gh.patched_bodies
    assert "[x] `READY FOR REVIEW`" in gh.patched_bodies[123]
    assert "[ ] `CHANGES REQUIRED`" in gh.patched_bodies[123]


def test_synchronize_without_ready_label_fails_closed_and_does_not_patch(gh):
    body = "- [x] `CHANGES REQUIRED` — more work remains.\n"
    with pytest.raises(RuntimeError, match="READY FOR REVIEW declaration is missing"):
        hunter_draft_promotion_signal.synchronize_ready_metadata(123, body)
    assert 123 not in gh.patched_bodies


def test_synchronize_ambiguous_body_raises_and_does_not_patch(gh):
    body = "- [ ] `READY FOR REVIEW` — a\n- [ ] `CHANGES REQUIRED` — b\n"
    with pytest.raises(RuntimeError):
        hunter_draft_promotion_signal.synchronize_ready_metadata(123, body)
    assert 123 not in gh.patched_bodies


# ====================================================================================
# evaluate(): end-to-end regression — promotion must include live review feedback
# ====================================================================================


def test_evaluate_with_single_declaration_line_reaches_success_and_comments(gh):
    """End-to-end regression for the exact scenario observed on PR #243: a Draft
    PR with every exact-head check green and a body carrying only the single
    checked READY FOR REVIEW line must reach a published success status and post
    the promotion comment, instead of the job crashing with an unhandled
    RuntimeError and leaving the PR with no signal at all.
    """
    sha = "sha_123"
    body = "## Implementer readiness declaration\n\n- [x] `READY FOR REVIEW` — all good.\n"
    gh.pulls[123] = green_pr(123, sha, body)
    green_checks(gh, sha)

    hunter_draft_promotion_signal.evaluate(gh.pulls[123])

    assert gh.published[-1][1] == "success"
    assert "Ready to promote from Draft" in gh.published[-1][2]
    assert len(gh.comments.get(123, [])) == 1
    assert "Hunter Draft Promotion" in gh.comments[123][0]["body"]


def test_evaluate_with_ambiguous_declaration_does_not_silently_disappear(gh):
    """When the declaration is genuinely unparseable, evaluate() must still raise
    rather than silently doing nothing — the caller (main()) is responsible for
    surfacing this as a failed run, which is at least visible in Actions, unlike
    a successful-looking no-op.
    """
    sha = "sha_123"
    body = "- [ ] `READY FOR REVIEW` — a\n- [ ] `CHANGES REQUIRED` — b\n"
    gh.pulls[123] = green_pr(123, sha, body)
    green_checks(gh, sha)

    with pytest.raises(RuntimeError):
        hunter_draft_promotion_signal.evaluate(gh.pulls[123])

    assert not any(state == "success" for _sha, state, _desc in gh.published)


def test_unresolved_review_thread_blocks_promotion_and_metadata_mutation(gh):
    sha = "sha_review_blocked"
    body = "- [ ] `READY FOR REVIEW` — pending review.\n- [x] `CHANGES REQUIRED` — review feedback open.\n"
    gh.pulls[275] = green_pr(275, sha, body)
    green_checks(gh, sha)

    with patch(
        "hunter_draft_promotion_signal.review_feedback_blockers",
        return_value=["unresolved review threads=1"],
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[275])

    assert gh.published[-1][1] == "pending"
    assert "unresolved review threads=1" in gh.published[-1][2]
    assert 275 not in gh.patched_bodies
    assert not gh.comments.get(275)


def test_changes_requested_review_blocks_promotion(gh):
    sha = "sha_changes_requested"
    body = "- [ ] `READY FOR REVIEW` — pending review.\n- [x] `CHANGES REQUIRED` — review feedback open.\n"
    gh.pulls[276] = green_pr(276, sha, body)
    green_checks(gh, sha)

    with patch(
        "hunter_draft_promotion_signal.review_feedback_blockers",
        return_value=["changes requested by reviewer"],
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[276])

    assert gh.published[-1][1] == "pending"
    assert "changes requested by reviewer" in gh.published[-1][2]
    assert 276 not in gh.patched_bodies


def test_review_feedback_is_rechecked_before_metadata_mutation(gh):
    sha = "sha_review_race"
    body = "- [ ] `READY FOR REVIEW` — pending review.\n- [x] `CHANGES REQUIRED` — waiting.\n"
    gh.pulls[277] = green_pr(277, sha, body)
    green_checks(gh, sha)

    with patch(
        "hunter_draft_promotion_signal.review_feedback_blockers",
        side_effect=[[], ["unresolved review threads=1"]],
    ):
        hunter_draft_promotion_signal.evaluate(gh.pulls[277])

    assert gh.published[-1][1] == "pending"
    assert 277 not in gh.patched_bodies
    assert not gh.comments.get(277)


def test_review_feedback_blockers_include_unacknowledged_top_level_comments():
    hunter_draft_promotion_signal.repo = "fafa33/Project-Hunter"
    hunter_draft_promotion_signal.token = "fake-token"
    with (
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "unresolved_review_thread_ids",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "current_changes_requested_reviewers",
            return_value=(),
        ),
        patch.object(
            hunter_draft_promotion_signal.merge_readiness,
            "unacknowledged_top_level_comments",
            return_value=(101, 102),
        ),
    ):
        blockers = hunter_draft_promotion_signal.review_feedback_blockers(275)

    assert blockers == ["unacknowledged top-level comments=2"]


def test_workflow_reconciles_when_review_feedback_changes():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-draft-promotion-signal.yml"
    ).read_text(encoding="utf-8")
    assert "pull_request_review:" in workflow
    assert "pull_request_review_comment:" in workflow
    assert "issue_comment:" in workflow
