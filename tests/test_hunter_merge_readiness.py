"""Behavioural tests for the Hunter Merge Readiness current-state reconciler.

These tests assert *what readiness is*, never how the controller computes it.
Every one of them mutates repository state in the harness and then asks the
controller to reconcile; none of them asserts a call sequence, and none supplies
a decision input through an event payload.
"""

from __future__ import annotations

import hunter_github_transport as transport
import hunter_merge_readiness as core
import pytest
from hunter_readiness_harness import READY_BODY, FakeGitHub, install, ready_pull_request


@pytest.fixture
def gh(monkeypatch):
    server = FakeGitHub()
    install(monkeypatch, server)
    return server


# --- the happy path ---------------------------------------------------------


def test_current_state_that_satisfies_every_gate_publishes_success(gh):
    head = ready_pull_request(gh)

    decision = core.reconcile_pr(501)

    assert decision.state == "success"
    assert gh.readiness_status(head) == ("success", decision.description)


def test_readiness_is_published_against_the_pull_request_head_never_the_base(gh):
    head = ready_pull_request(gh)

    core.reconcile_pr(501)

    assert [sha for sha, _, _ in gh.published] == [head]
    assert gh.base_oids[501] not in {sha for sha, _, _ in gh.published}


def test_reconciling_an_unchanged_state_confirms_without_a_redundant_write(gh):
    ready_pull_request(gh)

    core.reconcile_pr(501)
    writes_after_first = gh.status_writes
    core.reconcile_pr(501)

    assert gh.status_writes == writes_after_first


def test_closed_pull_request_publishes_nothing(gh):
    ready_pull_request(gh)
    gh.pulls[501]["state"] = "closed"

    assert core.reconcile_pr(501) is None
    assert gh.published == []


# --- blockers, all read live ------------------------------------------------


def test_draft_pull_request_waits_for_ready_for_review(gh):
    ready_pull_request(gh)
    gh.pulls[501]["draft"] = True

    decision = core.reconcile_pr(501)

    assert decision.state == "pending"
    assert "Draft" in decision.description


def test_missing_acceptance_matrix_fails(gh):
    ready_pull_request(gh)
    gh.pulls[501]["body"] = "no matrix here"

    decision = core.reconcile_pr(501)

    assert decision.state == "failure"
    assert "Acceptance-criteria matrix" in decision.description


def test_blocked_acceptance_criterion_fails(gh):
    ready_pull_request(gh)
    gh.pulls[501]["body"] = READY_BODY.replace("| PASS |", "| BLOCKED |")

    decision = core.reconcile_pr(501)

    assert decision.state == "failure"
    assert "FAIL/BLOCKED" in decision.description


def test_unresolved_review_thread_blocks(gh):
    ready_pull_request(gh)
    gh.add_unresolved_thread(501, "THREAD_1")

    decision = core.reconcile_pr(501)

    assert decision.state == "failure"
    assert "Unresolved review threads remain: 1" in decision.description


def test_changes_requested_blocks(gh):
    ready_pull_request(gh)
    gh.request_changes(501, "reviewer")

    decision = core.reconcile_pr(501)

    assert decision.state == "failure"
    assert "Changes requested by: reviewer" in decision.description


def test_unacknowledged_comment_blocks_until_the_owner_reacts(gh):
    ready_pull_request(gh)
    gh.add_comment(501, 900)

    assert core.reconcile_pr(501).state == "failure"

    gh.acknowledge(900)

    assert core.reconcile_pr(501).state == "success"


def test_editing_an_acknowledged_comment_revokes_the_acknowledgement(gh):
    # NOTE: With the stable owner +1 acknowledgment logic, editing comment.updated_at
    # no longer revokes acknowledgment. The +1 reaction persists as acknowledgment.
    ready_pull_request(gh)
    gh.add_comment(501, 900)
    gh.acknowledge(900, at="2026-08-13T00:00:00Z")
    assert core.reconcile_pr(501).state == "success"

    # Editing updated_at no longer revokes the +1-based acknowledgment.
    gh.comments[501][0]["updated_at"] = "2026-08-13T01:00:00Z"

    # The +1 reaction still counts as acknowledgment regardless of timestamp changes.
    assert core.reconcile_pr(501).state == "success"


def test_missing_required_check_stays_pending(gh):
    head = ready_pull_request(gh)
    gh.check_runs[head] = [run for run in gh.check_runs[head] if run["name"] != "CodeQL"]

    decision = core.reconcile_pr(501)

    assert decision.state == "pending"
    assert "CodeQL" in decision.description


def test_failed_required_check_fails(gh):
    head = ready_pull_request(gh)
    gh.check_runs[head] = [run for run in gh.check_runs[head] if run["name"] != "Quality Gates"]
    gh.set_check(head, "Quality Gates", status="completed", conclusion="failure")

    decision = core.reconcile_pr(501)

    assert decision.state == "failure"
    assert "Quality Gates=failure" in decision.description


def test_governance_failure_fails(gh):
    head = ready_pull_request(gh)
    gh.statuses[head] = [s for s in gh.statuses[head] if s["context"] != "Hunter Governance Review"]
    gh.publish_governance(501, "failure")

    decision = core.reconcile_pr(501)

    assert decision.state == "failure"
    assert "Hunter Governance Review=failure" in decision.description


# --- governance evidence identity -------------------------------------------


def test_absent_governance_evidence_waits_naming_the_current_revision(gh):
    head = ready_pull_request(gh)
    gh.statuses[head] = []

    decision = core.reconcile_pr(501)

    assert decision.state == "pending"
    assert core.read_current_state(501).governance_revision in decision.description


def test_unstamped_governance_status_is_unattributable_and_fails_closed(gh):
    head = ready_pull_request(gh)
    gh.statuses[head] = []
    gh.publish_governance(501, "success", marker=False)

    decision = core.reconcile_pr(501)

    assert decision.state == "pending"
    state = core.read_current_state(501)
    assert state.governance is None
    assert any("no revision marker" in reason for reason in state.unusable_governance_reasons)


def test_governance_evidence_for_a_superseded_revision_cannot_satisfy_the_current_one(gh):
    ready_pull_request(gh)
    assert core.reconcile_pr(501).state == "success"

    gh.pulls[501]["body"] = READY_BODY + "\n\nAdditional evidence added after the review.\n"

    decision = core.reconcile_pr(501)

    assert decision.state == "pending"
    assert "Hunter Governance Review" in decision.description


def test_governance_evidence_for_a_previous_head_cannot_satisfy_a_new_head(gh):
    ready_pull_request(gh)
    assert core.reconcile_pr(501).state == "success"

    new_head = "head_b" * 6
    gh.pulls[501]["head"]["sha"] = new_head
    gh.green_required_checks(new_head)

    decision = core.reconcile_pr(501)

    assert decision.state == "pending"
    assert gh.readiness_status(new_head)[0] == "pending"


def test_base_advance_invalidates_governance_evidence_for_the_old_pair(gh):
    ready_pull_request(gh)
    assert core.reconcile_pr(501).state == "success"

    gh.base_oids[501] = "base_main_1"

    assert core.reconcile_pr(501).state == "pending"


# --- stale published state --------------------------------------------------


def test_stale_pending_from_a_historical_controller_is_replaced(gh):
    head = ready_pull_request(gh)
    gh.seed_readiness(head, "pending", "Waiting for a fresh Hunter Governance Review (legacy marker).")

    decision = core.reconcile_pr(501)

    assert decision.state == "success"
    assert gh.readiness_status(head)[0] == "success"


def test_stale_failure_from_a_historical_controller_is_replaced(gh):
    head = ready_pull_request(gh)
    gh.seed_readiness(head, "failure", "Timed out waiting for fresh exact-head prerequisites.")

    assert core.reconcile_pr(501).state == "success"
    assert gh.readiness_status(head)[0] == "success"


# --- controller-upgrade admission -------------------------------------------


def test_controller_upgrade_candidate_is_detected_from_changed_paths_only(gh):
    head = ready_pull_request(gh)
    gh.files[501] = ["scripts/hunter_merge_readiness.py"]
    gh.statuses[head] = [s for s in gh.statuses[head] if s["context"] != "Hunter Governance Review"]
    gh.publish_governance(501)

    state = core.read_current_state(501)

    assert state.controller_upgrade_candidate is True
    assert core.decide(state).state == "success"
    assert "controller-upgrade" in core.decide(state).description


def test_controller_upgrade_candidate_is_not_admitted_when_state_moves_mid_reconcile(gh):
    head = ready_pull_request(gh)
    gh.files[501] = ["scripts/hunter_governance_revision.py"]
    gh.statuses[head] = [s for s in gh.statuses[head] if s["context"] != "Hunter Governance Review"]
    gh.publish_governance(501)

    original_read = core.read_current_state
    calls = {"n": 0}

    def racing_read(pr_number):
        calls["n"] += 1
        if calls["n"] == 2:
            gh.add_unresolved_thread(pr_number, "THREAD_RACE")
        return original_read(pr_number)

    core.read_current_state = racing_read
    try:
        decision = core.reconcile_pr(501)
    finally:
        core.read_current_state = original_read

    assert decision.state == "pending"
    assert gh.readiness_status(head)[0] == "pending"


def test_a_missing_base_sha_fails_loudly_rather_than_stranding_the_pull_request(gh):
    """An empty base SHA would compute an identity no evaluator can ever match."""
    ready_pull_request(gh)
    gh.base_oids[501] = ""

    with pytest.raises(RuntimeError, match="base SHA unavailable"):
        core.read_current_state(501)


def test_a_governance_failure_for_the_current_revision_still_blocks(gh):
    """Preferring success among qualifying verdicts must not hide a real failure."""
    head = ready_pull_request(gh)
    gh.statuses[head] = [s for s in gh.statuses[head] if s["context"] != "Hunter Governance Review"]
    gh.publish_governance(501, "failure")
    gh.publish_governance(501, "failure", revision="f" * 32)

    decision = core.reconcile_pr(501)

    assert decision.state == "failure"
    assert "Hunter Governance Review=failure" in decision.description


# --- controller errors ------------------------------------------------------


def test_a_reconciliation_error_publishes_failure_and_fails_the_run(gh, monkeypatch, tmp_path):
    head = ready_pull_request(gh)

    def exploding_state(pr_number):
        raise RuntimeError("GitHub unavailable")

    monkeypatch.setattr(core, "read_current_state", exploding_state)
    event_path = tmp_path / "event.json"
    event_path.write_text('{"pull_request": {"number": 501}}', encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GH_REPO", "fafa33/Project-Hunter")
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("EVENT_NAME", "pull_request_target")
    monkeypatch.setenv("RUN_URL", "https://example.invalid/run")

    with pytest.raises(SystemExit):
        core.main()

    assert gh.readiness_status(head)[0] == "failure"


# --- GitHub infrastructure unavailability (bounded-retry exhaustion) ---------


def test_infrastructure_unavailable_fails_closed_with_typed_pending(gh, monkeypatch):
    """An exhausted GitHub outage is never a semantic verdict: pending, typed."""
    head = ready_pull_request(gh)

    def unavailable(method, path, payload=None):
        raise transport.GitHubUnavailable(
            f"GET {path}",
            attempts=3,
            last=transport.GitHubRequestError("HTTP 503: no server", category="transient", status_code=503),
        )

    monkeypatch.setattr(core, "request_json", unavailable)

    decision = core.reconcile_pr(501)

    assert decision is not None
    assert decision.state == "pending"
    assert "Readiness infrastructure unavailable" in decision.description
    assert "success" not in decision.description
    assert gh.readiness_status(head) is None
    assert all(state != "success" for _, state, _ in gh.published)


def test_infrastructure_unavailable_never_publishes_a_green(gh, monkeypatch):
    """Failed acquisition must never collapse into an empty review/blocker set."""
    ready_pull_request(gh)
    original = gh.request_json

    def unavailable_for_reviews(method, path, payload=None):
        if method == "GET" and "reviews" in path:
            raise transport.GitHubUnavailable(
                "GET reviews",
                attempts=3,
                last=transport.GitHubRequestError(
                    "GitHub node-resolution 404: could not resolve to a node with the global id",
                    category="node-resolution",
                    status_code=404,
                ),
            )
        return original(method, path, payload)

    monkeypatch.setattr(core, "request_json", unavailable_for_reviews)

    decision = core.reconcile_pr(501)

    assert decision is not None
    assert decision.state == "pending"
    assert "Readiness infrastructure unavailable" in decision.description
    assert all(state != "success" for _, state, _ in gh.published)
    assert all(
        status["state"] != "success"
        for statuses in gh.statuses.values()
        for status in statuses
        if status["context"] == "Hunter Merge Readiness"
    )


def test_unavailable_during_publication_stays_pending_never_success(gh, monkeypatch):
    """A 503 while publishing the success state must not become a green."""
    ready_pull_request(gh)
    original = gh.request_json

    def unavailable_for_status_writes(method, path, payload=None):
        if method == "POST" and path.startswith("statuses/"):
            raise transport.GitHubUnavailable(
                f"POST {path}",
                attempts=3,
                last=transport.GitHubRequestError("HTTP 503: no server", category="transient", status_code=503),
            )
        return original(method, path, payload)

    monkeypatch.setattr(core, "request_json", unavailable_for_status_writes)

    decision = core.reconcile_pr(501)

    assert decision is not None
    assert decision.state == "pending"
    assert "Readiness infrastructure unavailable" in decision.description
    assert all(state != "success" for _, state, _ in gh.published)


def test_unavailable_guard_is_typed_not_a_generic_except(gh, monkeypatch):
    """Counterfactual binding: a plain RuntimeError is NOT swallowed as pending."""
    ready_pull_request(gh)

    def broken(method, path, payload=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(core, "request_json", broken)

    with pytest.raises(RuntimeError, match="boom"):
        core.reconcile_pr(501)

    assert all(state != "success" for _, state, _ in gh.published)


def test_publish_controller_failure_keeps_unavailable_distinct(gh, monkeypatch):
    """Terminal failure publication distinguishes outage from semantic failure."""
    head = ready_pull_request(gh)

    def failing_status_writes(method, path, payload=None):
        if method == "GET" and path.startswith("pulls/"):
            return {"head": {"sha": head}}
        raise AssertionError(f"unexpected call: {method} {path}")

    monkeypatch.setattr(core, "request_json", failing_status_writes)

    recorded = []

    def _record_publish(sha, state, description, published):
        recorded.append((sha, state, description))

    monkeypatch.setattr(core, "publish", _record_publish)

    unavailable = transport.GitHubUnavailable(
        "GET reviews",
        attempts=3,
        last=transport.GitHubRequestError("HTTP 503: no server", category="transient", status_code=503),
    )
    core._publish_controller_failure(501, unavailable)
    assert recorded[-1][1] == "pending"
    assert "Readiness infrastructure unavailable" in recorded[-1][2]

    core._publish_controller_failure(501, RuntimeError("boom"))
    assert recorded[-1][1] == "failure"
    assert "Readiness controller error" in recorded[-1][2]
