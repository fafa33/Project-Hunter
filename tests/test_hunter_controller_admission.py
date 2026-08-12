import os
import sys
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

import hunter_controller_admission as admission
import hunter_merge_readiness


class MockGitHubServer:
    """Minimal GitHub API mock covering everything the admission kernel and
    evaluate() need: pulls, changed files, check-runs, statuses, comments,
    reviews, review comments, and unresolved-thread GraphQL.
    """

    def __init__(self):
        self.pulls = {}
        self.pr_files = {}
        self.check_runs = {}
        self.statuses = {}
        self.comments = {}
        self.reviews = {}
        self.review_comments = {}
        self.refs = {}
        self.published = []
        self.gql_threads = 0
        self.api_calls = []

    def request_json(self, method, path, payload=None):
        clean_path = path.split("?")[0]
        self.api_calls.append((method, clean_path, payload))

        if method == "POST":
            if clean_path == "git/refs":
                ref = payload["ref"]
                if ref in self.refs:
                    from urllib.error import HTTPError

                    raise HTTPError("https://api.github.com", 422, "mock", {}, None)
                self.refs[ref] = payload["sha"]
                return {}
            if clean_path.startswith("statuses/"):
                sha = clean_path.split("/")[-1]
                self.published.append((sha, payload["state"], payload["description"]))
                self.statuses.setdefault(sha, []).append(
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

        if method == "DELETE":
            if clean_path.startswith("git/refs/"):
                ref = "refs/" + clean_path[len("git/refs/") :]
                if ref in self.refs:
                    del self.refs[ref]
                return {}
            return {}

        if clean_path.startswith("pulls/"):
            parts = clean_path.split("/")
            pr_num = int(parts[1])
            if len(parts) > 2:
                sub_resource = parts[2]
                if sub_resource == "files":
                    return self.pr_files.get(pr_num, [])
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
            if parts[1] == "comments" and len(parts) > 3 and parts[3] == "reactions":
                return []
            if len(parts) > 2 and parts[2] == "comments":
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


def green_check_runs(sha, governance_started_at="2026-08-12T10:00:00Z"):
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


def candidate_pr(sha="sha_upgrade", updated_at="2026-08-12T09:00:00Z"):
    return {
        "number": 251,
        "state": "open",
        "head": {"sha": sha},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "created_at": "2026-08-12T06:00:00Z",
        "updated_at": updated_at,
    }


# ====================================================================================
# 11-12: candidate detection is deterministic and evidence-only
# ====================================================================================


def test_ordinary_pr_is_not_a_controller_upgrade_candidate(gh):
    gh.pr_files[251] = [{"filename": "src/hunter/some_engine.py"}]
    assert admission.is_controller_upgrade_candidate(251) is False


@pytest.mark.parametrize(
    "path",
    [
        "scripts/hunter_merge_readiness.py",
        "scripts/hunter_merge_readiness_entrypoint.py",
        "scripts/hunter_controller_admission.py",
        ".github/workflows/hunter-merge-readiness.yml",
    ],
)
def test_controller_upgrade_candidate_detected_for_each_owned_path(gh, path):
    gh.pr_files[251] = [{"filename": "README.md"}, {"filename": path}]
    assert admission.is_controller_upgrade_candidate(251) is True


def test_candidate_detection_ignores_pr_body_and_number(gh):
    """Detection must be evidence-only: a PR whose body/title claims to be a
    controller upgrade, but whose changed files don't touch owned paths, is
    NOT a candidate -- and vice versa. No PR-number-specific exception.
    """
    gh.pulls[999] = candidate_pr()
    gh.pulls[999]["body"] = "This PR upgrades the Merge Readiness controller!!!"
    gh.pr_files[999] = [{"filename": "src/hunter/unrelated.py"}]
    assert admission.is_controller_upgrade_candidate(999) is False


def test_candidate_detection_catches_rename_of_an_owned_path(gh):
    """A PR that renames a controller-owned file away from its current name
    must still be detected as a candidate via previous_filename, not just
    the new filename.
    """
    gh.pr_files[251] = [
        {
            "filename": "scripts/hunter_merge_readiness_core.py",
            "previous_filename": "scripts/hunter_merge_readiness.py",
        }
    ]
    assert admission.is_controller_upgrade_candidate(251) is True


# ====================================================================================
# 13/24: exact-head binding, replay rejection
# ====================================================================================


def test_admission_rejects_when_head_changed_since_evaluation_started(gh):
    gh.pulls[251] = candidate_pr(sha="sha_new")
    result = admission.evaluate_admission(
        251,
        candidate_pr(sha="sha_old"),
        "sha_old",
        green_check_runs("sha_old"),
        datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
    )
    assert result.admitted is False
    assert "head changed" in result.reason


def test_admission_rejects_replay_of_evidence_bound_to_an_old_head(gh):
    """Evidence (check-runs) gathered for an old head must not be usable to
    admit a newer head, even if that evidence looks perfectly green -- the
    fresh re-fetch inside evaluate_admission is what prevents this replay.
    """
    gh.pulls[251] = candidate_pr(sha="sha_current")
    stale_runs = green_check_runs("sha_replayed")
    result = admission.evaluate_admission(
        251,
        candidate_pr(sha="sha_replayed"),
        "sha_replayed",
        stale_runs,
        datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
    )
    assert result.admitted is False
    assert "head changed" in result.reason


# ====================================================================================
# 14-19: each required gate independently blocks admission
# ====================================================================================


def test_ci_failure_blocks_admission(gh):
    gh.pulls[251] = candidate_pr()
    runs = green_check_runs("sha_upgrade")
    runs[1]["conclusion"] = "failure"  # Quality Gates
    result = admission.evaluate_admission(
        251, gh.pulls[251], "sha_upgrade", runs, datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    )
    assert result.admitted is False
    assert "Quality Gates=failure" in result.reason


def test_codeql_failure_blocks_admission(gh):
    gh.pulls[251] = candidate_pr()
    runs = green_check_runs("sha_upgrade")
    runs[3]["conclusion"] = "failure"  # CodeQL
    result = admission.evaluate_admission(
        251, gh.pulls[251], "sha_upgrade", runs, datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    )
    assert result.admitted is False
    assert "CodeQL=failure" in result.reason


def test_dependency_review_failure_blocks_admission(gh):
    gh.pulls[251] = candidate_pr()
    runs = green_check_runs("sha_upgrade")
    runs[2]["conclusion"] = "failure"  # dependency-review
    result = admission.evaluate_admission(
        251, gh.pulls[251], "sha_upgrade", runs, datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    )
    assert result.admitted is False
    assert "dependency-review=failure" in result.reason


def test_governance_failure_blocks_admission(gh):
    gh.pulls[251] = candidate_pr()
    runs = green_check_runs("sha_upgrade")
    runs[0]["conclusion"] = "failure"  # Hunter Governance Review
    result = admission.evaluate_admission(
        251, gh.pulls[251], "sha_upgrade", runs, datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    )
    assert result.admitted is False
    assert "Hunter Governance Review=failure" in result.reason


def test_changes_requested_blocks_admission(gh):
    gh.pulls[251] = candidate_pr()
    gh.reviews[251] = [{"id": 1, "user": {"login": "reviewer"}, "state": "CHANGES_REQUESTED"}]
    result = admission.evaluate_admission(
        251,
        gh.pulls[251],
        "sha_upgrade",
        green_check_runs("sha_upgrade"),
        datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
    )
    assert result.admitted is False
    assert "reviewer" in result.reason


def test_unresolved_review_thread_blocks_admission(gh):
    gh.pulls[251] = candidate_pr()
    gh.gql_threads = 1
    result = admission.evaluate_admission(
        251,
        gh.pulls[251],
        "sha_upgrade",
        green_check_runs("sha_upgrade"),
        datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
    )
    assert result.admitted is False
    assert "unresolved review threads" in result.reason


def test_stale_governance_for_real_semantic_change_blocks_admission(gh):
    """A real edit (reflected here in updated_at, exactly like the raw core
    invalidation primitive already tracks) that postdates Governance's start
    must block admission, identically to how it blocks ordinary readiness.
    """
    gh.pulls[251] = candidate_pr(updated_at="2026-08-12T10:30:00Z")
    result = admission.evaluate_admission(
        251,
        gh.pulls[251],
        "sha_upgrade",
        green_check_runs("sha_upgrade", governance_started_at="2026-08-12T10:00:00Z"),
        datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
    )
    assert result.admitted is False
    assert "older than latest invalidation" in result.reason


# ====================================================================================
# 20/21/22: full green candidate is admitted
# ====================================================================================


def test_valid_exact_head_candidate_with_all_evidence_is_admitted(gh):
    gh.pulls[251] = candidate_pr()
    result = admission.evaluate_admission(
        251,
        gh.pulls[251],
        "sha_upgrade",
        green_check_runs("sha_upgrade"),
        datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
    )
    assert result.admitted is True
    assert "admitted" in result.reason


def test_current_generation_admits_next_generation_via_evaluate(gh):
    """End-to-end: evaluate() itself, using only the currently-resident
    module (representing 'the previous generation'), routes a controller-
    upgrade candidate through admission and reaches success -- without
    importing, executing, or reading the content of any candidate file.
    """
    hunter_merge_readiness.event_name = "schedule"
    gh.pulls[251] = candidate_pr(sha="sha_upgrade")
    gh.pr_files[251] = [{"filename": "scripts/hunter_merge_readiness.py"}]
    gh.check_runs["sha_upgrade"] = green_check_runs("sha_upgrade")
    gh.statuses["sha_upgrade"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-12T10:05:00Z", "id": 1},
    ]

    hunter_merge_readiness.evaluate(251, poll=False)

    assert gh.published[-1][1] == "success"
    assert "controller-upgrade PR admitted" in gh.published[-1][2]


# ====================================================================================
# 23: no privileged execution of candidate code (structural/static guarantee)
# ====================================================================================


def test_admission_kernel_never_imports_or_execs_dynamic_code():
    """Static guarantee: the admission kernel source contains no dynamic
    execution primitive that could run candidate-supplied code.
    """
    import pathlib

    source = pathlib.Path(admission.__file__).read_text()
    for forbidden in ("exec(", "eval(", "importlib", "subprocess", "__import__"):
        assert forbidden not in source, f"admission kernel must never contain {forbidden!r}"


def test_workflow_checkout_is_pinned_to_default_branch():
    """Static guarantee: the controller workflow always checks out the
    default branch, never the PR's own branch -- the security property this
    entire admission design depends on.
    """
    import pathlib

    workflow_path = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-merge-readiness.yml"
    text = workflow_path.read_text()
    assert "ref: ${{ github.event.repository.default_branch }}" in text


# ====================================================================================
# 25: no fabricated downgrade/version registry
# ====================================================================================


def test_admission_has_no_version_or_generation_registry(gh):
    """This design deliberately has no persisted controller version/generation
    number to compare against -- 'generation' is simply whatever is resident
    on the default branch. A candidate whose diff conceptually reverts prior
    controller behavior is evaluated through the exact same evidence gates
    as any other candidate; there is no separate 'reject downgrades' rule to
    test, and this test asserts that absence rather than fabricating one.
    """
    import inspect

    signature = inspect.signature(admission.evaluate_admission)
    for name in signature.parameters:
        assert "version" not in name.lower()
        assert "generation" not in name.lower()


# ====================================================================================
# 26: migration semantics are inherited from whatever is resident, not reinvented
# ====================================================================================


def test_admission_inherits_resident_freshness_primitive(gh, monkeypatch):
    """evaluate_admission must call whatever get_latest_invalidation_time is
    currently bound on the core module (e.g. a migration-aware, bot-exemption
    -aware version installed by the entrypoint), not a private copy -- so a
    legitimately stale candidate is still correctly rejected under whatever
    policy is resident, without evaluate_admission needing its own migration
    logic.
    """
    seen = {}

    def fake_freshness(pr_number, pr):
        seen["called_with_pr_number"] = pr_number
        return datetime(2026, 8, 12, 10, 30, tzinfo=UTC)

    monkeypatch.setattr(hunter_merge_readiness, "get_latest_invalidation_time", fake_freshness)
    gh.pulls[251] = candidate_pr()

    result = admission.evaluate_admission(
        251,
        gh.pulls[251],
        "sha_upgrade",
        green_check_runs("sha_upgrade", governance_started_at="2026-08-12T10:00:00Z"),
        datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
    )

    assert seen["called_with_pr_number"] == 251
    assert result.admitted is False
    assert "older than latest invalidation" in result.reason


# ====================================================================================
# 27: post-upgrade / concurrent ordinary PRs are unaffected
# ====================================================================================


def test_ordinary_pr_evaluated_after_upgrade_candidate_uses_normal_policy(gh):
    """Processing a controller-upgrade candidate must not leak any state into
    the evaluation of an ordinary PR reconciled in the same sweep.
    """
    hunter_merge_readiness.event_name = "schedule"

    gh.pulls[251] = candidate_pr(sha="sha_upgrade")
    gh.pr_files[251] = [{"filename": "scripts/hunter_merge_readiness.py"}]
    gh.check_runs["sha_upgrade"] = green_check_runs("sha_upgrade")
    gh.statuses["sha_upgrade"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-12T10:05:00Z", "id": 1},
    ]
    hunter_merge_readiness.evaluate(251, poll=False)
    assert gh.published[-1][1] == "success"
    assert "controller-upgrade" in gh.published[-1][2]

    gh.published.clear()
    gh.pulls[300] = {
        "number": 300,
        "state": "open",
        "head": {"sha": "sha_ordinary"},
        "body": "Acceptance-criteria matrix:\n| Acceptance criterion | Status |\n| Wire up | PASS |\n- [x] `READY FOR REVIEW`",
        "draft": False,
        "user": {"login": "human"},
        "updated_at": "2026-08-12T09:00:00Z",
    }
    gh.pr_files[300] = [{"filename": "src/hunter/unrelated.py"}]
    gh.check_runs["sha_ordinary"] = green_check_runs("sha_ordinary", governance_started_at="2026-08-12T09:30:00Z")
    gh.statuses["sha_ordinary"] = [
        {"context": "Hunter Governance Review", "state": "success", "created_at": "2026-08-12T09:35:00Z", "id": 1},
    ]

    hunter_merge_readiness.evaluate(300, poll=False)

    assert gh.published[-1][1] == "success"
    assert "controller-upgrade" not in gh.published[-1][2]
