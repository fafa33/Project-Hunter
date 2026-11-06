"""ADR 0041 F1/F2/F4: the single governed fallback on the signed Issue ledger (adversarial and regression)."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import pytest
from test_issue_agent_state import (
    AUTH,
    CONTROL,
    HANDOFF,
    Chain,
    _artifact,
    _by,
    authorized_evidence,
    reject,
    result_bound_evidence,
    trusted,
    validated_evidence,
)

from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_state import UNKNOWN, ArtifactFact, Facts, LedgerCorruptError

FALLBACK_ID = state.execution_identity(
    authorization_id=AUTH, authorize_run_id=100, control_sha=CONTROL, handoff_sha256=HANDOFF, execution_attempt=2
)
NOW = "2026-10-04T11:00:00Z"


def offered_evidence() -> dict[str, Any]:
    evidence = authorized_evidence()
    second = _artifact(12)
    second["recipient_key_id"] = "3" * 64
    evidence["fallback"] = {"execution_id": FALLBACK_ID, "handoff_artifact": second}
    return evidence


def admitted_evidence(**changes: Any) -> dict[str, Any]:
    evidence = {
        "primary_executor_job_id": 5,
        "primary_executor_conclusion": "success",
        "primary_cause": "RATE_LIMIT",
        "stderr_category": "RATE_LIMIT",
        "probe_category": "RATE_LIMIT",
        "primary_result_artifacts": 0,
        "fallback_executor": "codex",
        "fallback_execution_id": FALLBACK_ID,
    }
    evidence.update(changes)
    return evidence


def fallback_bound_evidence() -> dict[str, Any]:
    return {
        **result_bound_evidence(),
        "fallback": {
            "execution_attempt": 2,
            "executor": "codex",
            "attestation_sha256": "ab" * 32,
            "deadline_published_at": "2026-12-01T00:00:00Z",
        },
    }


def offered() -> Chain:
    return Chain().transition(state.AUTHORIZED, offered_evidence(), "authorize")


def admitted() -> Chain:
    return offered().transition(state.FALLBACK_AUTHORIZED, admitted_evidence(), "bind")


NONCE = "ab" * 32
APPROVED_RUN = 101


def request(chain: Chain, attempt: int = 1, nonce: str = NONCE) -> Chain:
    """The owner's approval: a nonce-bound resume request for the fallback stage (ADR 0041 F1)."""

    evidence = {"stage": "fallback", "nonce": nonce, "attempt": attempt, "dispatched_at": "2026-10-04T10:30:00Z"}
    return chain.add(chain.make("resume_requested", state.FALLBACK_AUTHORIZED, evidence, _by("owner-decision")))


def claim(chain: Chain, nonce: str = NONCE, run_id: int = APPROVED_RUN) -> Chain:
    evidence = {"nonce": nonce, "run_id": run_id}
    return chain.add(chain.make("resume_bound", state.FALLBACK_AUTHORIZED, evidence, _by("bind", run_id)))


def approved() -> Chain:
    return claim(request(admitted()))


def facts(**changes: Any) -> Facts:
    base = Facts(
        stage_run_active=False,
        executor_conclusion="success",
        executor_advisory_code="PROVIDER_UNAVAILABLE",
        executor_stderr_category="RATE_LIMIT",
        executor_probe_category="RATE_LIMIT",
        result_artifacts=(),
        resume_run_status="concluded",
        remote_branch_head=None,
        open_draft_pr=None,
        now=NOW,
    )
    return replace(base, **changes)


def art(name: str, artifact_id: int = 31, expired: bool = False) -> ArtifactFact:
    return ArtifactFact(artifact_id, name, "sha256:" + "e" * 64, 100, expired)


# --- identity -----------------------------------------------------------------------------------------------


def test_attempt_one_identity_is_byte_identical_to_the_original_derivation() -> None:
    original = state.sha256_hex(
        state.canonical_json(
            {
                "authorization_id": AUTH,
                "authorize_run_id": 100,
                "run_attempt": 1,
                "control_sha": CONTROL,
                "handoff_sha256": HANDOFF,
            }
        )
    )
    assert authorized_evidence()["execution_id"] == original
    assert FALLBACK_ID != original


@pytest.mark.parametrize("attempt", [0, 3, -1])
def test_there_is_no_third_execution_identity(attempt: int) -> None:
    with pytest.raises(state.LedgerSchemaError):
        state.execution_identity(
            authorization_id=AUTH,
            authorize_run_id=100,
            control_sha=CONTROL,
            handoff_sha256=HANDOFF,
            execution_attempt=attempt,
        )


def test_every_later_reader_binds_the_admitted_execution() -> None:
    chain = offered()
    view = chain.view.authorizations[AUTH]
    assert view.execution_id == authorized_evidence()["execution_id"]
    chain.transition(state.FALLBACK_AUTHORIZED, admitted_evidence(), "bind")
    assert chain.view.authorizations[AUTH].execution_id == FALLBACK_ID


# --- AUTHORIZED binding of the offer ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda e: e["fallback"].update(execution_id="0" * 64), "fallback execution identity"),
        (lambda e: e["fallback"]["handoff_artifact"].update(recipient_key_id="2" * 64), "distinct recipient"),
        (lambda e: e["fallback"]["handoff_artifact"].update(artifact_id=11), "distinct artifact"),
        (lambda e: e["fallback"]["handoff_artifact"].update(run_id=101), "distinct artifact"),
    ],
)
def test_a_forged_fallback_offer_is_corrupt(mutate: Any, message: str) -> None:
    evidence = offered_evidence()
    mutate(evidence)
    chain = Chain()
    reject(chain, chain.make("transition", state.AUTHORIZED, evidence, _by("authorize")), message)


# --- FALLBACK_AUTHORIZED record --------------------------------------------------------------------------------


def test_admission_requires_an_offer_bound_at_authorization() -> None:
    chain = Chain().transition(state.AUTHORIZED, authorized_evidence(), "authorize")
    record = chain.make("transition", state.FALLBACK_AUTHORIZED, admitted_evidence(), _by("bind"))
    reject(chain, record, "bound none")


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"fallback_execution_id": "0" * 64}, "bound fallback identity"),
        ({"stderr_category": "QUOTA_EXCEEDED"}, "not agreed"),
        ({"probe_category": "NETWORK"}, "not agreed"),
        ({"primary_cause": "NETWORK"}, "not agreed"),
        ({"primary_cause": "MODEL_NOT_FOUND", "stderr_category": "MODEL_NOT_FOUND"}, "schema"),
        ({"probe_category": "UNCLASSIFIED"}, "schema"),
        ({"primary_result_artifacts": 1}, "schema"),
        ({"fallback_executor": "kilo"}, "schema"),
    ],
)
def test_a_forged_admission_record_is_corrupt(changes: dict[str, Any], message: str) -> None:
    chain = offered()
    record = chain.make("transition", state.FALLBACK_AUTHORIZED, admitted_evidence(**changes), _by("bind"))
    reject(chain, record, message)


@pytest.mark.parametrize("role", ["authorize", "reconcile", "record-validation", "finalize"])
def test_only_bind_admits_the_fallback(role: str) -> None:
    chain = offered()
    reject(chain, chain.make("transition", state.FALLBACK_AUTHORIZED, admitted_evidence(), _by(role)), "may not write")


def test_there_is_never_a_second_fallback() -> None:
    chain = admitted()
    record = chain.make("transition", state.FALLBACK_AUTHORIZED, admitted_evidence(), _by("bind"))
    reject(chain, record, "illegal transition")


def test_a_bound_result_must_match_the_attempt_it_left() -> None:
    chain = admitted()
    reject(chain, chain.make("transition", state.RESULT_BOUND, result_bound_evidence(), _by("bind")), "attempt")
    primary = offered()
    record = primary.make("transition", state.RESULT_BOUND, fallback_bound_evidence(), _by("bind"))
    reject(primary, record, "attempt")
    chain.transition(state.RESULT_BOUND, fallback_bound_evidence(), "bind")
    assert chain.view.authorizations[AUTH].state == state.RESULT_BOUND


def test_the_admitted_fallback_may_fail_and_names_its_state() -> None:
    chain = admitted()
    chain.transition(
        state.FAILED, {"code": "EXECUTION_NOT_COMPLETED", "failed_from_state": "FALLBACK_AUTHORIZED"}, "bind"
    )
    assert chain.view.active is None


def test_a_replayed_chain_with_the_admission_verifies() -> None:
    chain = admitted().transition(state.RESULT_BOUND, fallback_bound_evidence(), "bind")
    view = state.verify_chain(
        copy.deepcopy(chain.records), repository_id=1, issue_number=520, trust=chain_trust(), provenance=trusted
    )
    assert view.authorizations[AUTH].execution_id == FALLBACK_ID


def chain_trust() -> state.TrustRoots:
    from test_issue_agent_state import TRUST

    return TRUST


# --- advance: admission ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("cause", sorted(state.FALLBACK_CAUSES))
def test_a_probe_verified_recoverable_failure_is_admitted(cause: str) -> None:
    view = offered().view.authorizations[AUTH]
    decision = state.advance(view, facts(executor_stderr_category=cause, executor_probe_category=cause))
    assert decision.action == "transition" and decision.target_state == state.FALLBACK_AUTHORIZED
    assert decision.evidence_hint == {"cause": cause}


@pytest.mark.parametrize(
    "changes",
    [
        {"executor_probe_category": "UNCLASSIFIED"},  # model-forged stderr, healthy provider
        {"executor_probe_category": None},  # no trusted probe: the primary never launched
        {"executor_stderr_category": None},
        {"executor_stderr_category": "QUOTA_EXCEEDED"},  # disagreement never resolves toward fallback
        {"executor_stderr_category": "MODEL_NOT_FOUND", "executor_probe_category": "MODEL_NOT_FOUND"},
        {"executor_stderr_category": "AUTHENTICATION", "executor_probe_category": "AUTHENTICATION"},
        {"executor_stderr_category": "PROVIDER_UNAVAILABLE", "executor_probe_category": "PROVIDER_UNAVAILABLE"},
        {"executor_stderr_category": "rate limit", "executor_probe_category": "rate limit"},  # outside vocabulary
        {"now": "2026-10-04T16:00:00Z"},  # the lifecycle deadline has passed
    ],
)
def test_anything_short_of_agreement_fails_like_today(changes: dict[str, Any]) -> None:
    view = offered().view.authorizations[AUTH]
    decision = state.advance(view, facts(**changes))
    assert decision == state.Decision("fail", code="EXECUTION_NOT_COMPLETED")


def test_without_a_bound_offer_the_primary_failure_stays_terminal() -> None:
    view = Chain().transition(state.AUTHORIZED, authorized_evidence(), "authorize").view.authorizations[AUTH]
    assert state.advance(view, facts()).code == "EXECUTION_NOT_COMPLETED"


def test_a_primary_result_always_wins_over_a_fallback_signal() -> None:
    view = offered().view.authorizations[AUTH]
    decision = state.advance(view, facts(result_artifacts=(art(state.result_artifact_name(AUTH)),)))
    assert decision.target_state == state.RESULT_BOUND


def test_a_primary_that_never_started_never_falls_back() -> None:
    view = offered().view.authorizations[AUTH]
    assert state.advance(view, facts(executor_conclusion="skipped")).code == "EXECUTION_NOT_STARTED"


# --- advance: the admitted fallback ----------------------------------------------------------------------------


def test_one_fallback_result_is_bound() -> None:
    view = approved().view.authorizations[AUTH]
    decision = state.advance(view, facts(result_artifacts=(art(state.fallback_result_artifact_name(AUTH)),)))
    assert decision.target_state == state.RESULT_BOUND and decision.evidence_hint == {"artifact_id": 31}


@pytest.mark.parametrize(
    "artifacts",
    [
        (art(state.result_artifact_name(AUTH)),),  # a late primary result can never stand in for attempt 2
        (art(state.result_artifact_name(AUTH)), art(state.fallback_result_artifact_name(AUTH), 32)),
        (art(state.fallback_result_artifact_name(AUTH)), art(state.fallback_result_artifact_name(AUTH), 32)),
    ],
)
def test_any_result_other_than_exactly_one_fallback_result_is_an_integrity_failure(artifacts: tuple) -> None:
    view = approved().view.authorizations[AUTH]
    assert state.advance(view, facts(result_artifacts=artifacts)).code == "TRANSPORT_INTEGRITY_FAILED"


def test_the_fallback_never_falls_back_again() -> None:
    view = approved().view.authorizations[AUTH]
    assert state.advance(view, facts()).code == "EXECUTION_NOT_COMPLETED"
    assert state.advance(view, facts(executor_conclusion="cancelled")).code == "EXECUTION_NOT_COMPLETED"
    # A run whose job never started launched no model: the request returns to pending, never to a second fallback.
    assert state.advance(view, facts(executor_conclusion=None)).action == "abandon_resume"


def test_unknown_or_active_fallback_facts_never_decide() -> None:
    view = approved().view.authorizations[AUTH]
    assert state.advance(view, facts(result_artifacts=UNKNOWN)).action == "noop"
    assert state.advance(view, facts(stage_run_active=True)).action == "noop"
    assert state.advance(view, facts(resume_run_status="active")).action == "noop"
    assert state.advance(view, facts(resume_run_status=UNKNOWN)).action == "noop"


# --- ADR 0041 F1: pending until the owner decides -------------------------------------------------------------


@pytest.mark.parametrize("now", [NOW, "2026-10-04T16:00:01Z", "2027-06-01T00:00:00Z", "2099-01-01T00:00:00Z"])
def test_an_unapproved_fallback_waits_for_the_owner_without_any_deadline(now: str) -> None:
    view = admitted().view.authorizations[AUTH]
    for observed in (
        facts(now=now),
        facts(now=now, result_artifacts=UNKNOWN),
        facts(now=now, executor_conclusion=None),
    ):
        decision = state.advance(view, observed)
        assert decision.action == "noop" and "owner" in decision.reason


def test_an_approved_run_that_never_claimed_its_nonce_is_redispatched_or_returned_to_pending() -> None:
    view = request(admitted()).view.authorizations[AUTH]
    assert state.advance(view, facts(resume_run_status=None, now="2026-10-04T10:31:00Z")).action == "noop"
    assert state.advance(view, facts(resume_run_status=None, now="2026-10-04T10:45:00Z")).action == "redispatch"
    assert state.advance(view, facts(resume_run_status="concluded")).action == "abandon_resume"


def test_a_session_that_never_started_returns_the_request_to_pending() -> None:
    view = approved().view.authorizations[AUTH]
    decision = state.advance(view, facts(executor_advisory_code="FALLBACK_SESSION_NOT_STARTED"))
    assert decision == state.Decision("abandon_resume", stage="fallback", reason="run_concluded_without_output")


def test_a_result_always_wins_over_a_not_started_claim() -> None:
    view = approved().view.authorizations[AUTH]
    result = (art(state.fallback_result_artifact_name(AUTH)),)
    decision = state.advance(
        view, facts(executor_advisory_code="FALLBACK_SESSION_NOT_STARTED", result_artifacts=result)
    )
    assert decision.target_state == state.RESULT_BOUND


def test_the_owner_may_renew_any_number_of_times_once_each_previous_run_is_closed() -> None:
    chain = admitted()
    for attempt in range(1, 8):  # beyond every resume cap of the other stages
        nonce = f"{attempt:02d}" * 32
        request(chain, attempt, nonce)
        claim(chain, nonce, 101)
        chain.add(
            chain.make(
                "resume_abandoned",
                state.FALLBACK_AUTHORIZED,
                {"nonce": nonce, "reason": "run_concluded_without_output"},
                _by("bind", 102),
            )
        )
    view = chain.view.authorizations[AUTH]
    assert view.state == state.FALLBACK_AUTHORIZED and view.pending_resume is None
    assert view.resume_attempts == {"fallback": 7}


@pytest.mark.parametrize(
    "build,message",
    [
        (lambda c: request(request(c), 2, "cd" * 32), "while one is pending"),  # one approved run at a time
        (lambda c: request(c, 2), "next attempt"),  # attempts are contiguous
    ],
)
def test_a_second_concurrent_or_skipped_approval_is_corrupt(build: Any, message: str) -> None:
    with pytest.raises(LedgerCorruptError, match=message):
        build(admitted())


@pytest.mark.parametrize("role", ["authorize", "candidate-pr-record"])
def test_only_resume_roles_request_the_fallback_run(role: str) -> None:
    chain = admitted()
    evidence = {"stage": "fallback", "nonce": NONCE, "attempt": 1, "dispatched_at": "2026-10-04T10:30:00Z"}
    reject(chain, chain.make("resume_requested", state.FALLBACK_AUTHORIZED, evidence, _by(role)), "resume record")


def test_a_fallback_run_cannot_be_requested_outside_the_admitted_state() -> None:
    chain = offered()
    evidence = {"stage": "fallback", "nonce": NONCE, "attempt": 1, "dispatched_at": "2026-10-04T10:30:00Z"}
    reject(chain, chain.make("resume_requested", state.AUTHORIZED, evidence, _by("owner-decision")), "outside")


def test_the_owner_may_withdraw_a_pending_fallback() -> None:
    chain = admitted()
    chain.transition(
        state.FAILED, {"code": "OWNER_WITHDREW", "failed_from_state": "FALLBACK_AUTHORIZED"}, "owner-decision"
    )
    assert chain.view.active is None


@pytest.mark.parametrize(
    "build,target,evidence",
    [
        (lambda: offered(), state.FALLBACK_AUTHORIZED, admitted_evidence),
        (lambda: approved(), state.RESULT_BOUND, fallback_bound_evidence),
        (
            lambda: approved().transition(state.RESULT_BOUND, fallback_bound_evidence(), "bind"),
            state.VALIDATED,
            validated_evidence,
        ),
    ],
)
def test_the_owner_decision_role_writes_nothing_but_the_withdrawal(build: Any, target: str, evidence: Any) -> None:
    # Schema-valid evidence on a chain where the transition itself is legal: only the writer role is wrong.
    chain = build()
    reject(chain, chain.make("transition", target, evidence(), _by("owner-decision")), "may not write")


def test_a_fallback_result_is_published_against_its_own_restarted_deadline() -> None:
    chain = approved().transition(state.RESULT_BOUND, fallback_bound_evidence(), "bind")
    view = chain.view.authorizations[AUTH]
    assert view.publish_deadline == "2026-12-01T00:00:00Z" != view.binding("deadline_published_at")


def test_an_early_branch_or_pull_request_freezes_the_admitted_fallback() -> None:
    view = admitted().view.authorizations[AUTH]
    assert state.advance(view, facts(remote_branch_head="9" * 40)).action == "freeze"
    assert state.advance(view, facts(open_draft_pr={"number": 1})).action == "freeze"


def test_an_expired_fallback_result_is_not_bound() -> None:
    view = approved().view.authorizations[AUTH]
    expired = (art(state.fallback_result_artifact_name(AUTH), expired=True),)
    assert state.advance(view, facts(result_artifacts=expired)).code == "RESULT_TRANSPORT_EXPIRED"


def test_artifact_names_are_distinct_and_never_prefix_confusable() -> None:
    names = {
        state.result_artifact_name(AUTH),
        state.fallback_result_artifact_name(AUTH),
        state.handoff_artifact_name(AUTH),
        state.fallback_handoff_artifact_name(AUTH),
    }
    assert len(names) == 4


def test_corrupt_admission_is_reported_as_ledger_corruption_not_a_crash() -> None:
    chain = offered()
    evidence = admitted_evidence()
    del evidence["probe_category"]
    with pytest.raises(LedgerCorruptError):
        state.apply_record(
            copy.deepcopy(chain.view),
            chain.make("transition", state.FALLBACK_AUTHORIZED, evidence, _by("bind")),
            trust=chain_trust(),
            provenance=trusted,
        )
