"""Behavioural tests for the Hunter Merge Readiness current-state reconciler.

These tests assert *what readiness is*, never how the controller computes it.
Every one of them mutates repository state in the harness and then asks the
controller to reconcile; none of them asserts a call sequence, and none supplies
a decision input through an event payload.
"""

from __future__ import annotations

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
    ready_pull_request(gh)
    gh.add_comment(501, 900)
    gh.acknowledge(900, at="2026-08-13T00:00:00Z")
    assert core.reconcile_pr(501).state == "success"

    gh.comments[501][0]["updated_at"] = "2026-08-13T01:00:00Z"

    assert core.reconcile_pr(501).state == "failure"


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
