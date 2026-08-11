import os
import sys
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

# Ensure scripts directory is in PYTHONPATH so we can import hunter_merge_readiness
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

import hunter_merge_readiness


class MockGitHubServer:
    def __init__(self):
        self.pulls = {}
        self.check_runs = {}
        self.statuses = {}
        self.comments = {}
        self.reviews = {}
        self.reactions = {}
        self.review_comments = {}
        self.published = []
        self.gql_threads = 0
        self.api_calls = []  # Log order of all API calls to assert sequencing

    def request_json(self, method, path, payload=None):
        clean_path = path.split("?")[0]
        self.api_calls.append((method, clean_path, payload))

        if method == "POST":
            if clean_path.startswith("statuses/"):
                sha = clean_path.split("/")[-1]
                self.published.append((sha, payload["state"], payload["description"]))
                if sha not in self.statuses:
                    self.statuses[sha] = []
                self.statuses[sha].append(
                    {
                        "context": payload["context"],
                        "state": payload["state"],
                        "description": payload["description"],
                        "id": len(self.statuses[sha]) + 1,
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )
                return {}
            return {}

        # GET requests
        if clean_path.startswith("pulls/"):
            parts = clean_path.split("/")
            pr_num = int(parts[1])
            if len(parts) > 2:
                sub_resource = parts[2]
                if sub_resource == "files":
                    return []
                if sub_resource == "reviews":
                    return self.reviews.get(pr_num, [])
                if sub_resource == "comments":
                    return self.review_comments.get(pr_num, [])
                return []
            return self.pulls.get(pr_num, {})

        if clean_path.startswith("commits/"):
            parts = clean_path.split("/")
            sha = parts[1]
            sub_resource = parts[2]
            if sub_resource == "statuses":
                return self.statuses.get(sha, [])
            if sub_resource == "check-runs":
                return {"check_runs": self.check_runs.get(sha, [])}

        if clean_path.startswith("issues/"):
            parts = clean_path.split("/")
            # e.g., issues/comments/comment_id/reactions
            if parts[1] == "comments" and len(parts) > 3 and parts[3] == "reactions":
                comment_id = int(parts[2])
                return self.reactions.get(comment_id, [])
            # e.g., issues/123/comments
            elif len(parts) > 2 and parts[2] == "comments":
                pr_num = int(parts[1])
                return self.comments.get(pr_num, [])

        if clean_path == "pulls":
            return list(self.pulls.values())

        return {}

    def graphql_json(self, query, variables):
        self.api_calls.append(("POST", "graphql", variables))
        return {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [{"isResolved": False}] * self.gql_threads,
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }


@pytest.fixture
def gh():
    server = MockGitHubServer()
    # Reset global configuration and state in hunter_merge_readiness
    hunter_merge_readiness.repo = "fafa33/Project-Hunter"
    hunter_merge_readiness.repo_owner = "fafa33"
    hunter_merge_readiness.token = "fake-token"
    hunter_merge_readiness.event_name = "schedule"
    hunter_merge_readiness.run_url = "https://github.com/fafa33/Project-Hunter/actions/runs/1"
    hunter_merge_readiness.active_sha = None
    hunter_merge_readiness.latest_readiness = None

    with (
        patch("hunter_merge_readiness.request_json", side_effect=server.request_json),
        patch("hunter_merge_readiness.graphql_json", side_effect=server.graphql_json),
    ):
        yield server


def test_stale_pending_after_governance_success(gh):
    """Test A: stale pending after governance success:
    - readiness = pending
    - governance = success
    - required checks = success
    - PR not Draft
    - schedule run
    - expected readiness = success
    """
    hunter_merge_readiness.event_name = "schedule"

    # Setup PR with updated_at matching/pre-dating governance run to ensure freshness passes
    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    # Setup completed governance run and required checks
    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    # Setup existing statuses on the commit (stale pending readiness status)
    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting...", "id": 2},
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    # Check final published status is success
    assert len(gh.published) > 0
    assert gh.published[-1][1] == "success"
    assert "Ready to merge" in gh.published[-1][2]


def test_stale_pending_with_required_check_still_pending(gh):
    """Test B: stale pending with required check still pending:
    - schedule re-evaluates
    - remains bounded pending
    - later reconciliation can succeed
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    # Setup checks with CodeQL still in progress
    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "in_progress", "id": 103},
    ]

    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting...", "id": 2},
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    # Remains pending
    assert len(gh.published) > 0
    assert gh.published[-1][1] == "pending"
    assert "Waiting for exact-head checks" in gh.published[-1][2]
    assert "CodeQL" in gh.published[-1][2]


def test_stale_pending_with_failed_prerequisite(gh):
    """Test C: stale pending with failed prerequisite:
    - schedule publishes failure
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    # Setup checks with Quality Gates failed
    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "failure", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting...", "id": 2},
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    # Publishes failure
    assert len(gh.published) > 0
    assert gh.published[-1][1] == "failure"
    assert "prerequisite failed: Quality Gates=failure" in gh.published[-1][2]


def test_draft_pr_behavior(gh):
    """Test D: Draft PR:
    - deterministic waiting-for-ready state
    - no false success
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Empty body",
        "draft": True,  # Draft!
        "user": {"login": "human"},
    }

    hunter_merge_readiness.evaluate(123, poll=False)

    # Publishes waiting for Ready for Review
    assert len(gh.published) == 1
    assert gh.published[0][1] == "pending"
    assert gh.published[0][2] == "Waiting for Ready for Review (PR is Draft)."


def test_draft_to_ready_transition(gh):
    """Test E: Draft -> Ready transition:
    - fresh evaluation occurs
    - no stale Draft pending remains authoritative
    """
    # Transition to ready_for_review triggers pull_request_target event
    hunter_merge_readiness.event_name = "pull_request_target"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,  # Now NOT Draft!
        "user": {"login": "human"},
    }

    # Previous status on the commit was the Draft pending status
    gh.statuses["sha_123"] = [
        {
            "context": "Hunter Merge Readiness",
            "state": "pending",
            "description": "Waiting for Ready for Review (PR is Draft).",
            "id": 1,
        }
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    # Fresh evaluation runs. Since it is PR trigger, it should publish pending "Waiting for current Hunter Governance Review"
    assert len(gh.published) > 0
    assert gh.published[-1][1] == "pending"
    assert gh.published[-1][2] == "Waiting for current Hunter Governance Review."


def test_feedback_failure(gh):
    """Test F: feedback failure:
    - existing feedback reconciliation still works
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
    }

    # Set 1 unresolved thread in GraphQL
    gh.gql_threads = 1

    gh.statuses["sha_123"] = [
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting...", "id": 1}
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    assert len(gh.published) > 0
    assert gh.published[-1][1] == "failure"
    assert "Unresolved review threads remain: 1." in gh.published[-1][2]


def test_missing_readiness_status(gh):
    """Test G: missing readiness status:
    - schedule initializes/reconciles it
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    # No existing readiness status in gh.statuses["sha_123"]
    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    # Correctly initializes and publishes success
    assert len(gh.published) > 0
    assert gh.published[-1][1] == "success"
    assert "Ready to merge" in gh.published[-1][2]


def test_stale_governance_result(gh):
    """Test H: stale governance result:
    - must NOT succeed
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    # Governance status was created before check run start time (00:40:00Z vs 00:50:00Z)
    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T00:40:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting...", "id": 2},
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    # Must wait for fresh evaluation
    assert len(gh.published) > 0
    assert gh.published[-1][1] == "pending"
    assert "Hunter Governance Review (fresh evaluation)" in gh.published[-1][2]


def test_governance_for_old_head_sha(gh, capsys):
    """Test I: governance for old head SHA:
    - must NOT satisfy current head
    """
    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "current_head_sha"},
        "draft": False,
    }

    # Trigger head SHA does not match current_head_sha
    hunter_merge_readiness.evaluate(123, trigger_head_sha="old_head_sha", poll=False)

    # No statuses should be published
    assert len(gh.published) == 0
    captured = capsys.readouterr()
    assert "Ignoring stale Governance Review completion" in captured.out


def test_cancelled_or_interrupted_prior_controller(gh):
    """Test J: cancelled/interrupted prior controller:
    - schedule recovers current head state
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        # Previous run got cancelled/interrupted
        {
            "context": "Hunter Merge Readiness",
            "state": "pending",
            "description": "Validating governance, every review thread/comment, and exact-head checks.",
            "id": 2,
        },
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    # Transitioned to success
    assert len(gh.published) > 0
    assert gh.published[-1][1] == "success"
    assert "Ready to merge" in gh.published[-1][2]


def test_no_endless_pending_invariant(gh):
    """Test K: no endless pending invariant:
    - simulate all controller paths and assert every non-Draft state has a deterministic recovery or terminal path
    """
    # Sub-test 1: Webhook trigger with no governance started at
    # Should transition to pending "Waiting for current Hunter Governance Review"
    hunter_merge_readiness.event_name = "pull_request"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    gh.statuses["sha_123"] = [
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting...", "id": 1}
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    assert len(gh.published) > 0
    assert gh.published[-1][1] == "pending"
    assert gh.published[-1][2] == "Waiting for current Hunter Governance Review."

    # Sub-test 2: Metadata error
    # Should transition to terminal failure
    gh.published = []
    gh.pulls[123]["body"] = "Invalid body"

    hunter_merge_readiness.evaluate(123, poll=False)

    assert len(gh.published) > 0
    assert gh.published[-1][1] == "failure"
    assert "Acceptance-criteria matrix is missing" in gh.published[-1][2]


# ====================================================================================
# P1 REGRESSION TESTS (L - P)
# ====================================================================================


def test_p1_1_same_head_pr_edit_invalidates_old_governance_success(gh):
    """Test L: same-head PR edit invalidates old governance success.
    - HEAD SHA is unchanged.
    - An old successful Governance Review run exists (started at 00:50:00Z).
    - PR is edited/updated afterward (updated_at at 01:10:00Z).
    - Scheduled reconciliation runs.
    - Expected: rejects stale governance, readiness remains recoverably pending.
    """
    hunter_merge_readiness.event_name = "schedule"

    # PR edited/updated_at is 01:10:00Z (after governance started!)
    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T01:10:00Z",
    }

    # Governance check run started at 00:50:00Z (stale!)
    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting...", "id": 2},
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    # Must be pending, waiting for a fresh run
    assert len(gh.published) > 0
    assert gh.published[-1][1] == "pending"
    assert "Waiting for a fresh Hunter Governance Review" in gh.published[-1][2]


def test_p1_1_fresh_same_head_governance_after_invalidation_is_accepted(gh):
    """Test M: fresh same-head governance after invalidation is accepted.
    - same HEAD SHA.
    - invalidation/PR update occurs at 01:10:00Z.
    - a successful Governance Review run starts at 01:20:00Z (fresh!).
    - Expected: readiness transitions to success.
    """
    hunter_merge_readiness.event_name = "schedule"

    # PR edited at 01:10:00Z
    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T01:10:00Z",
    }

    # Governance check run started at 01:20:00Z (fresh!)
    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T01:20:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    # Setup a fresh governance status check too
    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:25:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting...", "id": 2},
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    # Succeeds cleanly!
    assert len(gh.published) > 0
    assert gh.published[-1][1] == "success"
    assert "Ready to merge" in gh.published[-1][2]


def test_p1_2_green_status_is_invalidated_before_feedback_queries(gh):
    """Test N: green status is invalidated before feedback queries.
    - existing readiness is success (green).
    - a webhook event occurs (event_name = "pull_request").
    - Expected: pending status is published BEFORE any potentially lengthy feedback queries are performed.
    """
    hunter_merge_readiness.event_name = "pull_request"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
    }

    # PR is currently green (success)
    gh.statuses["sha_123"] = [
        {"context": "Hunter Merge Readiness", "state": "success", "description": "Ready to merge...", "id": 1}
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    # Let's inspect the order of API calls
    # The first POST to create/update status (statuses/sha_123) MUST happen BEFORE
    # any GET requests for comments, reviews, or GraphQL POST queries!
    first_status_post_index = None
    first_feedback_query_index = None

    for idx, (method, clean_path, payload) in enumerate(gh.api_calls):
        if method == "POST" and clean_path == "statuses/sha_123" and payload and payload.get("state") == "pending":
            if first_status_post_index is None:
                first_status_post_index = idx
        if "comments" in clean_path or "reviews" in clean_path or clean_path == "graphql":
            if first_feedback_query_index is None:
                first_feedback_query_index = idx

    assert first_status_post_index is not None
    assert first_feedback_query_index is not None
    assert first_status_post_index < first_feedback_query_index, (
        f"Pending publish (index {first_status_post_index}) must precede "
        f"feedback queries (index {first_feedback_query_index})"
    )


def test_p1_2_cancellation_after_early_invalidation(gh):
    """Test O: cancellation after early invalidation.
    - existing readiness is success.
    - webhook event occurs, starts execution.
    - pending is immediately published.
    - execution is interrupted/cancelled (simulated by raising an exception during feedback query).
    - Expected: the previous green is immediately invalidated on GitHub, and remains pending/recoverable.
    - Scheduled reconciliation can later produce success when run succeeds.
    """
    # 1. Start with green PR
    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }
    gh.statuses["sha_123"] = [
        {"context": "Hunter Merge Readiness", "state": "success", "description": "Ready to merge...", "id": 1}
    ]

    # 2. Simulate event and cancellation (raise an exception when GraphQL/paged query begins)
    hunter_merge_readiness.event_name = "pull_request"

    def mock_paged(path):
        if "statuses" in path:
            return gh.request_json("GET", path)
        raise RuntimeError("Simulated workflow cancellation / query failure during lengthy feedback query")

    with patch("hunter_merge_readiness.paged", side_effect=mock_paged):
        try:
            hunter_merge_readiness.evaluate(123, poll=False)
        except RuntimeError:
            pass

    # Assert that "pending" was published before the exception cut off the execution
    assert len(gh.published) > 0
    assert gh.published[0][1] == "pending"

    # Previous "success" is no longer the latest status
    latest = gh.statuses["sha_123"][-1]
    assert latest["state"] == "pending"

    # 3. Simulate scheduled recovery (which has no exception)
    hunter_merge_readiness.event_name = "schedule"
    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]
    gh.statuses["sha_123"].append(
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 10}
    )

    hunter_merge_readiness.evaluate(123, poll=False)

    # Recovers to success!
    assert gh.published[-1][1] == "success"
    assert "Ready to merge" in gh.published[-1][2]


def test_p1_1_stale_governance_and_stale_pending_recovery(gh):
    """Test P: stale governance + stale pending recovery.
    - Scheduler runs.
    - Latest readiness is pending "Waiting for a fresh Hunter Governance Review...".
    - Governance run is stale (starts before PR updated_at).
    - Expected: scheduler must NOT publish success; remains pending.
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T01:10:00Z",  # PR edited/invalidated
    }

    # Stale governance starts at 00:50:00Z
    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {
            "context": "Hunter Merge Readiness",
            "state": "pending",
            "description": "Waiting for a fresh Hunter Governance Review...",
            "id": 2,
        },
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    # Must remain pending (not succeed!)
    assert gh.published[-1][1] == "pending"
    assert "Waiting for a fresh Hunter Governance Review" in gh.published[-1][2]


# ====================================================================================
# OWNER ACKNOWLEDGMENT REACTION REGRESSION TESTS (A - H)
# ====================================================================================


def test_owner_ack_reaction_does_not_invalidate_fresh_governance(gh):
    """Test A: an owner +1 acknowledgment reaction posted after a successful,
    fresh Hunter Governance Review must NOT invalidate that governance result,
    must resolve the comment-acknowledgment blocker, and readiness must reach
    success without requiring a new governance run.
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    gh.comments[123] = [
        {
            "id": 456,
            "body": "Needs owner acknowledgment",
            "created_at": "2026-08-05T00:35:00Z",
            "updated_at": "2026-08-05T00:35:00Z",
        }
    ]

    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting for owner +1", "id": 2},
    ]

    # Owner acknowledges after governance ran (01:10:00Z is after the 01:00:00Z governance status)
    gh.reactions[456] = [
        {
            "user": {"login": "fafa33"},
            "content": "+1",
            "created_at": "2026-08-05T01:10:00Z",
        }
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    # Success must be produced immediately without needing a new governance run.
    assert len(gh.published) > 0
    assert gh.published[-1][1] == "success"
    assert "Ready to merge" in gh.published[-1][2]


def test_existing_successful_governance_remains_fresh_without_reactions(gh):
    """Test B: with no comments/reactions at all, an existing fresh, successful
    governance run continues to be honored (baseline, unaffected by the fix).
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting...", "id": 2},
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    assert len(gh.published) > 0
    assert gh.published[-1][1] == "success"
    assert "Ready to merge" in gh.published[-1][2]


def test_unacknowledged_comment_without_reaction_still_blocks(gh):
    """Test C: a top-level comment with no owner +1 reaction remains an
    acknowledgment blocker (governance stays fresh, but readiness still fails).
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    gh.comments[123] = [
        {
            "id": 456,
            "body": "Needs owner acknowledgment",
            "created_at": "2026-08-05T00:35:00Z",
            "updated_at": "2026-08-05T00:35:00Z",
        }
    ]

    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting for owner +1", "id": 2},
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    assert len(gh.published) > 0
    assert gh.published[-1][1] == "failure"
    assert "Unacknowledged PR comments need owner" in gh.published[-1][2]


def test_non_owner_reaction_does_not_satisfy_acknowledgment(gh):
    """Test D: a +1 reaction from someone other than the repo owner does not
    satisfy the acknowledgment blocker, even though it also must not invalidate
    an otherwise-fresh governance run.
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    gh.comments[123] = [
        {
            "id": 456,
            "body": "Needs owner acknowledgment",
            "created_at": "2026-08-05T00:35:00Z",
            "updated_at": "2026-08-05T00:35:00Z",
        }
    ]

    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting for owner +1", "id": 2},
    ]

    # A non-owner reacts +1 on the comment.
    gh.reactions[456] = [
        {
            "user": {"login": "attacker"},
            "content": "+1",
            "created_at": "2026-08-05T01:10:00Z",
        }
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    assert len(gh.published) > 0
    assert gh.published[-1][1] == "failure"
    assert "Unacknowledged PR comments need owner" in gh.published[-1][2]


def test_pr_body_edit_after_owner_ack_still_invalidates_governance(gh):
    """Test E: a PR body edit occurring after governance ran must still
    invalidate the old governance result, even when an owner +1 acknowledgment
    reaction is also present on a top-level comment.
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        # PR edited after the governance status was created (01:00:00Z).
        "updated_at": "2026-08-05T01:15:00Z",
    }

    gh.comments[123] = [
        {
            "id": 456,
            "body": "Needs owner acknowledgment",
            "created_at": "2026-08-05T00:35:00Z",
            "updated_at": "2026-08-05T00:35:00Z",
        }
    ]

    gh.reactions[456] = [
        {
            "user": {"login": "fafa33"},
            "content": "+1",
            "created_at": "2026-08-05T01:10:00Z",
        }
    ]

    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting for owner +1", "id": 2},
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    assert len(gh.published) > 0
    assert gh.published[-1][1] == "pending"
    assert "Waiting for a fresh Hunter Governance Review" in gh.published[-1][2]


def test_head_sha_change_after_owner_ack_still_invalidates_governance(gh):
    """Test F: a head SHA change must still invalidate the prior governance
    result and require a fresh evaluation on the new SHA, regardless of any
    owner acknowledgment reaction recorded against the old SHA's comments.
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_new"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    gh.comments[123] = [
        {
            "id": 456,
            "body": "Needs owner acknowledgment",
            "created_at": "2026-08-05T00:35:00Z",
            "updated_at": "2026-08-05T00:35:00Z",
        }
    ]

    gh.reactions[456] = [
        {
            "user": {"login": "fafa33"},
            "content": "+1",
            "created_at": "2026-08-05T01:10:00Z",
        }
    ]

    # Governance results only exist for the old SHA; the new SHA has none yet.
    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
    ]
    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
    ]
    gh.check_runs["sha_new"] = []
    gh.statuses["sha_new"] = []

    hunter_merge_readiness.evaluate(123, poll=False)

    assert len(gh.published) > 0
    assert gh.published[-1][1] == "pending"
    assert "Waiting for current Hunter Governance Review" in gh.published[-1][2]


def test_review_thread_comment_after_owner_ack_still_invalidates_governance(gh):
    """Test G: review/review-state invalidation behavior is unchanged — a new
    review (pull request review) thread comment submitted after governance ran
    must still invalidate the prior governance result, even in the presence of
    an owner +1 acknowledgment reaction on an unrelated top-level comment.
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    gh.comments[123] = [
        {
            "id": 456,
            "body": "Needs owner acknowledgment",
            "created_at": "2026-08-05T00:35:00Z",
            "updated_at": "2026-08-05T00:35:00Z",
        }
    ]

    gh.reactions[456] = [
        {
            "user": {"login": "fafa33"},
            "content": "+1",
            "created_at": "2026-08-05T01:10:00Z",
        }
    ]

    # A review (thread) comment submitted after governance ran (01:00:00Z).
    gh.review_comments[123] = [
        {
            "id": 789,
            "body": "New thread comment",
            "created_at": "2026-08-05T01:15:00Z",
            "updated_at": "2026-08-05T01:15:00Z",
        }
    ]

    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting for owner +1", "id": 2},
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    assert len(gh.published) > 0
    assert gh.published[-1][1] == "pending"
    assert "Waiting for a fresh Hunter Governance Review" in gh.published[-1][2]


def test_scheduled_reconciliation_converges_on_owner_ack_without_fresh_governance(gh):
    """Test H: scheduled reconciliation converges to success purely because the
    owner posted a +1 acknowledgment reaction, without requiring — or
    triggering — a new Hunter Governance Review run.
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    gh.comments[123] = [
        {
            "id": 456,
            "body": "Needs owner acknowledgment",
            "created_at": "2026-08-05T00:35:00Z",
            "updated_at": "2026-08-05T00:35:00Z",
        }
    ]

    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-05T00:50:00Z",
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]

    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting for owner +1", "id": 2},
    ]

    # First scheduled reconciliation: comment is still unacknowledged.
    hunter_merge_readiness.evaluate(123, poll=False)
    assert gh.published[-1][1] == "failure"
    assert "Unacknowledged PR comments need owner" in gh.published[-1][2]

    # Owner posts the acknowledgment reaction; nothing else changes.
    gh.reactions[456] = [
        {
            "user": {"login": "fafa33"},
            "content": "+1",
            "created_at": "2026-08-05T01:10:00Z",
        }
    ]

    # Second scheduled reconciliation converges to success without any change
    # to the governance check-run/status fixtures (no fresh governance run
    # was required to reach this state).
    hunter_merge_readiness.evaluate(123, poll=False)
    assert gh.published[-1][1] == "success"
    assert "Ready to merge" in gh.published[-1][2]
