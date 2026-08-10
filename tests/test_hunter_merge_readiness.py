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
        self.published = []
        self.gql_threads = 0

    def request_json(self, method, path, payload=None):
        clean_path = path.split("?")[0]

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
            # e.g., issues/123/comments
            if parts[2] == "comments":
                if parts[1] == "comments":
                    # e.g., issues/comments/comment_id/reactions
                    comment_id = int(parts[3])
                    return self.reactions.get(comment_id, [])
                else:
                    pr_num = int(parts[1])
                    return self.comments.get(pr_num, [])

        if clean_path == "pulls":
            return list(self.pulls.values())

        return {}

    def graphql_json(self, query, variables):
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

    # Setup PR
    gh.pulls[123] = {
        "number": 123,
        "state": "open",
        "head": {"sha": "sha_123"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
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
