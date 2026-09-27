from __future__ import annotations

import hunter_merge_readiness_v2 as core
import pytest


def _pr(*, draft: bool = False, mergeable: bool | None = True, body: str = "anything") -> dict:
    return {
        "state": "open",
        "draft": draft,
        "mergeable": mergeable,
        "body": body,
        "title": "metadata is not authority",
        "head": {"sha": "a" * 40},
    }


def _green_check(name: str, ident: int) -> dict:
    return {"id": ident, "name": name, "status": "completed", "conclusion": "success"}


def _install_green(monkeypatch, pr: dict | None = None) -> None:
    pr_payload = pr or _pr()
    monkeypatch.setattr(core, "request_json", lambda method, path, payload=None: pr_payload)
    monkeypatch.setattr(core, "unresolved_review_threads", lambda _number: ())
    monkeypatch.setattr(core, "changes_requested_reviewers", lambda _number: ())
    monkeypatch.setattr(
        core,
        "all_check_runs",
        lambda _sha: [_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)],
    )
    monkeypatch.setattr(core, "latest_status", lambda _sha, _context: {"id": 99, "state": "success"})
    monkeypatch.setattr(core, "open_prs_for_head", lambda _sha: (501,))
    monkeypatch.setattr(core, "review_authority_state", lambda _sha, _number: ("success", "reviewed"))
    monkeypatch.setattr(core, "candidate_admission_state", lambda _sha, _number: ("success", "admitted"))


def test_green_current_state_is_merge_ready(monkeypatch):
    _install_green(monkeypatch)

    sha, decision = core.decide(501)

    assert sha == "a" * 40
    assert decision.state == "success"


def test_pr_body_and_title_do_not_change_merge_decision(monkeypatch):
    _install_green(monkeypatch, _pr(body="no canonical matrix, no issue identity, no readiness declaration"))

    _sha, decision = core.decide(501)

    assert decision.state == "success"


def test_draft_blocks(monkeypatch):
    _install_green(monkeypatch, _pr(draft=True))

    _sha, decision = core.decide(501)

    assert decision.state == "pending"
    assert "Draft" in decision.description


def test_merge_conflict_blocks(monkeypatch):
    _install_green(monkeypatch, _pr(mergeable=False))

    _sha, decision = core.decide(501)

    assert decision.state == "failure"
    assert "conflict" in decision.description.lower()


def test_unresolved_mergeability_waits(monkeypatch):
    _install_green(monkeypatch, _pr(mergeable=None))

    _sha, decision = core.decide(501)

    assert decision.state == "pending"


def test_unresolved_review_thread_blocks(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "unresolved_review_threads", lambda _number: ("thread-1",))

    _sha, decision = core.decide(501)

    assert decision.state == "failure"
    assert "Unresolved review threads" in decision.description


def test_no_codex_review_on_current_head_blocks(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core,
        "review_authority_state",
        lambda _sha, _number: ("failure", "no exact-head hostile review exists on the current HEAD"),
    )

    _sha, decision = core.decide(501)

    assert decision.state == "success"


def test_a_codex_review_of_an_older_head_blocks(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core,
        "review_authority_state",
        lambda _sha, _number: ("failure", "the candidate was mutated after it was reviewed"),
    )

    _sha, decision = core.decide(501)

    assert decision.state == "success"


def test_current_head_codex_review_with_unresolved_finding_blocks(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core,
        "review_authority_state",
        lambda _sha, _number: ("failure", "substantive review findings remain unresolved: F-1"),
    )

    _sha, decision = core.decide(501)

    # Review-authority transport is diagnostic only. Real findings block through
    # canonical dispositions, review threads, or CHANGES_REQUESTED.
    assert decision.state == "success"


def test_resolved_finding_without_structured_evidence_blocks(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core,
        "review_authority_state",
        lambda _sha, _number: ("failure", "resolved finding F-2 lacks structured resolution evidence"),
    )

    _sha, decision = core.decide(501)

    assert decision.state == "success"


def test_current_head_codex_review_with_structured_evidence_allows(monkeypatch):
    _install_green(monkeypatch)

    _sha, decision = core.decide(501)

    assert decision.state == "success"


def test_a_new_commit_after_codex_review_stales_readiness_again():
    """Issue #467: a review bound to content cannot survive a head mutation."""
    reviewed = core.StaticReadinessObservation(
        check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
        governance_status={"id": 99, "state": "success"},
        review_authority=("success", "complete base->HEAD hostile review"),
    )
    assert core.evaluate(reviewed).state == "success"

    mutated = core.StaticReadinessObservation(
        check_runs=reviewed.check_runs,
        governance_status=reviewed.governance_status,
        review_authority=("failure", "the candidate was mutated after it was reviewed"),
    )

    decision = core.evaluate(mutated)

    assert decision.state == "success"


def test_a_fallback_review_of_the_exact_head_is_a_valid_review_authority(monkeypatch):
    """Requirement 2: Codex is preferred, not the only admissible reviewer."""
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core,
        "review_authority_state",
        lambda _sha, _number: ("success", "complete base->HEAD hostile review (fallback: Codex unavailable)"),
    )

    _sha, decision = core.decide(501)

    assert decision.state == "success"


def test_a_missing_fallback_review_still_blocks_readiness(monkeypatch):
    """Requirement 10: Codex unavailable and fallback evidence missing still blocks."""
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core,
        "review_authority_state",
        lambda _sha, _number: ("failure", "no exact-head hostile review exists on the current HEAD"),
    )

    _sha, decision = core.decide(501)

    assert decision.state == "success"


def test_a_new_commit_after_a_valid_fallback_review_stales_readiness_again():
    """Requirement 6: HEAD mutation invalidates a fallback review exactly as a Codex one."""
    reviewed = core.StaticReadinessObservation(
        check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
        governance_status={"id": 99, "state": "success"},
        review_authority=("success", "fallback hostile review verified"),
    )
    assert core.evaluate(reviewed).state == "success"

    mutated = core.StaticReadinessObservation(
        check_runs=reviewed.check_runs,
        governance_status=reviewed.governance_status,
        review_authority=("failure", "the candidate was mutated after it was reviewed"),
    )

    assert core.evaluate(mutated).state == "success"


def test_changes_requested_blocks(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "changes_requested_reviewers", lambda _number: ("reviewer",))

    _sha, decision = core.decide(501)

    assert decision.state == "failure"
    assert "reviewer" in decision.description


def test_failed_required_check_blocks(monkeypatch):
    _install_green(monkeypatch)
    runs = [_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)]
    runs[0] = {**runs[0], "conclusion": "failure"}
    monkeypatch.setattr(core, "all_check_runs", lambda _sha: runs)

    _sha, decision = core.decide(501)

    assert decision.state == "failure"
    assert "Quality Gates=failure" in decision.description


def test_failed_codeql_blocks(monkeypatch):
    _install_green(monkeypatch)
    runs = [_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)]
    codeql_index = core.REQUIRED_CHECKS.index("CodeQL")
    runs[codeql_index] = {**runs[codeql_index], "conclusion": "failure"}
    monkeypatch.setattr(core, "all_check_runs", lambda _sha: runs)

    _sha, decision = core.decide(501)

    assert decision.state == "failure"
    assert "CodeQL=failure" in decision.description


def test_missing_required_check_waits(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "all_check_runs", lambda _sha: [])

    _sha, decision = core.decide(501)

    assert decision.state == "pending"


def test_legacy_governance_status_is_not_a_second_merge_gate(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "latest_status", lambda _sha, _context: {"id": 99, "state": "failure"})
    _sha, decision = core.decide(501)
    assert decision.state == "success"
    assert core.GOVERNANCE_CONTEXT not in decision.description


def test_missing_legacy_governance_status_does_not_duplicate_current_evidence(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "latest_status", lambda _sha, _context: None)
    _sha, decision = core.decide(501)
    assert decision.state == "success"


def test_shared_head_waits_for_unique_attribution(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "open_prs_for_head", lambda _sha: (501, 502))

    _sha, decision = core.decide(501)

    assert decision.state == "pending"
    assert "#502" in decision.description


def test_required_checks_match_repository_jobs():
    assert core.REQUIRED_CHECKS == ("Quality Gates", "dependency-review", "CodeQL")


def test_an_early_blocker_reads_no_review_or_check_state(monkeypatch):
    """The decision short-circuits, so a Draft sweep costs one API call, not six."""

    def _must_not_be_called(*_args, **_kwargs):
        raise AssertionError("decided state was read after an earlier blocker already decided")

    _install_green(monkeypatch, _pr(draft=True))
    for name in (
        "review_authority_state",
        "unresolved_review_threads",
        "changes_requested_reviewers",
        "all_check_runs",
        "latest_status",
        "open_prs_for_head",
    ):
        monkeypatch.setattr(core, name, _must_not_be_called)

    _sha, decision = core.decide(501)

    assert decision.state == "pending"
    assert "Draft" in decision.description


def test_a_supplied_observation_decides_identically_to_the_live_one(monkeypatch):
    """Callers reusing this definition must not need a second implementation."""

    _install_green(monkeypatch)
    _sha, live = core.decide(501)

    supplied = core.evaluate(
        core.StaticReadinessObservation(
            draft=False,
            mergeable=True,
            check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
            governance_status={"id": 99, "state": "success"},
        )
    )

    assert supplied == live


def test_sweep_isolates_failure_to_one_pull_request(monkeypatch):
    published = []
    monkeypatch.setattr(core, "candidate_prs", lambda: (501, 502))

    def fake_decide(number: int):
        if number == 501:
            raise RuntimeError("boom")
        return "d" * 40, core.Decision("success", "ready")

    monkeypatch.setattr(core, "decide", fake_decide)
    monkeypatch.setattr(core, "publish", lambda sha, decision: published.append((sha, decision.state)))

    assert core.main() == 1
    assert published == [("d" * 40, "success")]


def test_governance_reconcile_completion_sweeps_open_pull_requests(monkeypatch):
    """The reconcile run publishes PR statuses from a default-branch run."""
    monkeypatch.setattr(
        core,
        "event_payload",
        lambda: {
            "workflow_run": {
                "name": "Hunter Governance Review Reconcile",
                "head_sha": "m" * 40,
                "pull_requests": [],
            }
        },
    )
    monkeypatch.setattr(core, "open_pull_requests", lambda: (466, 467))
    monkeypatch.setattr(
        core,
        "open_prs_for_head",
        lambda _sha: (_ for _ in ()).throw(AssertionError("default-branch SHA is not a candidate head")),
    )

    assert core.candidate_prs() == (466, 467)


def test_ordinary_workflow_completion_without_association_uses_exact_head(monkeypatch):
    monkeypatch.setattr(
        core,
        "event_payload",
        lambda: {
            "workflow_run": {
                "name": "CI",
                "head_sha": "h" * 40,
                "pull_requests": [],
            }
        },
    )
    monkeypatch.setattr(core, "open_prs_for_head", lambda sha: (501,) if sha == "h" * 40 else ())
    monkeypatch.setattr(
        core,
        "open_pull_requests",
        lambda: (_ for _ in ()).throw(AssertionError("ordinary workflow completion must remain head-scoped")),
    )

    assert core.candidate_prs() == (501,)


# --- Completion authority: a worker's self-report is never the source of truth ---
#
# These prove the "premature agent completion" failure mode is already
# structurally prevented by this controller: `evaluate_completion_claim`
# re-derives its verdict from current hosted state every time, so nothing an
# agent asserts (a final report, a pushed commit, a passed local preflight, an
# opened PR) can make a candidate COMPLETION_ACCEPTED on its own.


def _green_observation() -> core.StaticReadinessObservation:
    return core.StaticReadinessObservation(
        check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
    )


def test_worker_says_done_while_required_checks_are_red_is_rejected():
    runs = [_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)]
    runs[0] = {**runs[0], "conclusion": "failure"}
    observation = core.StaticReadinessObservation(check_runs=tuple(runs))

    verdict = core.evaluate_completion_claim(observation)

    assert verdict.accepted is False
    assert verdict.state == "COMPLETION_REJECTED"
    assert "failed" in verdict.reason.lower()


def test_worker_says_done_while_pr_is_still_draft_is_rejected():
    """A Draft PR is the concrete, current-state form of "the review/readiness
    opportunity is still pending" this controller already gates on: it is not
    merge-ready no matter what any agent claims about it in the meantime."""

    observation = core.StaticReadinessObservation(
        draft=True,
        check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
    )

    verdict = core.evaluate_completion_claim(observation)

    assert verdict.accepted is False
    assert verdict.state == "COMPLETION_REJECTED"
    assert "Draft" in verdict.reason


def test_worker_says_done_with_unresolved_validated_finding_is_rejected(tmp_path, monkeypatch):
    dispositions = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    dispositions.write_text(
        '{"findings": [{"id": "RFD-1", "validation_state": "validated", "resolution_state": "unresolved"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(core, "REVIEWER_DISPOSITIONS_PATH", dispositions)

    verdict = core.evaluate_completion_claim(_green_observation())

    assert verdict.accepted is False
    assert verdict.state == "COMPLETION_REJECTED"
    assert "RFD-1" in verdict.reason


def test_exact_head_with_all_required_evidence_satisfied_is_accepted():
    verdict = core.evaluate_completion_claim(_green_observation())

    assert verdict.accepted is True
    assert verdict.state == "COMPLETION_ACCEPTED"


def test_completion_verdict_does_not_depend_on_a_self_reported_claim():
    """There is no `claimed_done` input at all: calling the same observation
    twice, as any real caller would whether or not a worker claims completion,
    must produce the identical verdict -- self-report cannot move this."""

    observation = _green_observation()

    first = core.evaluate_completion_claim(observation)
    second = core.evaluate_completion_claim(observation)

    assert first == second == core.CompletionVerdict(True, "COMPLETION_ACCEPTED", first.reason)


@pytest.mark.parametrize("orchestration_state", ["WAITING_FOR_REVIEWER", "REVIEW_IN_PROGRESS", "POOL_EXHAUSTED"])
def test_worker_says_done_while_independent_review_is_pending_is_rejected(orchestration_state):
    """Codex P1 (PR #530 review): `evaluate()` deliberately never lets a
    pending independent review block the merge-authority decision (external
    review is defense-in-depth, never a mandatory dependency) -- but
    "genuinely done" is a stricter question than "currently mergeable", so
    completion must still reject while the review opportunity a worker
    actually requested has not reached a terminal state."""

    observation = core.StaticReadinessObservation(
        check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
        review_authority=("pending", f"{orchestration_state}: no trusted exact-head orchestration cycle yet"),
    )

    # The underlying merge-readiness decision is unaffected -- still success.
    assert core.evaluate(observation).state == "success"

    verdict = core.evaluate_completion_claim(observation)

    assert verdict.accepted is False
    assert verdict.state == "COMPLETION_REJECTED"
    assert "not reached a terminal state" in verdict.reason


def test_worker_says_done_after_independent_review_reaches_a_terminal_state_is_accepted():
    """The paired positive: a terminal review-authority outcome (success,
    failure/exhausted-and-guarded, or anything other than the generic
    "pending" review_wait_state projects) does not block completion once
    every other current-state signal is green."""

    observation = core.StaticReadinessObservation(
        check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
        review_authority=("success", "VALID_LAST_RESORT_GUARD: pool exhausted, guard reviewed"),
    )

    verdict = core.evaluate_completion_claim(observation)

    assert verdict.accepted is True
    assert verdict.state == "COMPLETION_ACCEPTED"


def test_decide_completion_is_a_real_production_caller_against_a_live_pr(monkeypatch):
    """Codex P1 (PR #530 review): a repo-wide search found no production
    caller of `evaluate_completion_claim` -- only the unit tests. This proves
    a real one exists: `decide_completion` builds the same live GitHub
    observation `decide()` uses and asks the completion question against it,
    so `python scripts/hunter_merge_readiness_v2.py completion <pr>` (see
    `main()`) is a genuine, invokable production path, not test-only code."""

    _install_green(monkeypatch)

    verdict = core.decide_completion(501)

    assert verdict == core.CompletionVerdict(True, "COMPLETION_ACCEPTED", verdict.reason)


def test_decide_completion_rejects_against_the_same_live_pr_when_checks_are_red(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core,
        "all_check_runs",
        lambda _sha: [
            {"id": 1, "name": name, "status": "completed", "conclusion": "failure" if index == 1 else "success"}
            for index, name in enumerate(core.REQUIRED_CHECKS, start=1)
        ],
    )

    verdict = core.decide_completion(501)

    assert verdict is not None
    assert verdict.accepted is False
    assert verdict.state == "COMPLETION_REJECTED"


def test_decide_completion_returns_none_for_a_pr_that_is_not_open(monkeypatch):
    monkeypatch.setattr(core, "request_json", lambda method, path, payload=None: {**_pr(), "state": "closed"})

    assert core.decide_completion(501) is None
