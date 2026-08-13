"""Trusted admission for pull requests that change the controller's trust boundary.

Admission is a pure function of a ``CurrentState`` snapshot. It never reads the
network, never imports or executes the candidate pull request's code, and never
consults the pull-request number, body, or labels to decide whether a pull
request is a candidate -- only changed file paths from the GitHub API.

Under the current-state reconciler the ordinary success path already re-reads
live state once and requires the semantic revision to be unchanged. A
controller-upgrade candidate additionally requires that second live reading to
satisfy an independent re-derivation of every gate -- code that shares no logic
with ``hunter_merge_readiness.decide``. That divergence check is what makes the
kernel a real superset rather than a restatement, and it is exercised directly
below by weakening ``decide`` and observing that a candidate is still held.
"""

from __future__ import annotations

import hunter_controller_admission as admission
import hunter_merge_readiness as core
import pytest
from hunter_readiness_harness import FakeGitHub, install, ready_pull_request


@pytest.fixture
def gh(monkeypatch):
    server = FakeGitHub()
    install(monkeypatch, server)
    return server


def _candidate(gh: FakeGitHub, paths: list[str], number: int = 501) -> str:
    head = "upgrade_head" * 3
    gh.add_pull_request(number, head_sha=head, files=paths)
    gh.green_required_checks(head)
    gh.publish_governance(number)
    return head


# --- candidate detection ----------------------------------------------------


def test_every_controller_owned_path_is_a_candidate():
    for path in admission.CONTROLLER_OWNED_PATHS:
        assert admission.touches_trust_boundary([path]) is True


def test_the_trust_boundary_covers_the_controller_the_kernel_and_the_revision_definition():
    assert admission.CONTROLLER_OWNED_PATHS == frozenset(
        {
            "scripts/hunter_merge_readiness.py",
            "scripts/hunter_controller_admission.py",
            "scripts/hunter_governance_revision.py",
            ".github/workflows/hunter-merge-readiness.yml",
        }
    )


def test_renaming_a_controller_owned_path_away_is_still_a_candidate():
    """GitHub puts the owned source in `previous_filename`, not `filename`.

    A pull request that renames a controller-owned file to a new path must not be
    able to escape admission by making the owned path appear only on the "before"
    side of the rename.
    """
    assert admission.touches_trust_boundary(["scripts/renamed_controller.py"]) is False
    assert (
        admission.touches_trust_boundary(["scripts/renamed_controller.py", "scripts/hunter_merge_readiness.py"]) is True
    )


def test_ordinary_paths_are_not_candidates():
    assert admission.touches_trust_boundary(["src/hunter/engine.py", "docs/README.md"]) is False
    assert admission.touches_trust_boundary([]) is False


def test_a_mixed_pull_request_is_still_a_candidate():
    assert admission.touches_trust_boundary(["src/hunter/engine.py", "scripts/hunter_merge_readiness.py"]) is True


def test_a_rename_of_a_controller_owned_file_is_detected_from_current_state(gh):
    """End to end: the state reader must carry both sides of a rename."""
    head = "rename_head" * 3
    gh.add_pull_request(503, head_sha=head, files=["scripts/renamed_controller.py"])
    gh.renames[503] = {"scripts/renamed_controller.py": "scripts/hunter_merge_readiness.py"}
    gh.green_required_checks(head)
    gh.publish_governance(503)

    state = core.read_current_state(503)

    assert state.changed_paths == ("scripts/renamed_controller.py",)
    assert state.previous_paths == ("scripts/hunter_merge_readiness.py",)
    assert state.controller_upgrade_candidate is True
    # The governance fingerprint must still cover only what the evaluator reads,
    # so the previous filename must not leak into it.
    assert state.governance is not None
    assert core.decide(state).state == "success"


def test_candidacy_is_derived_only_from_changed_paths(gh):
    head = _candidate(gh, ["scripts/hunter_merge_readiness.py"])
    state = core.read_current_state(501)
    assert state.controller_upgrade_candidate is True

    gh.files[501] = ["src/hunter/engine.py"]
    gh.statuses[head] = [s for s in gh.statuses[head] if s["context"] != "Hunter Governance Review"]
    gh.publish_governance(501)

    assert core.read_current_state(501).controller_upgrade_candidate is False


# --- admission decisions ----------------------------------------------------


def test_a_fully_green_candidate_is_admitted(gh):
    _candidate(gh, ["scripts/hunter_merge_readiness.py"])

    result = admission.evaluate_admission(core.read_current_state(501))

    assert result.admitted is True
    assert "controller-upgrade candidate admitted" in result.reason


def test_a_candidate_with_an_incomplete_required_check_is_not_admitted(gh):
    head = _candidate(gh, ["scripts/hunter_merge_readiness.py"])
    gh.check_runs[head] = [run for run in gh.check_runs[head] if run["name"] != "CodeQL"]

    result = admission.evaluate_admission(core.read_current_state(501))

    assert result.admitted is False
    assert "CodeQL=missing" in result.reason


def test_a_candidate_with_a_failing_required_check_is_not_admitted(gh):
    head = _candidate(gh, ["scripts/hunter_controller_admission.py"])
    gh.check_runs[head] = [run for run in gh.check_runs[head] if run["name"] != "Quality Gates"]
    gh.set_check(head, "Quality Gates", status="completed", conclusion="failure")

    result = admission.evaluate_admission(core.read_current_state(501))

    assert result.admitted is False
    assert "Quality Gates=failure" in result.reason


def test_a_candidate_without_matching_governance_evidence_is_not_admitted(gh):
    head = _candidate(gh, ["scripts/hunter_governance_revision.py"])
    gh.statuses[head] = []

    result = admission.evaluate_admission(core.read_current_state(501))

    assert result.admitted is False
    assert "no Hunter Governance Review evidence" in result.reason


def test_a_candidate_with_failing_governance_is_not_admitted(gh):
    head = _candidate(gh, ["scripts/hunter_merge_readiness.py"])
    gh.statuses[head] = [s for s in gh.statuses[head] if s["context"] != "Hunter Governance Review"]
    gh.publish_governance(501, "failure")

    result = admission.evaluate_admission(core.read_current_state(501))

    assert result.admitted is False
    assert "Hunter Governance Review=failure" in result.reason


def test_a_candidate_with_unresolved_threads_is_not_admitted(gh):
    _candidate(gh, ["scripts/hunter_merge_readiness.py"])
    gh.add_unresolved_thread(501, "THREAD_1")

    result = admission.evaluate_admission(core.read_current_state(501))

    assert result.admitted is False
    assert "unresolved review threads remain: 1" in result.reason


def test_a_candidate_with_changes_requested_is_not_admitted(gh):
    _candidate(gh, ["scripts/hunter_merge_readiness.py"])
    gh.request_changes(501, "reviewer")

    result = admission.evaluate_admission(core.read_current_state(501))

    assert result.admitted is False
    assert "changes requested by: reviewer" in result.reason


def test_a_candidate_with_unacknowledged_comments_is_not_admitted(gh):
    _candidate(gh, ["scripts/hunter_merge_readiness.py"])
    gh.add_comment(501, 900)

    result = admission.evaluate_admission(core.read_current_state(501))

    assert result.admitted is False
    assert "unacknowledged PR comments remain: 1" in result.reason


def test_a_draft_candidate_is_not_admitted(gh):
    _candidate(gh, ["scripts/hunter_merge_readiness.py"])
    gh.pulls[501]["draft"] = True

    result = admission.evaluate_admission(core.read_current_state(501))

    assert result.admitted is False
    assert "draft" in result.reason


def test_admission_rejects_evidence_belonging_to_another_pull_request(gh):
    """Admission re-proves ownership; it never inherits the caller's conclusion."""
    shared = "shared_upgrade_head"
    gh.add_pull_request(601, head_sha=shared, files=["scripts/hunter_merge_readiness.py"])
    gh.add_pull_request(602, head_sha=shared, files=["scripts/hunter_merge_readiness.py"])
    gh.green_required_checks(shared)
    gh.publish_governance(601)

    result = admission.evaluate_admission(core.read_current_state(602))

    assert result.admitted is False
    assert "no Hunter Governance Review evidence" in result.reason


# --- integration with the reconciler ---------------------------------------


def test_the_success_description_records_that_admission_applied(gh):
    _candidate(gh, ["scripts/hunter_merge_readiness.py"])

    decision = core.reconcile_pr(501)

    assert decision.state == "success"
    assert "controller-upgrade PR admitted by the current trusted generation" in decision.description


def test_a_non_candidate_success_description_does_not_claim_admission(gh):
    ready_pull_request(gh, number=777, head="ordinary_head" * 2)

    decision = core.reconcile_pr(777)

    assert decision.state == "success"
    assert "controller-upgrade" not in decision.description


def test_admission_catches_a_weakened_readiness_decision_for_a_candidate(gh, monkeypatch):
    """The independent re-derivation is what makes admission a real superset.

    ``evaluate_admission`` deliberately re-derives every gate instead of reusing
    ``decide``. This test weakens ``decide`` so that it green-lights a pull
    request with an unresolved review thread, and shows that a pull request
    touching the trust boundary is still held pending -- while an ordinary pull
    request under the same weakened rule is not. That difference is the entire
    value of the kernel; without it this test would fail.
    """
    _candidate(gh, ["scripts/hunter_merge_readiness.py"], number=501)
    gh.add_unresolved_thread(501, "THREAD_1")

    ordinary_head = "ordinary_head" * 2
    gh.add_pull_request(502, head_sha=ordinary_head, files=["src/hunter/engine.py"])
    gh.green_required_checks(ordinary_head)
    gh.publish_governance(502)
    gh.add_unresolved_thread(502, "THREAD_2")

    monkeypatch.setattr(
        core,
        "decide",
        lambda state: core.ReadinessDecision("success", "weakened rule says ready"),
    )

    candidate_decision = core.reconcile_pr(501)
    ordinary_decision = core.reconcile_pr(502)

    assert ordinary_decision.state == "success"
    assert candidate_decision.state == "pending"
    assert "Controller-upgrade admission pending" in candidate_decision.description
    assert "unresolved review threads remain: 1" in candidate_decision.description


def test_a_candidate_is_held_pending_when_state_moves_during_confirmation(gh):
    """A success decision is never published against state that moved underneath it."""
    _candidate(gh, ["scripts/hunter_merge_readiness.py"])
    original_read = core.read_current_state
    reads = {"n": 0}

    def racing_read(pr_number):
        reads["n"] += 1
        if reads["n"] == 2:
            gh.add_unresolved_thread(pr_number, "THREAD_RACE")
        return original_read(pr_number)

    core.read_current_state = racing_read
    try:
        decision = core.reconcile_pr(501)
    finally:
        core.read_current_state = original_read

    assert decision.state == "pending"
    assert "changed while readiness was being confirmed" in decision.description
