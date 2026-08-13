import json
import os
import sys
import urllib.error
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

# Ensure scripts directory is in PYTHONPATH so we can import hunter_merge_readiness
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

import hunter_merge_readiness


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com", code, "mock", {}, None)


class MockGitHubServer:
    def __init__(self):
        self.pulls = {}
        self.check_runs = {}
        self.statuses = {}
        self.comments = {}
        self.reviews = {}
        self.reactions = {}
        self.review_comments = {}
        self.refs = {}
        self.workflow_runs = {}
        self.published = []
        self.gql_threads = 0
        self.api_calls = []  # Log order of all API calls to assert sequencing

    def request_json(self, method, path, payload=None):
        clean_path = path.split("?")[0]
        self.api_calls.append((method, clean_path, payload))

        if method == "POST":
            if clean_path == "git/refs":
                ref = payload["ref"]
                if ref in self.refs:
                    raise http_error(422)
                self.refs[ref] = payload["sha"]
                return {}
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
                        # Monotonic, like GitHub: a newly posted status always
                        # outranks every status already on the SHA, including
                        # seeded fixtures that start from a high id.
                        "id": max((s.get("id", 0) for s in self.statuses[sha]), default=0) + 1,
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )
                return {}
            return {}

        if method == "DELETE":
            if clean_path.startswith("git/refs/"):
                ref = "refs/" + clean_path[len("git/refs/") :]
                if ref not in self.refs:
                    raise http_error(404)
                del self.refs[ref]
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

        if clean_path.startswith("actions/runs/"):
            run_id = int(clean_path.split("/")[-1])
            if run_id not in self.workflow_runs:
                raise http_error(404)
            return self.workflow_runs[run_id]

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


# ====================================================================================
# WORKFLOW_DISPATCH ON-DEMAND RECONCILIATION TESTS
# ====================================================================================


def test_workflow_dispatch_performs_full_reconciliation_like_schedule(gh):
    """workflow_dispatch must perform the same full, check-runs-based reconciliation
    as schedule for a single PR, not the lightweight webhook "wait" shortcut, so an
    operator can force an immediate accurate answer instead of waiting on the
    schedule's real-world cadence.
    """
    hunter_merge_readiness.event_name = "workflow_dispatch"

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


def test_workflow_dispatch_waits_when_no_governance_run_exists(gh):
    """workflow_dispatch must still correctly report a pending wait, rather than
    fabricating success, when no Hunter Governance Review check run exists yet for
    the current head SHA.
    """
    hunter_merge_readiness.event_name = "workflow_dispatch"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }
    gh.check_runs["sha_123"] = []
    gh.statuses["sha_123"] = []

    hunter_merge_readiness.evaluate(123, poll=False)

    assert len(gh.published) > 0
    assert gh.published[-1][1] == "pending"
    assert "Waiting for current Hunter Governance Review" in gh.published[-1][2]


def test_issue_comment_event_still_uses_lightweight_shortcut(gh):
    """Non-reconciliation events (e.g. issue_comment) must still use the lightweight
    invalidate-and-wait shortcut rather than performing a full reconciliation; only
    schedule and workflow_dispatch are reconciliation events. This guards against
    accidentally broadening RECONCILIATION_EVENT_NAMES beyond its intended scope.
    """
    hunter_merge_readiness.event_name = "issue_comment"

    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-05T00:30:00Z",
    }

    # Checks are fully green, but issue_comment has no governance_started_at of its
    # own and must not consume the check-runs state directly.
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
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    assert len(gh.published) > 0
    assert gh.published[-1][1] == "pending"
    assert "Waiting for current Hunter Governance Review" in gh.published[-1][2]


def test_main_workflow_dispatch_evaluates_requested_pr(gh, tmp_path, monkeypatch):
    """main() must read pr_number from the workflow_dispatch event payload's
    `inputs` and evaluate exactly that PR with poll=False.
    """
    event_path = tmp_path / "event.json"
    event_path.write_text('{"inputs": {"pr_number": "123"}}')
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GH_REPO", "fafa33/Project-Hunter")
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("RUN_URL", "https://github.com/fafa33/Project-Hunter/actions/runs/1")

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
    ]

    hunter_merge_readiness.main()

    assert len(gh.published) > 0
    assert gh.published[-1][1] == "success"
    assert "Ready to merge" in gh.published[-1][2]


def test_main_workflow_dispatch_without_pr_number_exits(tmp_path, monkeypatch):
    """main() must fail fast (not silently no-op) when workflow_dispatch fires
    without the required pr_number input.
    """
    event_path = tmp_path / "event.json"
    event_path.write_text('{"inputs": {}}')
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GH_REPO", "fafa33/Project-Hunter")
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("RUN_URL", "https://github.com/fafa33/Project-Hunter/actions/runs/1")

    with pytest.raises(SystemExit):
        hunter_merge_readiness.main()


# ====================================================================================
# RECONCILIATION LOCK / SCHEDULE-VS-EDIT RACE REGRESSION TESTS
# ====================================================================================


def _fully_green_pr(sha="sha_123", updated_at="2026-08-05T00:30:00Z"):
    return {
        "number": 123,
        "state": "open",
        "head": {"sha": sha},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": updated_at,
    }


def _fully_green_check_runs(sha="sha_123", governance_started_at="2026-08-05T00:50:00Z"):
    return [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": governance_started_at,
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]


def test_schedule_edit_race_prevents_stale_success(gh):
    """Test: a real PR edit that lands after evaluate()'s initial PR fetch but
    before the final success publish must still be caught. This is the exact
    schedule-vs-edit race: a scheduled sweep reads a fully-green snapshot, and
    a concurrent real edit's invalidation becomes visible only when
    ``_confirm_still_fresh_before_success`` re-fetches the PR under the lock.
    Expected: readiness stays pending, never publishes the stale success.
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = _fully_green_pr(updated_at="2026-08-05T00:30:00Z")
    gh.check_runs["sha_123"] = _fully_green_check_runs()
    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting...", "id": 2},
    ]

    pulls_get_count = {"n": 0}
    original_request_json = gh.request_json

    def racing_request_json(method, path, payload=None):
        clean_path = path.split("?")[0]
        if method == "GET" and clean_path == "pulls/123":
            pulls_get_count["n"] += 1
            if pulls_get_count["n"] >= 2:
                # A real edit landed concurrently, after evaluate()'s initial
                # read but before the confirm-still-fresh recheck.
                return _fully_green_pr(updated_at="2026-08-05T01:10:00Z")
        return original_request_json(method, path, payload)

    with patch("hunter_merge_readiness.request_json", side_effect=racing_request_json):
        hunter_merge_readiness.evaluate(123, poll=False)

    assert pulls_get_count["n"] >= 2
    assert gh.published[-1][1] == "pending"
    assert "concurrent semantic invalidation detected" in gh.published[-1][2]
    # The stale success must never have been published at all.
    assert all(state != "success" for _sha, state, _desc in gh.published)


def test_confirm_still_fresh_blocks_on_lock_contention(gh, monkeypatch):
    """If the per-PR reconciliation lock cannot be acquired immediately before
    the success publish (e.g. a concurrent semantic-invalidation write holds
    it), readiness must stay pending rather than fail open into success.
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = _fully_green_pr()
    gh.check_runs["sha_123"] = _fully_green_check_runs()
    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting...", "id": 2},
    ]

    monkeypatch.setattr(hunter_merge_readiness, "acquire_pr_lock", lambda pr_number, sha: None)

    hunter_merge_readiness.evaluate(123, poll=False)

    assert gh.published[-1][1] == "pending"
    assert "concurrent semantic invalidation detected" in gh.published[-1][2]


def test_confirm_still_fresh_blocks_on_head_change(gh):
    """If the PR's head SHA changed by the time ``_confirm_still_fresh_before_success``
    re-fetches it, readiness must stay pending for the (now-superseded) SHA it
    was evaluating, never publish success for a head that has already moved on.
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = _fully_green_pr(sha="sha_123")
    gh.check_runs["sha_123"] = _fully_green_check_runs(sha="sha_123")
    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting...", "id": 2},
    ]

    pulls_get_count = {"n": 0}
    original_request_json = gh.request_json

    def head_moves_request_json(method, path, payload=None):
        clean_path = path.split("?")[0]
        if method == "GET" and clean_path == "pulls/123":
            pulls_get_count["n"] += 1
            if pulls_get_count["n"] >= 2:
                return _fully_green_pr(sha="sha_new")
        return original_request_json(method, path, payload)

    with patch("hunter_merge_readiness.request_json", side_effect=head_moves_request_json):
        hunter_merge_readiness.evaluate(123, poll=False)

    assert gh.published[-1][1] == "pending"
    assert "concurrent semantic invalidation detected" in gh.published[-1][2]
    assert all(state != "success" for _sha, state, _desc in gh.published)


def test_confirm_still_fresh_succeeds_and_releases_lock_when_still_fresh(gh):
    """Test: fresh Governance with no concurrent invalidation -> readiness
    succeeds, and the reconciliation lock is acquired then released exactly
    once around the confirmation, never left held.
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = _fully_green_pr()
    gh.check_runs["sha_123"] = _fully_green_check_runs()
    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting...", "id": 2},
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    assert gh.published[-1][1] == "success"
    assert "Ready to merge" in gh.published[-1][2]
    # Lock was created then deleted; nothing left held afterward.
    assert gh.refs == {}
    lock_creates = [call for call in gh.api_calls if call[0] == "POST" and call[1] == "git/refs"]
    lock_deletes = [call for call in gh.api_calls if call[0] == "DELETE" and call[1].startswith("git/refs/")]
    assert len(lock_creates) == 1
    assert len(lock_deletes) == 1


def test_acquire_pr_lock_retries_on_contention_then_succeeds():
    """acquire_pr_lock must retry (not immediately fail closed) on a 422
    contention response, and eventually succeed once the holder releases it.
    """
    sleeps = []
    calls = {"n": 0}

    def flaky_request_json(method, path, payload=None):
        if method == "POST" and path == "git/refs":
            calls["n"] += 1
            if calls["n"] < 3:
                raise http_error(422)
            return {}
        raise AssertionError(f"unexpected call: {method} {path}")

    with patch("hunter_merge_readiness.request_json", side_effect=flaky_request_json):
        result = hunter_merge_readiness.acquire_pr_lock(123, "sha_123", sleep_fn=sleeps.append)

    assert result == "refs/hunter-merge-readiness-locks/pr-123"
    assert calls["n"] == 3
    assert len(sleeps) == 2


def test_release_pr_lock_tolerates_missing_ref():
    """release_pr_lock must not raise when the ref is already gone (404) --
    e.g. a previous run's abandoned-lock recovery already cleared it.
    """

    def not_found(method, path, payload=None):
        assert method == "DELETE"
        assert path == "git/refs/hunter-merge-readiness-locks/pr-123"
        raise http_error(404)

    with patch("hunter_merge_readiness.request_json", side_effect=not_found):
        hunter_merge_readiness.release_pr_lock("refs/hunter-merge-readiness-locks/pr-123")


class _FakeUrlopenResponse:
    """Minimal context-manager stand-in for urllib.request.urlopen()'s
    response object, for testing request_json()'s own implementation
    (every other test in this file mocks request_json itself away, so none
    of them exercise its real body-parsing logic).
    """

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body


def test_request_json_treats_empty_response_body_as_no_content(monkeypatch):
    """A successful DELETE (e.g. releasing the per-PR lock ref) returns 204
    No Content with an empty body. request_json must return None for that,
    not crash trying to json.loads("") -- this is a real regression: a
    successful lock release used to crash with JSONDecodeError, causing
    every semantic-invalidation write to spuriously fail closed even though
    the durable marker itself had already been persisted.
    """
    hunter_merge_readiness.repo = "fafa33/Project-Hunter"
    hunter_merge_readiness.token = "fake-token"
    monkeypatch.setattr(
        hunter_merge_readiness.urllib.request,
        "urlopen",
        lambda request, timeout=30: _FakeUrlopenResponse(b""),
    )

    result = hunter_merge_readiness.request_json("DELETE", "git/refs/heads/some-lock")

    assert result is None


def test_request_json_still_parses_a_real_json_body(monkeypatch):
    hunter_merge_readiness.repo = "fafa33/Project-Hunter"
    hunter_merge_readiness.token = "fake-token"
    monkeypatch.setattr(
        hunter_merge_readiness.urllib.request,
        "urlopen",
        lambda request, timeout=30: _FakeUrlopenResponse(b'{"ok": true}'),
    )

    result = hunter_merge_readiness.request_json("GET", "pulls/123")

    assert result == {"ok": True}


def test_human_comment_after_governance_stales_governance_freshness(gh):
    """A human top-level comment posted after Governance ran must stale
    Governance freshness itself (not merely block on acknowledgment) -- an
    owner +1 on the same comment resolves the acknowledgment blocker but a
    fresh Governance Review is still required first.
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[123] = _fully_green_pr(updated_at="2026-08-05T00:30:00Z")
    gh.check_runs["sha_123"] = _fully_green_check_runs(governance_started_at="2026-08-05T00:50:00Z")
    gh.statuses["sha_123"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-05T01:00:00Z", "id": 1},
        {"context": "Hunter Merge Readiness", "state": "pending", "description": "Waiting...", "id": 2},
    ]
    # Comment posted after governance started (00:50) and after its status
    # was recorded (01:00) -- a real, later human comment. Owner acknowledges
    # it (satisfying the separate feedback/acknowledgment gate) so this test
    # isolates the freshness gate specifically: acknowledgment resolves the
    # comment-blocking failure, but the comment's own timestamp still
    # participates in governance-freshness computation regardless.
    gh.comments[123] = [
        {
            "id": 999,
            "body": "This changes the approach, please reconsider.",
            "created_at": "2026-08-05T01:15:00Z",
            "updated_at": "2026-08-05T01:15:00Z",
        }
    ]
    gh.reactions[999] = [
        {
            "user": {"login": "fafa33"},
            "content": "+1",
            "created_at": "2026-08-05T01:20:00Z",
        }
    ]

    hunter_merge_readiness.evaluate(123, poll=False)

    assert gh.published[-1][1] == "pending"
    assert "Waiting for a fresh Hunter Governance Review" in gh.published[-1][2]


def test_lock_ref_is_not_under_refs_heads():
    """The reconciliation lock ref must NOT live under refs/heads/ (a branch)
    or refs/tags/. Creating/deleting a refs/heads/* ref is a `push`/`create`
    webhook event, and this repository's hunter-pre-pr-preflight.yml workflow
    triggers on `push: branches-ignore: [main]` -- a lock ref under
    refs/heads/ would recursively spawn a full Preflight CI run on every
    single lock acquisition. This guards against ever reintroducing that.
    """
    ref_name = hunter_merge_readiness.LOCK_REF_NAMESPACE + "123"
    assert not ref_name.startswith("refs/heads/")
    assert not ref_name.startswith("refs/tags/")


# ====================================================================================
# GOVERNANCE FRESHNESS AUTHORITY
#
# Regression coverage for a stuck-pending class observed live on PR #256.
# Hunter Governance Review reaches an exact head by two delivery paths: the
# pull_request-triggered run, which leaves a check run on that head, and the
# "Hunter Governance Review Reconcile" sweep, which republishes the
# authoritative commit status from a run whose own check run belongs to the
# default branch. Freshness used to be read only from the exact-head check run,
# so every reconcile-published verdict was invisible and the PR could never
# leave "Waiting for a fresh Hunter Governance Review".
# ====================================================================================


RECONCILE_RUN_ID = 31662976298


def _stuck_pending_pr(gh, *, governance_check_started, invalidated_at, reconcile_started, reconcile_published):
    """A PR whose only exact-head Governance check run predates its invalidation.

    The fresh Governance verdict exists solely as a commit status republished by
    the reconcile sweep, exactly as observed live.
    """
    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": invalidated_at,
    }
    gh.check_runs["sha_123"] = [
        {
            "name": "Hunter Governance Review",
            "status": "completed",
            "conclusion": "success",
            "started_at": governance_check_started,
            "id": 100,
        },
        {"name": "Quality Gates", "status": "completed", "conclusion": "success", "id": 101},
        {"name": "dependency-review", "status": "completed", "conclusion": "success", "id": 102},
        {"name": "CodeQL", "status": "completed", "conclusion": "success", "id": 103},
    ]
    gh.statuses["sha_123"] = [
        {
            "context": "Hunter Governance Review",
            "state": "success",
            "created_at": reconcile_published,
            "target_url": f"https://github.com/fafa33/Project-Hunter/actions/runs/{RECONCILE_RUN_ID}",
            "id": 10,
        },
        {
            "context": "Hunter Merge Readiness",
            "state": "pending",
            "description": "Waiting for a fresh Hunter Governance Review (last run was older than latest invalidation).",
            "id": 11,
        },
    ]
    gh.workflow_runs[RECONCILE_RUN_ID] = {
        "id": RECONCILE_RUN_ID,
        "name": "Hunter Governance Review Reconcile",
        "run_started_at": reconcile_started,
        "head_branch": "main",
    }


def test_reconcile_published_governance_clears_stale_pending(gh):
    """The exact live failure: stale PENDING must converge to SUCCESS.

    The only exact-head Governance check run started BEFORE the invalidation,
    so the old freshness rule rejected it forever. The reconcile sweep's run
    began well after the invalidation, so its verdict genuinely observed the
    invalidating change and must satisfy freshness.
    """
    hunter_merge_readiness.event_name = "schedule"
    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-12T22:36:23Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )

    hunter_merge_readiness.evaluate(123, poll=False)

    assert gh.published[-1][1] == "success"
    assert "Ready to merge" in gh.published[-1][2]


def test_reconcile_run_starting_before_invalidation_still_fails_closed(gh):
    """Fail-closed is preserved: publication time alone must never satisfy freshness.

    A long-running review that began before the invalidating change cannot have
    observed it, even though it published afterwards.
    """
    hunter_merge_readiness.event_name = "schedule"
    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-12T22:36:23Z",
        reconcile_started="2026-08-12T22:36:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )

    hunter_merge_readiness.evaluate(123, poll=False)

    assert gh.published[-1][1] == "pending"
    assert "Waiting for a fresh Hunter Governance Review" in gh.published[-1][2]
    assert all(state != "success" for _sha, state, _desc in gh.published)


def test_newest_governance_evaluation_wins_across_both_delivery_paths(gh):
    """When both paths have evidence, the one that evaluated latest decides."""
    hunter_merge_readiness.event_name = "schedule"
    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-13T04:00:00Z",
        invalidated_at="2026-08-12T22:36:23Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )

    evaluation = hunter_merge_readiness.resolve_governance_evaluation("sha_123", gh.check_runs["sha_123"])

    assert evaluation is not None
    assert evaluation.source == "check run"
    assert evaluation.started_at == datetime(2026, 8, 13, 4, 0, tzinfo=UTC)


def test_unresolvable_governance_status_is_ignored_not_trusted(gh):
    """A status whose originating run cannot be resolved must not grant freshness."""
    hunter_merge_readiness.event_name = "schedule"
    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-12T22:36:23Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )
    # The run behind the status no longer exists (log retention, deleted run).
    del gh.workflow_runs[RECONCILE_RUN_ID]

    hunter_merge_readiness.evaluate(123, poll=False)

    assert gh.published[-1][1] == "pending"
    assert all(state != "success" for _sha, state, _desc in gh.published)


def test_governance_status_without_target_url_is_ignored(gh):
    """Legacy statuses carrying no run reference must not be treated as fresh."""
    hunter_merge_readiness.event_name = "schedule"
    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-12T22:36:23Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )
    gh.statuses["sha_123"][0].pop("target_url")

    hunter_merge_readiness.evaluate(123, poll=False)

    assert gh.published[-1][1] == "pending"
    assert all(state != "success" for _sha, state, _desc in gh.published)


def test_failed_reconcile_verdict_publishes_failure_not_success(gh):
    """A fresh but failing reconcile verdict must fail, not linger pending."""
    hunter_merge_readiness.event_name = "schedule"
    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-12T22:36:23Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )
    gh.statuses["sha_123"][0]["state"] = "failure"

    hunter_merge_readiness.evaluate(123, poll=False)

    assert gh.published[-1][1] == "failure"


def test_pending_reconcile_verdict_waits_rather_than_concluding(gh):
    """An in-flight fresh evaluation must wait, never be read as a conclusion."""
    hunter_merge_readiness.event_name = "schedule"
    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-12T22:36:23Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )
    gh.statuses["sha_123"][0]["state"] = "pending"

    hunter_merge_readiness.evaluate(123, poll=False)

    assert gh.published[-1][1] == "pending"
    assert gh.published[-1][2] == "Waiting for current Hunter Governance Review."


# ====================================================================================
# workflow_run pull-request identity recovery
# ====================================================================================


def test_workflow_run_without_pull_requests_recovers_pr_by_head_sha(gh, tmp_path, monkeypatch):
    """An empty pull_requests payload must not silently reconcile nothing."""
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "workflow_run": {
                    "pull_requests": [],
                    "head_sha": "sha_123",
                    "head_branch": "feature/x",
                    "run_started_at": "2026-08-13T03:08:00Z",
                    "conclusion": "success",
                }
            }
        )
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GH_REPO", "fafa33/Project-Hunter")
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("EVENT_NAME", "workflow_run")
    monkeypatch.setenv("RUN_URL", "https://example.invalid/run")

    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-12T22:36:23Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )

    hunter_merge_readiness.main()

    assert gh.published, "controller reconciled nothing despite a recoverable head SHA"
    assert gh.published[-1][1] == "success"


def test_workflow_run_without_any_matching_open_pr_exits_cleanly(gh, tmp_path, monkeypatch):
    """Recovery must stay bounded: an unrelated head reconciles nothing."""
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "workflow_run": {
                    "pull_requests": [],
                    "head_sha": "sha_unrelated",
                    "run_started_at": "2026-08-13T03:08:00Z",
                    "conclusion": "success",
                }
            }
        )
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GH_REPO", "fafa33/Project-Hunter")
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("EVENT_NAME", "workflow_run")
    monkeypatch.setenv("RUN_URL", "https://example.invalid/run")

    gh.pulls[123] = {"number": 123, "state": "open", "head": {"sha": "sha_123"}, "draft": False}

    with pytest.raises(SystemExit):
        hunter_merge_readiness.main()

    assert gh.published == []


def test_recovered_pr_is_bound_to_the_evaluated_head(gh, tmp_path, monkeypatch):
    """Recovery must never publish against a head the triggering run did not evaluate."""
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "workflow_run": {
                    "pull_requests": [],
                    "head_sha": "sha_stale",
                    "run_started_at": "2026-08-13T03:08:00Z",
                    "conclusion": "success",
                }
            }
        )
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GH_REPO", "fafa33/Project-Hunter")
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("EVENT_NAME", "workflow_run")
    monkeypatch.setenv("RUN_URL", "https://example.invalid/run")

    # The PR has already moved on to a newer head, so nothing may be published.
    gh.pulls[123] = {"number": 123, "state": "open", "head": {"sha": "sha_new"}, "draft": False}

    with pytest.raises(SystemExit):
        hunter_merge_readiness.main()

    assert gh.published == []


def test_open_pull_requests_paginates_beyond_one_page(gh):
    """PR discovery must not stop at the first hundred open pull requests."""
    calls = []
    first_page = [{"number": index, "head": {"sha": f"sha_{index}"}} for index in range(100)]
    second_page = [{"number": 500, "head": {"sha": "sha_target"}}]

    def paginated(method, path, payload=None):
        calls.append(path)
        if path.endswith("&page=1"):
            return first_page
        if path.endswith("&page=2"):
            return second_page
        raise AssertionError(f"unexpected page request: {path}")

    with patch("hunter_merge_readiness.request_json", side_effect=paginated):
        recovered = hunter_merge_readiness.open_pull_requests_for_head("sha_target")

    assert len(calls) == 2
    assert [pr["number"] for pr in recovered] == [500]


def test_governance_status_beyond_the_first_status_page_is_still_found(gh):
    """Exact-head Governance evidence must survive a head with >100 statuses.

    A busy head pushes the authoritative Governance status onto a later page.
    Reading only the first page would make a fresh verdict invisible and
    reproduce the same permanent-pending failure through a different route.
    """
    hunter_merge_readiness.event_name = "schedule"
    filler = [{"context": "Other Check", "state": "success", "id": index} for index in range(100)]
    governance = {
        "context": "Hunter Governance Review",
        "state": "success",
        "created_at": "2026-08-13T03:08:17Z",
        "target_url": f"https://github.com/fafa33/Project-Hunter/actions/runs/{RECONCILE_RUN_ID}",
        "id": 900,
    }

    def paginated(method, path, payload=None):
        if method == "GET" and path.startswith("commits/sha_123/statuses"):
            if path.endswith("&page=1"):
                return filler
            if path.endswith("&page=2"):
                return [governance]
            raise AssertionError(f"unexpected status page request: {path}")
        return gh.request_json(method, path, payload)

    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-12T22:36:23Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )

    with patch("hunter_merge_readiness.request_json", side_effect=paginated):
        resolved = hunter_merge_readiness.latest_commit_status("sha_123", "Hunter Governance Review")
        evaluation = hunter_merge_readiness.resolve_governance_evaluation("sha_123", gh.check_runs["sha_123"])

    assert resolved is not None and resolved["id"] == 900
    assert evaluation is not None
    assert evaluation.source == "commit status"
    assert evaluation.started_at == datetime(2026, 8, 13, 3, 8, tzinfo=UTC)


def test_same_second_governance_and_invalidation_does_not_regress_freshness(gh):
    """An evaluation beginning in the same second as the invalidation is fresh.

    Freshness is `started_at >= invalidation`. GitHub timestamps are
    second-granular, so treating an equal timestamp as stale would strand any
    pull request whose review happened to start within the invalidating second.
    """
    hunter_merge_readiness.event_name = "schedule"
    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-13T03:08:00Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )

    hunter_merge_readiness.evaluate(123, poll=False)

    assert gh.published[-1][1] == "success"


def test_manual_reconciliation_repairs_a_stuck_pr_without_touching_it(gh):
    """workflow_dispatch must repair a stuck pull request the same way the sweep does.

    The repair may not depend on editing the pull request, its head, or its
    body: only on re-reading evidence that already exists.
    """
    hunter_merge_readiness.event_name = "workflow_dispatch"
    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-12T22:36:23Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )
    before = json.dumps(gh.pulls[123], sort_keys=True)

    hunter_merge_readiness.evaluate(123, poll=False)

    assert gh.published[-1][1] == "success"
    assert json.dumps(gh.pulls[123], sort_keys=True) == before
    # The only writes a repair may perform are the readiness status itself and
    # the reconciliation lock. Nothing may mutate the pull request or its
    # conversation -- no body edit, no comment, no head change.
    # "graphql" is the review-thread read; it is POST only by protocol.
    writes = [(method, path) for method, path, _ in gh.api_calls if method != "GET" and path != "graphql"]
    assert writes, "expected at least the status publication"
    assert all(path.startswith("statuses/") or path.startswith("git/refs") for _method, path in writes)


def test_readiness_is_only_ever_published_against_the_pull_request_head(gh):
    """No reconciliation path may publish readiness against the default-branch SHA."""
    hunter_merge_readiness.event_name = "schedule"
    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-12T22:36:23Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )
    # The reconcile sweep's own run belongs to the default branch; its SHA must
    # never become a publication target just because it produced the verdict.
    gh.workflow_runs[RECONCILE_RUN_ID]["head_sha"] = "sha_main"

    hunter_merge_readiness.evaluate(123, poll=False)

    assert gh.published, "expected a publication"
    assert {sha for sha, _state, _desc in gh.published} == {"sha_123"}


def test_controller_status_writes_do_not_invalidate_the_controller(gh):
    """The controller's own publications must not feed its invalidation boundary.

    If they did, every reconciliation would invalidate the verdict it had just
    accepted and the sweep would oscillate forever instead of converging.
    """
    hunter_merge_readiness.event_name = "schedule"
    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-12T22:36:23Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )

    hunter_merge_readiness.evaluate(123, poll=False)
    assert gh.published[-1][1] == "success"
    publications_after_first_pass = len(gh.published)

    # A second sweep observes the status the first sweep just wrote.
    hunter_merge_readiness.latest_readiness = None
    hunter_merge_readiness.evaluate(123, poll=False)

    assert len(gh.published) == publications_after_first_pass, "converged state was republished"
    assert gh.published[-1][1] == "success"


# ====================================================================================
# Event payloads are hints, never current truth
# ====================================================================================


def test_replayed_workflow_run_payload_does_not_override_newer_head_evidence(gh, tmp_path, monkeypatch):
    """Re-running an old workflow_run replays its original payload verbatim.

    That payload described a Governance evaluation the head has already moved
    past. Current exact-head evidence that began later must decide, otherwise a
    rerun would publish a terminal failure that contradicts the live verdict.
    """
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "workflow_run": {
                    "pull_requests": [{"number": 123, "head": {"sha": "sha_123"}}],
                    "head_sha": "sha_123",
                    "run_started_at": "2026-08-12T22:36:14Z",
                    "conclusion": "failure",
                }
            }
        )
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GH_REPO", "fafa33/Project-Hunter")
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("EVENT_NAME", "workflow_run")
    monkeypatch.setenv("RUN_URL", "https://example.invalid/run")

    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-12T22:36:23Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )

    hunter_merge_readiness.main()

    assert gh.published[-1][1] == "success"
    assert all(state != "failure" for _sha, state, _desc in gh.published)


def test_older_head_evidence_never_displaces_a_newer_event_payload(gh, tmp_path, monkeypatch):
    """Supersession is strictly one-directional.

    The ordinary path -- Governance finishes and immediately triggers the
    controller -- must keep using the payload it was handed, even while an older
    evaluation of the same head is still the newest one visible through the API.
    """
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "workflow_run": {
                    "pull_requests": [{"number": 123, "head": {"sha": "sha_123"}}],
                    "head_sha": "sha_123",
                    "run_started_at": "2026-08-13T05:00:00Z",
                    "conclusion": "success",
                }
            }
        )
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GH_REPO", "fafa33/Project-Hunter")
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("EVENT_NAME", "workflow_run")
    monkeypatch.setenv("RUN_URL", "https://example.invalid/run")

    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-13T04:00:00Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T05:00:30Z",
    )

    hunter_merge_readiness.main()

    # Only the payload's 05:00 start clears the 04:00 invalidation; the older
    # 03:08 evaluation visible on the head would have failed closed.
    assert gh.published[-1][1] == "success"


def test_superseded_pull_request_target_run_still_converges_via_successor(gh, tmp_path, monkeypatch):
    """The cancellation race must not be able to strand a pull request.

    A pull_request_target run publishes the immediate pending invalidation and
    is then cancelled before it can publish anything terminal. The successor
    workflow_run arrives without pull-request identity. Convergence must still
    happen.
    """
    _stuck_pending_pr(
        gh,
        governance_check_started="2026-08-12T22:36:14Z",
        invalidated_at="2026-08-12T22:36:23Z",
        reconcile_started="2026-08-13T03:08:00Z",
        reconcile_published="2026-08-13T03:08:17Z",
    )

    # The cancelled run got as far as invalidating the green status.
    hunter_merge_readiness.event_name = "pull_request_target"
    hunter_merge_readiness.latest_readiness = {"state": "success", "description": "Ready to merge"}
    hunter_merge_readiness.publish("sha_123", "pending", "Validating current feedback and exact-head prerequisites.")
    assert gh.published[-1][1] == "pending"

    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "workflow_run": {
                    "pull_requests": [],
                    "head_sha": "sha_123",
                    "run_started_at": "2026-08-13T03:08:00Z",
                    "conclusion": "success",
                }
            }
        )
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GH_REPO", "fafa33/Project-Hunter")
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("EVENT_NAME", "workflow_run")
    monkeypatch.setenv("RUN_URL", "https://example.invalid/run")

    hunter_merge_readiness.main()

    assert gh.published[-1][1] == "success"
