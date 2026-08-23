from __future__ import annotations

import hunter_merge_readiness_v2 as readiness
import hunter_workflow_state as workflow
import pytest
from hunter_workflow_state import (
    LocalEvidence,
    PullRequestObservation,
    Review,
    Verdict,
    WorkflowState,
    evaluate_workflow_state,
)

HEAD = "a" * 40


def _green_checks() -> tuple[dict[str, object], ...]:
    return tuple(
        {"id": index, "name": name, "status": "completed", "conclusion": "success"}
        for index, name in enumerate((workflow.CANONICAL_PREFLIGHT_CHECK, *readiness.REQUIRED_CHECKS), start=1)
    )


def _observation(**overrides: object) -> PullRequestObservation:
    """A PR whose current GitHub state supports every workflow state."""

    base: dict[str, object] = {
        "number": 501,
        "is_open": True,
        "head_sha": HEAD,
        "author": "author",
        "base_ref": "main",
        "changed_files": 3,
        "draft": False,
        "mergeable": True,
        "reviews": (Review(author="reviewer", state="APPROVED"),),
        "unresolved_review_threads": (),
        "changes_requested": (),
        "check_runs": _green_checks(),
        "governance_status": {"id": 99, "state": "success"},
        "shared_open_prs": (),
    }
    base.update(overrides)
    return PullRequestObservation(**base)  # type: ignore[arg-type]


def _derive(**overrides: object) -> WorkflowState:
    return evaluate_workflow_state(observation=_observation(**overrides)).derived


# --- the happy path must not be blocked -------------------------------------


def test_fully_green_pull_request_reaches_merge_ready() -> None:
    report = evaluate_workflow_state(observation=_observation(), claimed=WorkflowState.MERGE_READY)

    assert report.derived is WorkflowState.MERGE_READY
    assert report.verdict == Verdict.CONFIRMED
    assert report.blocker is None
    assert all(finding.established for finding in report.findings)


def test_every_enforced_state_is_evaluated_in_order() -> None:
    report = evaluate_workflow_state(observation=_observation())

    assert tuple(finding.state for finding in report.findings) == workflow.ENFORCED_STATES
    assert [finding.state.name for finding in report.findings] == [
        "IMPLEMENTED",
        "TESTED",
        "PREFLIGHT_PASSED",
        "PR_OPEN",
        "REVIEWED",
        "ZERO_OPEN_FINDINGS",
        "ALL_CHECKS_GREEN",
        "MERGE_READY",
    ]


# --- GITHUB STATE > AGENT CLAIMS --------------------------------------------


@pytest.mark.parametrize("claim", list(workflow.ENFORCED_STATES))
def test_a_claim_never_changes_the_derived_state(claim: WorkflowState) -> None:
    """The claim is compared with the derivation, never an input to it."""

    observation = _observation(draft=True)
    unclaimed = evaluate_workflow_state(observation=observation)
    claimed = evaluate_workflow_state(observation=observation, claimed=claim)

    assert claimed.derived is unclaimed.derived
    assert [(f.state, f.established) for f in claimed.findings] == [
        (f.state, f.established) for f in unclaimed.findings
    ]


def test_claiming_merge_ready_on_a_draft_pr_is_demoted() -> None:
    report = evaluate_workflow_state(observation=_observation(draft=True), claimed=WorkflowState.MERGE_READY)

    assert report.derived is WorkflowState.ALL_CHECKS_GREEN
    assert report.verdict == Verdict.DEMOTED
    assert report.claim_upheld is False
    assert report.blocker is not None
    assert report.blocker.state is WorkflowState.MERGE_READY
    assert "Draft" in report.blocker.detail


def test_claiming_merge_ready_with_unresolved_threads_is_demoted_at_findings() -> None:
    report = evaluate_workflow_state(
        observation=_observation(unresolved_review_threads=("thread-1", "thread-2")),
        claimed=WorkflowState.MERGE_READY,
    )

    assert report.derived is WorkflowState.REVIEWED
    assert report.verdict == Verdict.DEMOTED
    assert report.blocker is not None
    assert report.blocker.state is WorkflowState.ZERO_OPEN_FINDINGS
    assert "2 unresolved review thread(s)" in report.blocker.detail


def test_claiming_merge_ready_with_changes_requested_is_demoted() -> None:
    report = evaluate_workflow_state(
        observation=_observation(changes_requested=("reviewer",)),
        claimed=WorkflowState.MERGE_READY,
    )

    assert report.derived is WorkflowState.REVIEWED
    assert report.verdict == Verdict.DEMOTED


def test_github_may_advance_a_conservative_claim() -> None:
    report = evaluate_workflow_state(observation=_observation(), claimed=WorkflowState.PR_OPEN)

    assert report.derived is WorkflowState.MERGE_READY
    assert report.verdict == Verdict.ADVANCED
    assert report.claim_upheld is True


# --- local evidence must not launder a claim past GitHub ---------------------


def test_local_evidence_establishes_pre_pr_states_when_no_pr_exists() -> None:
    report = evaluate_workflow_state(
        observation=None,
        local_evidence=LocalEvidence(changed_files=4, pytest_passed=True, preflight_passed=True),
        claimed=WorkflowState.PREFLIGHT_PASSED,
    )

    assert report.derived is WorkflowState.PREFLIGHT_PASSED
    assert report.verdict == Verdict.CONFIRMED
    assert [f.authority for f in report.findings[:3]] == [workflow.LOCAL] * 3


def test_local_claims_alone_cannot_reach_pr_open() -> None:
    report = evaluate_workflow_state(
        observation=None,
        local_evidence=LocalEvidence(changed_files=4, pytest_passed=True, preflight_passed=True),
        claimed=WorkflowState.MERGE_READY,
    )

    assert report.derived is WorkflowState.PREFLIGHT_PASSED
    assert report.verdict == Verdict.DEMOTED
    assert report.blocker is not None
    assert report.blocker.state is WorkflowState.PR_OPEN


def test_local_pass_claim_cannot_override_a_failing_hosted_gate() -> None:
    """The central bypass: once a PR is open, local evidence is not consulted."""

    failing = tuple(
        {"id": 1, "name": name, "status": "completed", "conclusion": "failure"}
        for name in (workflow.CANONICAL_PREFLIGHT_CHECK,)
    ) + tuple(
        {"id": 2, "name": name, "status": "completed", "conclusion": "success"}
        for name in readiness.REQUIRED_CHECKS
        if name != workflow.CANONICAL_PREFLIGHT_CHECK
    )

    report = evaluate_workflow_state(
        observation=_observation(check_runs=failing),
        local_evidence=LocalEvidence(changed_files=9, pytest_passed=True, preflight_passed=True),
        claimed=WorkflowState.MERGE_READY,
    )

    assert report.derived is WorkflowState.IMPLEMENTED
    assert report.verdict == Verdict.DEMOTED
    assert report.blocker is not None
    assert report.blocker.state is WorkflowState.TESTED
    assert report.blocker.authority == workflow.GITHUB


def test_an_empty_pr_cannot_be_implemented_however_much_was_changed_locally() -> None:
    report = evaluate_workflow_state(
        observation=_observation(changed_files=0),
        local_evidence=LocalEvidence(changed_files=12, pytest_passed=True, preflight_passed=True),
        claimed=WorkflowState.IMPLEMENTED,
    )

    assert report.derived is WorkflowState.UNVERIFIED
    assert report.verdict == Verdict.DEMOTED
    assert report.findings[0].authority == workflow.GITHUB


# --- REVIEWED cannot be self-served -----------------------------------------


def test_author_self_review_does_not_establish_reviewed() -> None:
    report = evaluate_workflow_state(
        observation=_observation(reviews=(Review(author="author", state="APPROVED"),)),
        claimed=WorkflowState.REVIEWED,
    )

    assert report.derived is WorkflowState.PR_OPEN
    assert report.verdict == Verdict.DEMOTED
    assert report.blocker is not None
    assert report.blocker.state is WorkflowState.REVIEWED


def test_pending_review_draft_does_not_establish_reviewed() -> None:
    assert _derive(reviews=(Review(author="reviewer", state="PENDING"),)) is WorkflowState.PR_OPEN


def test_a_non_author_comment_review_establishes_reviewed() -> None:
    """An independent review that raised no blocking finding is still a review."""

    assert _derive(reviews=(Review(author="reviewer", state="COMMENTED"),)) is WorkflowState.MERGE_READY


# --- tests-first-red must not establish TESTED -------------------------------


def test_green_branch_preflight_does_not_substitute_for_the_required_quality_gate() -> None:
    """`Hunter Pre-PR Preflight` passes in tests-first-red mode with a red suite."""

    runs = ({"id": 1, "name": "Hunter Pre-PR Preflight", "status": "completed", "conclusion": "success"},) + tuple(
        {"id": 2, "name": name, "status": "completed", "conclusion": "success"}
        for name in readiness.REQUIRED_CHECKS
        if name != workflow.CANONICAL_PREFLIGHT_CHECK
    )

    report = evaluate_workflow_state(observation=_observation(check_runs=runs), claimed=WorkflowState.TESTED)

    assert report.derived is WorkflowState.IMPLEMENTED
    assert report.verdict == Verdict.DEMOTED


def test_a_stale_green_run_does_not_outrank_a_newer_failure() -> None:
    runs = (
        {"id": 1, "name": workflow.CANONICAL_PREFLIGHT_CHECK, "status": "completed", "conclusion": "success"},
        {"id": 2, "name": workflow.CANONICAL_PREFLIGHT_CHECK, "status": "completed", "conclusion": "failure"},
    )

    assert _derive(check_runs=runs) is WorkflowState.IMPLEMENTED


def test_a_still_running_gate_does_not_establish_tested() -> None:
    runs = ({"id": 1, "name": workflow.CANONICAL_PREFLIGHT_CHECK, "status": "in_progress", "conclusion": None},)

    assert _derive(check_runs=runs) is WorkflowState.IMPLEMENTED


# --- PR_OPEN ----------------------------------------------------------------


def test_a_closed_pr_does_not_establish_pr_open() -> None:
    assert _derive(is_open=False) is WorkflowState.UNVERIFIED


def test_a_pr_targeting_another_base_does_not_establish_pr_open() -> None:
    assert _derive(base_ref="release") is WorkflowState.PREFLIGHT_PASSED


def test_a_draft_pr_still_reaches_the_states_below_merge_ready() -> None:
    """Draft blocks merge readiness only; it does not erase real review or CI evidence."""

    assert _derive(draft=True) is WorkflowState.ALL_CHECKS_GREEN


# --- ordering ---------------------------------------------------------------


def test_derivation_stops_at_the_first_gap_even_when_later_states_hold() -> None:
    report = evaluate_workflow_state(observation=_observation(reviews=()))

    assert report.derived is WorkflowState.PR_OPEN
    established = {finding.state: finding.established for finding in report.findings}
    assert established[WorkflowState.REVIEWED] is False
    assert established[WorkflowState.ZERO_OPEN_FINDINGS] is True
    assert established[WorkflowState.ALL_CHECKS_GREEN] is True
    assert established[WorkflowState.MERGE_READY] is True


# --- one merge-readiness definition -----------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"draft": True},
        {"mergeable": False},
        {"mergeable": None},
        {"unresolved_review_threads": ("t1",)},
        {"changes_requested": ("reviewer",)},
        {"check_runs": ()},
        {"governance_status": None},
        {"governance_status": {"id": 99, "state": "failure"}},
        {"governance_status": {"id": 99, "state": "pending"}},
        {"shared_open_prs": (777,)},
    ],
)
def test_merge_ready_is_exactly_the_canonical_readiness_decision(overrides: dict[str, object]) -> None:
    observation = _observation(**overrides)
    report = evaluate_workflow_state(observation=observation)

    canonical = readiness.evaluate(observation.readiness_observation())
    established = {finding.state: finding for finding in report.findings}[WorkflowState.MERGE_READY]

    assert established.established is (canonical.state == "success")
    assert established.detail == canonical.description


def test_all_checks_green_inherits_the_canonical_stale_governance_allowance() -> None:
    """A canonically valid state must not be rejected by this evaluator."""

    observation = _observation(governance_status={"id": 99, "state": "pending"}, mergeable=True)
    report = evaluate_workflow_state(observation=observation)

    established = {finding.state: finding for finding in report.findings}
    assert established[WorkflowState.ALL_CHECKS_GREEN].established is True
    assert report.derived is WorkflowState.MERGE_READY


def test_all_checks_green_is_blind_to_review_and_draft_blockers() -> None:
    """ALL_CHECKS_GREEN reports on checks; other blockers belong to their own states."""

    observation = _observation(draft=True, unresolved_review_threads=("t1",), changes_requested=("reviewer",))
    established = {f.state: f for f in evaluate_workflow_state(observation=observation).findings}

    assert established[WorkflowState.ALL_CHECKS_GREEN].established is True
    assert established[WorkflowState.ZERO_OPEN_FINDINGS].established is False
    assert established[WorkflowState.MERGE_READY].established is False


# --- reporting surface ------------------------------------------------------


def test_report_serialisation_names_the_blocker() -> None:
    payload = evaluate_workflow_state(observation=_observation(draft=True), claimed=WorkflowState.MERGE_READY).as_dict()

    assert payload["derived_state"] == "ALL_CHECKS_GREEN"
    assert payload["claimed_state"] == "MERGE_READY"
    assert payload["verdict"] == "DEMOTED"
    assert payload["blocked_at"] == "MERGE_READY"
    assert "Draft" in payload["blocked_because"]
    assert len(payload["states"]) == len(workflow.ENFORCED_STATES)


def test_rendered_report_states_the_rejection() -> None:
    rendered = evaluate_workflow_state(observation=_observation(draft=True), claimed=WorkflowState.MERGE_READY).render()

    assert "Derived workflow state: ALL_CHECKS_GREEN" in rendered
    assert "Claim rejected" in rendered


def test_unclaimed_evaluation_is_not_a_failure() -> None:
    report = evaluate_workflow_state(observation=_observation(draft=True))

    assert report.verdict == Verdict.UNCLAIMED
    assert report.claim_upheld is True


def test_parse_state_rejects_an_unknown_state() -> None:
    assert workflow.parse_state("merge_ready") is WorkflowState.MERGE_READY
    with pytest.raises(Exception, match="unknown workflow state"):
        workflow.parse_state("READY_TO_MERGE")


def test_cli_exit_code_reflects_claim_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflow, "observe_pull_request", lambda _number: _observation(draft=True))

    assert workflow.main(["--pr", "501", "--claim", "MERGE_READY"]) == 1
    assert workflow.main(["--pr", "501", "--claim", "ALL_CHECKS_GREEN"]) == 0
    assert workflow.main(["--pr", "501"]) == 0


def test_cli_infrastructure_failure_is_not_a_claim_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unavailable(_number: int) -> PullRequestObservation:
        raise readiness.transport.GitHubUnavailable(
            "GET pulls/501",
            attempts=3,
            last=readiness.transport.GitHubRequestError("connection reset", category="network"),
        )

    monkeypatch.setattr(workflow, "observe_pull_request", _unavailable)

    assert workflow.main(["--pr", "501", "--claim", "MERGE_READY"]) == 2
