from __future__ import annotations

import dataclasses
import json
from pathlib import Path

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


# --- proportional review ----------------------------------------------------


def test_review_is_required_by_default() -> None:
    """The fail-closed direction is the default."""

    assert evaluate_workflow_state(observation=_observation(reviews=())).derived is WorkflowState.PR_OPEN


def test_a_declared_waiver_lets_an_unreviewed_low_risk_change_reach_merge_ready() -> None:
    """docs/DEVELOPMENT_GOVERNANCE.md requires review for substantive changes only.

    Forcing every green cleanup to stop at PR_OPEN would reject a canonically
    valid MERGE_READY.
    """

    report = evaluate_workflow_state(
        observation=_observation(reviews=()),
        review_required=False,
        claimed=WorkflowState.MERGE_READY,
    )

    assert report.derived is WorkflowState.MERGE_READY
    assert report.verdict == Verdict.CONFIRMED
    reviewed = {finding.state: finding for finding in report.findings}[WorkflowState.REVIEWED]
    assert reviewed.authority == workflow.DECLARED
    assert "does not require one" in reviewed.detail


def test_a_waiver_never_hides_a_review_that_happened() -> None:
    report = evaluate_workflow_state(observation=_observation(), review_required=False)

    reviewed = {finding.state: finding for finding in report.findings}[WorkflowState.REVIEWED]
    assert reviewed.authority == workflow.GITHUB
    assert "reviewer" in reviewed.detail


def test_a_waiver_does_not_reach_any_other_state() -> None:
    """It excuses a missing review; GitHub still decides everything else."""

    with_threads = evaluate_workflow_state(
        observation=_observation(reviews=(), unresolved_review_threads=("t1",)),
        review_required=False,
        claimed=WorkflowState.MERGE_READY,
    )
    assert with_threads.derived is WorkflowState.REVIEWED
    assert with_threads.verdict == Verdict.DEMOTED

    with_changes_requested = evaluate_workflow_state(
        observation=_observation(reviews=(), changes_requested=("reviewer",)),
        review_required=False,
        claimed=WorkflowState.MERGE_READY,
    )
    assert with_changes_requested.derived is WorkflowState.REVIEWED

    with_red_ci = evaluate_workflow_state(
        observation=_observation(reviews=(), check_runs=()),
        review_required=False,
        claimed=WorkflowState.MERGE_READY,
    )
    assert with_red_ci.derived is WorkflowState.IMPLEMENTED

    draft = evaluate_workflow_state(
        observation=_observation(reviews=(), draft=True),
        review_required=False,
        claimed=WorkflowState.MERGE_READY,
    )
    assert draft.derived is WorkflowState.ALL_CHECKS_GREEN


# --- starting-scope gate ----------------------------------------------------
#
# The failure this gate exists to catch: an agent assigned one task works on a
# different one. PR #309 is the concrete instance -- the assignment was the
# workflow-state MVP, the agent produced an Issue #196 evidence-semantics
# follow-up on an unrelated branch.

ASSIGNED_BASE = "b" * 40


def _contract(**overrides: object) -> workflow.TaskScopeContract:
    base: dict[str, object] = {
        "task_id": "agent-workflow-state-enforcement-mvp",
        "branch_pattern": "governance/agent-workflow-state-*",
        "base_ref": "main",
        "base_sha": ASSIGNED_BASE,
        "allowed_paths": (
            "scripts/hunter_workflow_state.py",
            "scripts/hunter_merge_readiness_v2.py",
            "tests/test_hunter_workflow_state.py",
            "tests/test_hunter_merge_readiness_v2.py",
            "docs/AGENT_WORKFLOW_STATE_ENFORCEMENT.md",
        ),
        "prohibited_paths": ("src/", "alembic/", ".github/workflows/"),
    }
    base.update(overrides)
    return workflow.TaskScopeContract(**base)  # type: ignore[arg-type]


def _in_scope_observation(**overrides: object) -> PullRequestObservation:
    defaults: dict[str, object] = {
        "head_ref": "governance/agent-workflow-state-enforcement-mvp",
        "base_sha": ASSIGNED_BASE,
        "changed_paths": ("scripts/hunter_workflow_state.py", "tests/test_hunter_workflow_state.py"),
    }
    defaults.update(overrides)
    return _observation(**defaults)


def _scope_report(
    *,
    contract: workflow.TaskScopeContract | None = None,
    declared_task_id: str = "",
    claimed: WorkflowState | None = WorkflowState.IMPLEMENTED,
    observation: PullRequestObservation | None = None,
    **overrides: object,
) -> workflow.WorkflowStateReport:
    return evaluate_workflow_state(
        observation=_in_scope_observation(**overrides) if observation is None else observation,
        scope_contract=_contract() if contract is None else contract,
        declared_task_id=declared_task_id,
        claimed=claimed,
    )


def _assert_in_scope(report: workflow.WorkflowStateReport) -> None:
    """IMPLEMENTED is established by the scope gate, so derivation may continue."""

    implemented = report.findings[0]
    assert implemented.state is WorkflowState.IMPLEMENTED
    assert implemented.established is True
    assert implemented.authority == workflow.SCOPE
    assert report.derived >= WorkflowState.IMPLEMENTED


def _assert_scope_mismatch(report: workflow.WorkflowStateReport, *, because: str) -> None:
    assert report.derived is WorkflowState.UNVERIFIED
    assert report.verdict == Verdict.DEMOTED
    blocker = report.blocker
    assert blocker is not None
    assert blocker.state is WorkflowState.IMPLEMENTED
    assert blocker.authority == workflow.SCOPE
    assert blocker.detail.startswith(workflow.SCOPE_MISMATCH)
    assert because in blocker.detail


# 1. exact valid task contract + valid changes => IMPLEMENTED may advance


def test_matching_scope_allows_implemented_to_advance() -> None:
    report = _scope_report()

    _assert_in_scope(report)
    assert report.claim_upheld is True
    assert "in scope for task" in report.findings[0].detail


# 2. wrong branch => rejected


def test_wrong_branch_is_a_scope_mismatch() -> None:
    _assert_scope_mismatch(
        _scope_report(head_ref="claude/policy-correction-semantics-pr308-ngd28p"),
        because="does not match the assigned pattern",
    )


# 3. prohibited path => rejected


def test_prohibited_path_is_a_scope_mismatch() -> None:
    _assert_scope_mismatch(
        _scope_report(changed_paths=("scripts/hunter_workflow_state.py", "src/hunter/evidence_assembly/semantics.py")),
        because="prohibited path(s) changed: src/hunter/evidence_assembly/semantics.py",
    )


# 4. outside allowed path => rejected


def test_path_outside_the_assigned_scope_is_a_scope_mismatch() -> None:
    _assert_scope_mismatch(
        _scope_report(changed_paths=("scripts/hunter_workflow_state.py", "docs/HUNTER_ROADMAP.md")),
        because="outside the assigned scope changed: docs/HUNTER_ROADMAP.md",
    )


# 5. wrong task/issue => rejected


def test_working_a_different_task_is_a_scope_mismatch() -> None:
    _assert_scope_mismatch(
        _scope_report(declared_task_id="issue-196-evidence-semantics"),
        because="but the assigned task is",
    )


# 6. stale/wrong base => rejected


def test_wrong_base_commit_is_a_scope_mismatch() -> None:
    _assert_scope_mismatch(_scope_report(base_sha="c" * 40), because="is not the assigned base")


def test_wrong_base_branch_is_a_scope_mismatch() -> None:
    _assert_scope_mismatch(
        _scope_report(observation=_in_scope_observation(base_ref="release")),
        because="is not the assigned base 'main'",
    )


# 7. missing required contract evidence => rejected (fail closed)


@pytest.mark.parametrize(
    ("overrides", "because"),
    [
        ({"task_id": ""}, "missing task_id"),
        ({"branch_pattern": ""}, "missing branch_pattern"),
        ({"base_ref": ""}, "missing base_ref"),
        ({"allowed_paths": ()}, "declares no allowed_paths"),
    ],
)
def test_an_incomplete_contract_fails_closed(overrides: dict[str, object], because: str) -> None:
    _assert_scope_mismatch(_scope_report(contract=_contract(**overrides)), because=because)


@pytest.mark.parametrize(
    ("overrides", "because"),
    [
        ({"changed_paths": ()}, "no changed paths"),
        ({"head_ref": ""}, "no head branch"),
    ],
)
def test_missing_scope_evidence_fails_closed(overrides: dict[str, object], because: str) -> None:
    _assert_scope_mismatch(_scope_report(**overrides), because=because)


def test_a_contract_with_no_pull_request_to_check_fails_closed() -> None:
    report = evaluate_workflow_state(
        observation=None,
        local_evidence=LocalEvidence(changed_files=9, pytest_passed=True, preflight_passed=True),
        scope_contract=_contract(),
        claimed=WorkflowState.PREFLIGHT_PASSED,
    )

    _assert_scope_mismatch(report, because="no open pull request to check")


def test_an_unreadable_contract_field_is_refused_rather_than_partly_enforced() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        workflow.TaskScopeContract.from_dict(
            {
                "task_id": "t",
                "branch_pattern": "b*",
                "allowed_paths": ["scripts/"],
                "alowed_paths": ["everything/"],
            }
        )


# 8. agent says "correct scope" but evidence disagrees => rejected


def test_an_agent_declaring_the_assigned_task_cannot_waive_contradicting_evidence() -> None:
    """Naming the right task does not make the work match it."""

    _assert_scope_mismatch(
        _scope_report(
            declared_task_id="agent-workflow-state-enforcement-mvp",
            head_ref="claude/policy-correction-semantics-pr308-ngd28p",
            changed_paths=("tests/test_evidence_semantics_authority.py",),
        ),
        because="does not match the assigned pattern",
    )


def test_declining_to_state_a_task_does_not_evade_the_gate() -> None:
    """The task check is the only declaration-based one, so omitting it must grant nothing.

    Every other comparison is evidence-derived, and those are what caught the
    PR #309 shape independently of anything the agent said.
    """

    _assert_scope_mismatch(
        _scope_report(
            declared_task_id="",
            head_ref="claude/policy-correction-semantics-pr308-ngd28p",
            changed_paths=("tests/test_evidence_semantics_authority.py", "docs/DEFECT_REGISTRY.json"),
        ),
        because="does not match the assigned pattern",
    )

    # ...and with a branch that happens to match, the paths still bind.
    _assert_scope_mismatch(
        _scope_report(
            declared_task_id="",
            changed_paths=("tests/test_evidence_semantics_authority.py",),
        ),
        because="outside the assigned scope",
    )


def test_an_empty_allowed_path_entry_matches_nothing_rather_than_everything() -> None:
    _assert_scope_mismatch(
        _scope_report(contract=_contract(allowed_paths=("",))),
        because="outside the assigned scope",
    )


@pytest.mark.parametrize("claim", list(workflow.ENFORCED_STATES))
def test_no_workflow_claim_can_lift_a_scope_mismatch(claim: WorkflowState) -> None:
    report = _scope_report(claimed=claim, head_ref="some/other-branch")

    assert report.derived is WorkflowState.UNVERIFIED
    assert report.verdict == Verdict.DEMOTED


# 9. PR body claims correct task but evidence disagrees => rejected


def test_pull_request_prose_is_not_an_input_to_scope() -> None:
    """The observation has nowhere to carry a title or body, so prose cannot authorize scope."""

    carried = {item.name for item in dataclasses.fields(PullRequestObservation)}
    assert carried.isdisjoint({"title", "body", "description", "commit_messages", "comments"})


def test_a_pull_request_describing_the_right_task_is_still_rejected_on_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = "This PR implements the agent-workflow-state-enforcement-mvp exactly as assigned."
    payload = _pr_payload(
        body=body,
        title="feat(governance): agent workflow state enforcement MVP",
        head={"sha": HEAD, "ref": "claude/policy-correction-semantics-pr308-ngd28p"},
        base={"ref": "main", "sha": ASSIGNED_BASE},
    )
    _install_github(monkeypatch, payload, files=[{"filename": "tests/test_evidence_semantics_authority.py"}])

    observed = workflow.observe_pull_request(501)

    assert observed is not None
    report = evaluate_workflow_state(
        observation=observed, scope_contract=_contract(), claimed=WorkflowState.IMPLEMENTED
    )
    _assert_scope_mismatch(report, because="does not match the assigned pattern")


# 10. the exact PR #309-style mismatch


def test_the_pr_309_mismatch_is_mechanically_rejected() -> None:
    """Assigned the workflow-state MVP; produced an Issue #196 evidence-semantics follow-up."""

    report = _scope_report(
        declared_task_id="issue-196-evidence-semantics",
        head_ref="claude/policy-correction-semantics-pr308-ngd28p",
        changed_paths=("tests/test_evidence_semantics_authority.py", "docs/DEFECT_REGISTRY.json"),
        claimed=WorkflowState.MERGE_READY,
    )

    _assert_scope_mismatch(report, because="but the assigned task is")

    # Every later state is unreachable: the ordered derivation stops before
    # IMPLEMENTED, so a MERGE_READY claim is rejected even though this PR's own
    # checks, reviews and mergeability would otherwise support it.
    assert report.derived is WorkflowState.UNVERIFIED
    assert report.claim_upheld is False
    assert {finding.state for finding in report.findings if finding.established}.issubset(
        set(workflow.ENFORCED_STATES) - {WorkflowState.IMPLEMENTED}
    )


# 11. valid scope preserves the existing behaviour of every later state


def test_valid_scope_preserves_existing_merge_ready_behaviour() -> None:
    observation = _in_scope_observation()

    with_contract = evaluate_workflow_state(
        observation=observation, scope_contract=_contract(), claimed=WorkflowState.MERGE_READY
    )
    without_contract = evaluate_workflow_state(observation=observation, claimed=WorkflowState.MERGE_READY)

    assert with_contract.derived is WorkflowState.MERGE_READY
    assert with_contract.verdict == Verdict.CONFIRMED
    assert without_contract.derived is WorkflowState.MERGE_READY
    # Only IMPLEMENTED's authority differs; every later finding is identical.
    assert [(f.state, f.established) for f in with_contract.findings] == [
        (f.state, f.established) for f in without_contract.findings
    ]
    assert [f.detail for f in with_contract.findings[1:]] == [f.detail for f in without_contract.findings[1:]]


def test_no_contract_leaves_the_gate_disengaged() -> None:
    """Existing callers keep their behaviour exactly."""

    observation = _observation()
    assert evaluate_workflow_state(observation=observation).derived is WorkflowState.MERGE_READY
    assert evaluate_workflow_state(observation=observation).findings[0].authority == workflow.GITHUB


def test_an_in_scope_but_empty_pull_request_is_still_not_implemented() -> None:
    """Matching the assignment is necessary, not sufficient."""

    report = _scope_report(observation=_in_scope_observation(changed_files=0))

    assert report.derived is WorkflowState.UNVERIFIED
    blocker = report.blocker
    assert blocker is not None
    assert "no changed files" in blocker.detail


def test_scope_entries_match_by_directory_prefix_and_glob() -> None:
    contract = _contract(allowed_paths=("scripts/", "tests/test_hunter_*.py"), prohibited_paths=("scripts/secret/",))

    _assert_in_scope(
        _scope_report(
            contract=contract,
            changed_paths=("scripts/hunter_workflow_state.py", "tests/test_hunter_workflow_state.py"),
        )
    )

    _assert_scope_mismatch(
        _scope_report(contract=contract, changed_paths=("scripts/secret/keys.py",)),
        because="prohibited path(s) changed",
    )
    _assert_scope_mismatch(
        _scope_report(contract=contract, changed_paths=("tests/test_other_thing.py",)),
        because="outside the assigned scope",
    )


def test_cli_loads_the_contract_and_reports_the_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    contract_path = tmp_path / "scope.json"
    contract_path.write_text(
        json.dumps(
            {
                "task_id": "agent-workflow-state-enforcement-mvp",
                "branch_pattern": "governance/agent-workflow-state-*",
                "base_ref": "main",
                "allowed_paths": ["scripts/hunter_workflow_state.py"],
                "prohibited_paths": ["src/"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        workflow,
        "observe_pull_request",
        lambda _number: _in_scope_observation(head_ref="claude/policy-correction-semantics-pr308-ngd28p"),
    )

    exit_code = workflow.main(["--pr", "501", "--claim", "MERGE_READY", "--scope-contract", str(contract_path)])

    assert exit_code == 1
    assert workflow.SCOPE_MISMATCH in capsys.readouterr().out


# --- the parsing boundary ---------------------------------------------------
#
# observe_pull_request maps GitHub's payloads into the observation every state is
# derived from. A mapping regression here would change every derived state
# without failing any of the tests above, all of which construct the observation
# directly.


def _install_github(monkeypatch: pytest.MonkeyPatch, pr: dict[str, object], **overrides: object) -> None:
    monkeypatch.setattr(workflow.readiness, "request_json", lambda _method, _path: pr)

    reviews = overrides.get("paged", [])
    files = overrides.get("files", [])

    def _paged(path: str) -> object:
        return files if path.endswith("/files") else reviews

    monkeypatch.setattr(workflow.readiness, "paged", _paged)
    for name, value in (
        ("unresolved_review_threads", overrides.get("threads", ())),
        ("changes_requested_reviewers", overrides.get("changes_requested", ())),
        ("all_check_runs", overrides.get("check_runs", [])),
        ("latest_status", overrides.get("governance_status", None)),
        ("open_prs_for_head", overrides.get("open_prs", ())),
    ):
        monkeypatch.setattr(workflow.readiness, name, lambda *_a, _v=value, **_k: _v)


def _pr_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "number": 501,
        "state": "open",
        "draft": False,
        "mergeable": True,
        "changed_files": 4,
        "head": {"sha": HEAD},
        "base": {"ref": "main"},
        "user": {"login": "author"},
    }
    payload.update(overrides)
    return payload


def test_observation_maps_the_current_pull_request(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_github(
        monkeypatch,
        _pr_payload(),
        paged=[{"user": {"login": "reviewer"}, "state": "approved"}],
        threads=("t1",),
        changes_requested=("blocker",),
        check_runs=[{"id": 1, "name": "Quality Gates", "status": "completed", "conclusion": "success"}],
        governance_status={"id": 9, "state": "success"},
        open_prs=(501, 777),
    )

    observed = workflow.observe_pull_request(501)

    assert observed is not None
    assert (observed.number, observed.is_open, observed.head_sha) == (501, True, HEAD)
    assert (observed.author, observed.base_ref) == ("author", "main")
    assert (observed.changed_files, observed.draft, observed.mergeable) == (4, False, True)
    assert observed.reviews == (Review(author="reviewer", state="APPROVED"),)
    assert observed.unresolved_review_threads == ("t1",)
    assert observed.changes_requested == ("blocker",)
    assert observed.governance_status == {"id": 9, "state": "success"}
    assert len(observed.check_runs) == 1
    # The evaluated PR is not a competing claimant on its own head.
    assert observed.shared_open_prs == (777,)


def test_a_missing_pull_request_is_not_an_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflow.readiness, "request_json", lambda _method, _path: None)
    assert workflow.observe_pull_request(501) is None

    monkeypatch.setattr(workflow.readiness, "request_json", lambda _method, _path: {"message": "Not Found"})
    assert workflow.observe_pull_request(501) is None


@pytest.mark.parametrize(
    "overrides",
    [{"state": "closed"}, {"head": {"sha": ""}}, {"head": {}}],
    ids=["closed", "empty-head-sha", "no-head-sha"],
)
def test_a_closed_or_headless_pull_request_reads_no_further_state(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object]
) -> None:
    def _unexpected(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("state beyond the PR itself was read for a closed or headless PR")

    monkeypatch.setattr(workflow.readiness, "request_json", lambda _method, _path: _pr_payload(**overrides))
    for name in (
        "paged",
        "unresolved_review_threads",
        "changes_requested_reviewers",
        "all_check_runs",
        "latest_status",
        "open_prs_for_head",
    ):
        monkeypatch.setattr(workflow.readiness, name, _unexpected)

    observed = workflow.observe_pull_request(501)

    assert observed is not None
    assert observed.is_open is False
    assert evaluate_workflow_state(observation=observed).derived is WorkflowState.UNVERIFIED


def test_review_parsing_drops_unusable_entries_and_normalises_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workflow.readiness,
        "paged",
        lambda _path: [
            {"user": {"login": "reviewer"}, "state": "changes_requested"},
            {"user": {"login": ""}, "state": "APPROVED"},
            {"user": {}, "state": "APPROVED"},
            {"user": {"login": "nostate"}, "state": ""},
            "not-a-review",
        ],
    )

    assert workflow.submitted_reviews(501) == (Review(author="reviewer", state="CHANGES_REQUESTED"),)


# --- CLI --------------------------------------------------------------------


def test_local_only_evaluation_needs_no_pr_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before the first PR there is no PR number to supply, and nothing to read."""

    def _must_not_be_called(_number: int) -> PullRequestObservation:
        raise AssertionError("GitHub was read for a local-only evaluation")

    monkeypatch.setattr(workflow, "observe_pull_request", _must_not_be_called)

    assert (
        workflow.main(
            ["--changed-files", "3", "--local-tests-passed", "--local-preflight-passed", "--claim", "PREFLIGHT_PASSED"]
        )
        == 0
    )
    assert workflow.main(["--changed-files", "3", "--claim", "PR_OPEN"]) == 1


def test_cli_review_waiver_reaches_the_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflow, "observe_pull_request", lambda _number: _observation(reviews=()))

    assert workflow.main(["--pr", "501", "--claim", "MERGE_READY"]) == 1
    assert workflow.main(["--pr", "501", "--claim", "MERGE_READY", "--review-not-required"]) == 0


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


def test_an_evaluation_failure_is_not_reported_as_a_demoted_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit 1 means the claim was rejected. A crash must not borrow that meaning."""

    def _boom(**_kwargs: object) -> object:
        raise RuntimeError("evaluator defect")

    monkeypatch.setattr(workflow, "observe_pull_request", lambda _number: _observation())
    monkeypatch.setattr(workflow, "evaluate_workflow_state", _boom)

    assert workflow.main(["--pr", "501", "--claim", "MERGE_READY"]) == 2


def test_a_rendering_failure_is_not_reported_as_a_demoted_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    original = workflow.WorkflowStateReport.render

    def _boom(self: workflow.WorkflowStateReport) -> str:
        raise RuntimeError("rendering defect")

    monkeypatch.setattr(workflow, "observe_pull_request", lambda _number: _observation())
    monkeypatch.setattr(workflow.WorkflowStateReport, "render", _boom)
    try:
        assert workflow.main(["--pr", "501", "--claim", "MERGE_READY"]) == 2
    finally:
        monkeypatch.setattr(workflow.WorkflowStateReport, "render", original)
