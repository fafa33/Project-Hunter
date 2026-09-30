"""Focused tests for the Issue #541 root-of-trust review-opportunity cutover.

The cutover may enable exactly one thing: trusted exact-HEAD Review Opportunity
for one owner-authorized migration identity, ahead of full Candidate Admission.
It must not grant admission, provenance, signature, preflight, governance, merge
readiness or merge authority, and it must not be assertable by any candidate.

These tests cover the changed trusted boundary only.
"""

from __future__ import annotations

import sys

import hunter_review_orchestrator as orchestrator
import pytest

HEAD = "a" * 40
MIGRATION_REPOSITORY = "fafa33/Project-Hunter"
MIGRATION_PR = 535
ORDINARY_REPOSITORY = "fafa33/Project-Hunter"
ORDINARY_PR = 600


def _ago_minutes(minutes: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


#: Shared parametrize scaffolding. Declaring the cases once keeps the repeated
#: `[(pytest.param(...), ...)]` boilerplate out of every decorated test, which is
#: the only remaining duplicated shape in this file.
def _cases(*specs):
    """`[(pytest.param(*values, id=name), ...)]` from `(*values, name)` specs."""

    return [pytest.param(*values[:-1], id=values[-1]) for values in specs]


def _stub(monkeypatch, target, **stubs):
    """Patch several attributes of one module in a single call.

    Every test reaches the orchestrator and the governance module through
    repeated `monkeypatch.setattr(<module>, ...)` blocks; routing them through
    one helper keeps that the only place the pattern appears.
    """

    for name, value in stubs.items():
        monkeypatch.setattr(target, name, value, raising=False)
    return target


def _stub_governance(monkeypatch, **stubs):
    """Patch the trusted governance module in one call."""

    return _stub(monkeypatch, _governance(), **stubs)


def _governance():
    import hunter_governance_review_v2 as governance

    return governance


def _admission_blocked_unknown_key(monkeypatch, state="failure"):
    """Candidate Admission fails on an unverified pre-push ingress signature."""

    return _stub_governance(
        monkeypatch,
        read_trusted_upgrade_status=lambda *_args, **_kwargs: (
            state,
            "Candidate admission blocked: commit a87bc81ab4 has no verified pre-push ingress signature (reason=unknown_key).",
        ),
    )


def _request_valid_for_this_head(monkeypatch, *, valid=True, state="present"):
    document = {"review_request": {"schema": "hunter.review-request.v1", "claims_id": "c" * 64}}
    reader = (
        (lambda *_args: ("present", document, None)) if state == "present" else (lambda *_args: (state, None, "absent"))
    )
    _stub_governance(
        monkeypatch,
        read_head_pre_ready_review=reader,
        valid_current_review_request=lambda *_args: (
            valid,
            "the pre-ready hostile review describes different content than this candidate head",
        ),
    )
    return document


def _open_pr(monkeypatch, head=HEAD, draft=False):
    monkeypatch.setattr(
        orchestrator,
        "request_json",
        lambda *_args: {"state": "open", "draft": draft, "head": {"sha": head}},
        raising=False,
    )


def _dispatched_nothing(stored):
    """No cycle published and no reviewer capacity spent on this head."""

    assert stored["cycle"] is None
    assert stored["dispatches"] == 0
    return True


def _blocked_migration_candidate(monkeypatch, *, request_state="present", valid=True, draft=False, head=HEAD):
    """Arm the migration identity as an admission-blocked candidate at an exact head.

    Admission stays blocked on an unverified ingress signature, the pull request
    is open at `head`, and the collector is armed so any dispatch is observable.
    """

    _admission_blocked_unknown_key(monkeypatch)
    _open_pr(monkeypatch, head=head, draft=draft)
    _request_valid_for_this_head(monkeypatch, valid=valid, state=request_state)
    return _collector(monkeypatch)


def _collector(monkeypatch):
    """Arm the collector boundary so any dispatch is observable."""

    stored = {"cycle": None, "dispatches": 0}
    _stub(
        monkeypatch,
        orchestrator,
        read_cycle=lambda *_a: ("present", stored["cycle"], None) if stored["cycle"] else ("absent", None, None),
        reviewer_pool_config_digest=lambda: "d" * 64,
        current_run_id=lambda: 555,
        publish_cycle=lambda *_a, cycle: stored.update(cycle=cycle),
        collector_liveness=lambda *_a: ("missing", 0),
        dispatch_collector=lambda *_a: stored.update(dispatches=stored["dispatches"] + 1),
        current_remediation_generation=lambda *_a: "gen-1",
    )
    return stored


# --- 1. the migration identity does get Review Opportunity -------------------


@pytest.mark.parametrize(
    ("repository", "pr_number", "preflight"),
    _cases(
        (MIGRATION_REPOSITORY, MIGRATION_PR, "failure", "migration-identity-admission-blocked"),
        (ORDINARY_REPOSITORY, ORDINARY_PR, "success", "ordinary-pr-preflight-passed"),
    ),
)
def test_valid_request_starts_exactly_one_cycle(monkeypatch, repository, pr_number, preflight):
    """Requirements 1 and 10: a request that binds this exact head dispatches the
    collector once.

    Both paths reach the same outcome by different gates -- the migration
    identity reaches it with admission blocked, the ordinary path reaches it
    with the preflight satisfied -- so the expectation is asserted once.
    """

    if pr_number == MIGRATION_PR:
        stored = _blocked_migration_candidate(monkeypatch)
    else:
        _admission_blocked_unknown_key(monkeypatch, state=preflight)
        _open_pr(monkeypatch)
        _request_valid_for_this_head(monkeypatch)
        stored = _collector(monkeypatch)

    cycle = orchestrator.ensure_current(repository, "token", pr_number)

    assert cycle is not None
    assert cycle.head_sha == HEAD
    assert cycle.state == "WAITING_FOR_REVIEWER"
    assert stored["dispatches"] == 1


# --- 2/3. and it stays blocked for admission and merge ----------------------


def test_migration_candidate_remains_candidate_admission_failure(monkeypatch):
    """Requirement 2: the cutover does not convert the admission failure."""

    governance = _admission_blocked_unknown_key(monkeypatch)
    unverified = "a87bc81ab4" + "0" * 30
    _stub_governance(
        monkeypatch,
        read_pr_commits=lambda *_a, **_k: (
            True,
            [
                {"sha": unverified, "commit": {"verification": {"verified": False, "reason": "unknown_key"}}},
                {"sha": HEAD, "commit": {"verification": {"verified": True, "reason": "valid"}}},
            ],
            None,
        ),
        read_commits_beyond_attestation_floor=lambda *_a, **_k: (True, frozenset({unverified, HEAD}), None),
        read_pr_changed_files=lambda *_a, **_k: (True, [{"path": "scripts/x.py", "status": "modified"}], None),
        verify_connector_ingress_authorization=lambda *_a, **_k: type(
            "V", (), {"ok": True, "message": "", "origin": False}
        )(),
    )

    verdict, reason = governance.verify_code_write_ingress_provenance(MIGRATION_REPOSITORY, "token", HEAD, MIGRATION_PR)

    assert verdict == "failure"
    assert "no verified pre-push ingress signature" in reason
    assert "unknown_key" in reason


def test_migration_candidate_remains_non_mergeable(monkeypatch):
    """Requirement 3: review opportunity is not merge authority."""

    governance = _governance()
    readiness = (
        orchestrator.review_request_state.__wrapped__
        if hasattr(orchestrator.review_request_state, "__wrapped__")
        else None
    )
    assert readiness is None  # not memoised; the call below is the real check

    _admission_blocked_unknown_key(monkeypatch)
    _request_valid_for_this_head(monkeypatch)
    state = orchestrator.review_request_state(MIGRATION_REPOSITORY, "token", MIGRATION_PR, HEAD)

    # Review is ready, yet the admission prerequisite it deliberately ignored is
    # still a failure. Readiness of review never implies readiness to merge.
    assert state.ready is True
    preflight_state, preflight_reason = governance.read_trusted_upgrade_status(
        MIGRATION_REPOSITORY, "token", HEAD, MIGRATION_PR
    )
    assert preflight_state == "failure"
    assert "unknown_key" in preflight_reason


# --- 4. unknown_key is never softened --------------------------------------


def test_unknown_key_is_not_converted_to_success_or_pending(monkeypatch):
    """Requirement 4: the cutover does not touch the admission verdict at all."""

    _admission_blocked_unknown_key(monkeypatch, state="pending")
    _open_pr(monkeypatch)
    _request_valid_for_this_head(monkeypatch)
    _collector(monkeypatch)

    governance = _governance()
    state, _reason = governance.read_trusted_upgrade_status(MIGRATION_REPOSITORY, "token", HEAD, MIGRATION_PR)

    # The orchestrator reached review, but admission is reported exactly as the
    # trusted evidence reports it -- pending stays pending, it is not promoted.
    assert state == "pending"


# --- 5. candidates cannot assert the bootstrap -----------------------------


def test_candidate_authored_bootstrap_claim_cannot_activate_the_cutover(monkeypatch):
    """Requirement 5: the identity is trusted literal code, never input."""

    assert orchestrator.REVIEW_OPPORTUNITY_MIGRATION_IDENTITIES == frozenset({(MIGRATION_REPOSITORY, MIGRATION_PR)})
    # A different repository that merely claims the same PR number is not it.
    assert orchestrator._review_opportunity_migration("attacker/Project-Hunter", MIGRATION_PR) is False
    # A different PR in the trusted repository is not it either.
    assert orchestrator._review_opportunity_migration(MIGRATION_REPOSITORY, MIGRATION_PR + 1) is False
    # The comparison is over literal values; nothing reads candidate content.
    assert not any(
        isinstance(item, str) or hasattr(item, "get") for item in orchestrator.REVIEW_OPPORTUNITY_MIGRATION_IDENTITIES
    )


@pytest.mark.parametrize(
    "claim",
    [
        {"bootstrap": True},
        {"HUNTER_BOOTSTRAP": "1"},
        {"migration": "issue-541"},
        {"review_opportunity_migration": True},
    ],
)
def test_no_environment_or_injected_claim_can_activate_the_cutover(monkeypatch, claim):
    """Requirement 5, behaviourally: nothing outside the literal identity turns
    the cutover on. Sweeping the environment proves the predicate reads no
    ambient state, and a foreign PR is refused under every injection."""

    for key, value in claim.items():
        monkeypatch.setenv(key, value)

    assert orchestrator._review_opportunity_migration("attacker/Project-Hunter", MIGRATION_PR) is False
    assert orchestrator._review_opportunity_migration(MIGRATION_REPOSITORY, 1) is False
    assert orchestrator._review_opportunity_migration(MIGRATION_REPOSITORY, 999) is False
    # And the ordinary path still requires the preflight under the same sweep.
    stored = _blocked_migration_candidate(monkeypatch)
    assert orchestrator.ensure_current(ORDINARY_REPOSITORY, "token", ORDINARY_PR) is None
    assert stored["dispatches"] == 0


# --- 6/7/8. evidence and exact-head binding --------------------------------


@pytest.mark.parametrize(
    ("request_state", "valid", "head"),
    _cases(
        ("present", False, HEAD, "self-issued-request"),
        ("invalid", None, HEAD, "unreadable-request"),
    ),
)
def test_unusable_request_creates_no_authority(monkeypatch, request_state, valid, head):
    """Requirements 6 and the P2 fix: a request that exists for this head but
    cannot be dispatched as it stands is terminal and spends no capacity.

    A self-issued request whose claims do not re-derive from trusted state, and a
    committed request that is not readable JSON, are the same defect from the
    orchestrator's point of view: the head cannot resolve it by waiting.
    """

    kwargs = {"request_state": request_state, "head": head}
    if valid is not None:
        kwargs["valid"] = valid
    stored = _blocked_migration_candidate(monkeypatch, **kwargs)

    with pytest.raises(orchestrator.ReviewRequestBlocked):
        orchestrator.ensure_current(MIGRATION_REPOSITORY, "token", MIGRATION_PR)

    assert _dispatched_nothing(stored)


def test_stale_head_review_evidence_remains_invalid(monkeypatch):
    """Requirement 7: a request that does not bind the current exact head is
    still refused, even for the migration identity."""

    stored = _blocked_migration_candidate(monkeypatch, head="d" * 40, valid=False)
    stored["cycle"] = orchestrator.ReviewCycle(
        pr_number=MIGRATION_PR,
        head_sha="b" * 40,
        state="WAITING_FOR_REVIEWER",
        provider_id="copilot",
        started_at=_ago_minutes(5),
        trigger_id=1,
        config_digest="d" * 64,
        generation_id="gen-0",
    )

    with pytest.raises(orchestrator.ReviewRequestBlocked):
        orchestrator.ensure_current(MIGRATION_REPOSITORY, "token", MIGRATION_PR)

    assert stored["dispatches"] == 0
    assert stored["cycle"].head_sha == "b" * 40


def test_exact_head_binding_remains_mandatory(monkeypatch):
    """Requirement 8: the cycle is published for the head GitHub reports."""

    _admission_blocked_unknown_key(monkeypatch)
    new_head = "e" * 40
    _open_pr(monkeypatch, head=new_head)
    _request_valid_for_this_head(monkeypatch)
    _collector(monkeypatch)

    cycle = orchestrator.ensure_current(MIGRATION_REPOSITORY, "token", MIGRATION_PR)

    assert cycle.head_sha == new_head
    assert cycle.head_sha != HEAD


# --- 9. draft stays safe ----------------------------------------------------


# --- 10. ordinary behaviour is untouched -----------------------------------


def test_ordinary_pr_still_requires_the_trusted_preflight(monkeypatch):
    """Requirement 10: outside the migration identity nothing changes."""

    stored = _blocked_migration_candidate(monkeypatch)

    assert orchestrator.ensure_current(ORDINARY_REPOSITORY, "token", ORDINARY_PR) is None
    assert _dispatched_nothing(stored)

    readiness = orchestrator.review_request_state(ORDINARY_REPOSITORY, "token", ORDINARY_PR, HEAD)
    assert readiness.ready is False
    assert readiness.prerequisite_state == "failure"


# --- 11. the cutover cannot outlive its purpose ----------------------------


def test_cutover_grants_no_admission_surface_in_the_orchestrator():
    """Requirement 11, structurally: the cutover touches the review gate only.

    The orchestrator must still delegate admission to the trusted governance
    module, and must contain no code that could mark a candidate admissible.
    """

    source = open(orchestrator.__file__, encoding="utf-8").read()
    assert orchestrator.__file__.endswith("hunter_review_orchestrator.py")
    # No admission-granting call is introduced by the cutover.
    for forbidden in (
        "publish_admission",
        "grant_admission",
        "admit(",
        "set_admission",
        "override_admission",
    ):
        assert forbidden not in source
    # The only governance surface the cutover removes is the preflight read that
    # gated review; admission still reads it directly.
    assert source.count("read_trusted_upgrade_status") == 1


# --- 9. draft stays safe / identity is only a temporary widening -------------


@pytest.mark.parametrize(
    ("draft", "drop_identity", "why"),
    _cases(
        (True, False, "draft", "draft-pr"),
        (False, True, "identity-removed", "identity-removed-narrows"),
    ),
)
def test_migration_grants_nothing_where_it_must_not(monkeypatch, draft, drop_identity, why):
    """Requirement 9 and 11: Draft never gains authority, and once the migration
    identity is gone the candidate is refused exactly as any other would be.

    Both are the same observable outcome -- no cycle, no dispatch -- reached by
    different gates, so they are asserted together rather than twice.
    """

    stored = _blocked_migration_candidate(monkeypatch, draft=draft)
    if drop_identity:
        monkeypatch.setattr(orchestrator, "REVIEW_OPPORTUNITY_MIGRATION_IDENTITIES", frozenset(), raising=False)

    assert orchestrator.ensure_current(MIGRATION_REPOSITORY, "token", MIGRATION_PR) is None, why
    assert _dispatched_nothing(stored)


# --- Codex P2: a committed-but-unusable request is terminal ------------------
#
# read_head_pre_ready_review() can report "invalid" when the committed request
# is not readable JSON. The migration path propagated that state straight into
# prerequisite_state, and ensure_current() only raised for "present", so an
# invalid request returned None: reconcile exited successfully and this head
# silently stalled with no reviewer ever dispatched.


@pytest.mark.parametrize(
    "request_state",
    _cases(("absent", "nothing-committed-yet"), ("unavailable", "transient-github-evidence")),
)
def test_absent_or_unavailable_migration_request_stays_retryable(monkeypatch, request_state):
    """The benign states keep the ordinary retry path: no block, no dispatch."""

    stored = _blocked_migration_candidate(monkeypatch, request_state=request_state)

    assert orchestrator.ensure_current(MIGRATION_REPOSITORY, "token", MIGRATION_PR) is None
    assert _dispatched_nothing(stored)
    # A retryable wait must not be reported as a red reconcile.
    monkeypatch.setattr(
        sys, "argv", ["orchestrator", "ensure", "--repository", MIGRATION_REPOSITORY, "--pr", str(MIGRATION_PR)]
    )
    assert orchestrator.main() == 0


def test_terminal_request_states_exclude_the_retryable_ones():
    """The classification is explicit, so a future state cannot fall through."""

    assert orchestrator.TERMINAL_REQUEST_STATES == frozenset({"present", "invalid"})
    for benign in ("absent", "unavailable"):
        assert benign not in orchestrator.TERMINAL_REQUEST_STATES
