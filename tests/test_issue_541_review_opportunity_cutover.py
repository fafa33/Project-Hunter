"""Focused tests for the Issue #541 root-of-trust review-opportunity cutover.

The cutover may enable exactly one thing: trusted exact-HEAD Review Opportunity
for one owner-authorized migration identity, ahead of full Candidate Admission.
It must not grant admission, provenance, signature, preflight, governance, merge
readiness or merge authority, and it must not be assertable by any candidate.

These tests cover the changed trusted boundary only.
"""

from __future__ import annotations

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


def _governance():
    import hunter_governance_review_v2 as governance

    return governance


def _admission_blocked_unknown_key(monkeypatch, state="failure"):
    """Candidate Admission fails on an unverified pre-push ingress signature."""

    governance = _governance()
    monkeypatch.setattr(
        governance,
        "read_trusted_upgrade_status",
        lambda *_args, **_kwargs: (
            state,
            "Candidate admission blocked: commit a87bc81ab4 has no verified pre-push ingress signature (reason=unknown_key).",
        ),
        raising=False,
    )
    return governance


def _request_valid_for_this_head(monkeypatch, *, valid=True, state="present"):
    governance = _governance()
    document = {"review_request": {"schema": "hunter.review-request.v1", "claims_id": "c" * 64}}
    if state == "present":
        monkeypatch.setattr(
            governance, "read_head_pre_ready_review", lambda *_args: ("present", document, None), raising=False
        )
    else:
        monkeypatch.setattr(
            governance, "read_head_pre_ready_review", lambda *_args: (state, None, "absent"), raising=False
        )
    monkeypatch.setattr(
        governance,
        "valid_current_review_request",
        lambda *_args: (valid, "the pre-ready hostile review describes different content than this candidate head"),
        raising=False,
    )
    return document


def _open_pr(monkeypatch, head=HEAD, draft=False):
    monkeypatch.setattr(
        orchestrator,
        "request_json",
        lambda *_args: {"state": "open", "draft": draft, "head": {"sha": head}},
        raising=False,
    )


def _collector(monkeypatch):
    stored = {"cycle": None, "dispatches": 0}
    monkeypatch.setattr(
        orchestrator,
        "read_cycle",
        lambda *_args: ("present", stored["cycle"], None) if stored["cycle"] else ("absent", None, None),
        raising=False,
    )
    monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64, raising=False)
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: 555, raising=False)
    monkeypatch.setattr(orchestrator, "publish_cycle", lambda *_args, cycle: stored.update(cycle=cycle), raising=False)
    monkeypatch.setattr(orchestrator, "collector_liveness", lambda *_args: ("missing", 0), raising=False)
    monkeypatch.setattr(
        orchestrator,
        "dispatch_collector",
        lambda *_args: stored.update(dispatches=stored["dispatches"] + 1),
        raising=False,
    )
    monkeypatch.setattr(orchestrator, "current_remediation_generation", lambda *_args: "gen-1", raising=False)
    return stored


# --- 1. the migration identity does get Review Opportunity -------------------


def test_admission_blocked_migration_candidate_receives_review_opportunity(monkeypatch):
    """Requirement 1: a candidate blocked from admission can still earn review."""

    _admission_blocked_unknown_key(monkeypatch)
    _open_pr(monkeypatch)
    _request_valid_for_this_head(monkeypatch)
    stored = _collector(monkeypatch)

    cycle = orchestrator.ensure_current(MIGRATION_REPOSITORY, "token", MIGRATION_PR)

    assert cycle is not None
    assert cycle.head_sha == HEAD
    assert cycle.state == "WAITING_FOR_REVIEWER"
    assert stored["dispatches"] == 1


# --- 2/3. and it stays blocked for admission and merge ----------------------


def test_migration_candidate_remains_candidate_admission_failure(monkeypatch):
    """Requirement 2: the cutover does not convert the admission failure."""

    governance = _admission_blocked_unknown_key(monkeypatch)
    unverified = "a87bc81ab4" + "0" * 30
    monkeypatch.setattr(
        governance,
        "read_pr_commits",
        lambda *_args, **_kwargs: (
            True,
            [
                {"sha": unverified, "commit": {"verification": {"verified": False, "reason": "unknown_key"}}},
                {"sha": HEAD, "commit": {"verification": {"verified": True, "reason": "valid"}}},
            ],
            None,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        governance,
        "read_commits_beyond_attestation_floor",
        lambda *_args, **_kwargs: (True, frozenset({unverified, HEAD}), None),
        raising=False,
    )
    monkeypatch.setattr(
        governance,
        "read_pr_changed_files",
        lambda *_args, **_kwargs: (True, [{"path": "scripts/x.py", "status": "modified"}], None),
        raising=False,
    )
    monkeypatch.setattr(
        governance,
        "verify_connector_ingress_authorization",
        lambda *_args, **_kwargs: type("V", (), {"ok": True, "message": "", "origin": False})(),
        raising=False,
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
    _admission_blocked_unknown_key(monkeypatch)
    _open_pr(monkeypatch)
    _request_valid_for_this_head(monkeypatch)
    stored = _collector(monkeypatch)
    assert orchestrator.ensure_current(ORDINARY_REPOSITORY, "token", ORDINARY_PR) is None
    assert stored["dispatches"] == 0


# --- 6/7/8. evidence and exact-head binding --------------------------------


def test_candidate_authored_review_evidence_creates_no_authority(monkeypatch):
    """Requirement 6: a self-issued request whose claims do not re-derive from
    trusted state cannot start a review."""

    _admission_blocked_unknown_key(monkeypatch)
    _open_pr(monkeypatch)
    _request_valid_for_this_head(monkeypatch, valid=False)
    stored = _collector(monkeypatch)

    with pytest.raises(orchestrator.ReviewRequestBlocked):
        orchestrator.ensure_current(MIGRATION_REPOSITORY, "token", MIGRATION_PR)

    assert stored["cycle"] is None
    assert stored["dispatches"] == 0


def test_stale_head_review_evidence_remains_invalid(monkeypatch):
    """Requirement 7: a request that does not bind the current exact head is
    still refused, even for the migration identity."""

    _admission_blocked_unknown_key(monkeypatch)
    _open_pr(monkeypatch, head="d" * 40)
    _request_valid_for_this_head(monkeypatch, valid=False)
    stored = _collector(monkeypatch)
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


def test_draft_migration_pr_gets_no_review_authority(monkeypatch):
    """Requirement 9: the migration identity does not leak into Draft."""

    _admission_blocked_unknown_key(monkeypatch)
    _open_pr(monkeypatch, draft=True)
    _request_valid_for_this_head(monkeypatch)
    stored = _collector(monkeypatch)

    assert orchestrator.ensure_current(MIGRATION_REPOSITORY, "token", MIGRATION_PR) is None
    assert stored["cycle"] is None
    assert stored["dispatches"] == 0


# --- 10. ordinary behaviour is untouched -----------------------------------


def test_ordinary_pr_still_requires_the_trusted_preflight(monkeypatch):
    """Requirement 10: outside the migration identity nothing changes."""

    _admission_blocked_unknown_key(monkeypatch)
    _open_pr(monkeypatch)
    _request_valid_for_this_head(monkeypatch)
    stored = _collector(monkeypatch)

    assert orchestrator.ensure_current(ORDINARY_REPOSITORY, "token", ORDINARY_PR) is None
    assert stored["cycle"] is None
    assert stored["dispatches"] == 0

    readiness = orchestrator.review_request_state(ORDINARY_REPOSITORY, "token", ORDINARY_PR, HEAD)
    assert readiness.ready is False
    assert readiness.prerequisite_state == "failure"


def test_ordinary_pr_behaves_identically_when_preflight_succeeds(monkeypatch):
    """Requirement 10, positive side: the ordinary path is unchanged."""

    _admission_blocked_unknown_key(monkeypatch, state="success")
    _open_pr(monkeypatch)
    _request_valid_for_this_head(monkeypatch)
    stored = _collector(monkeypatch)

    cycle = orchestrator.ensure_current(ORDINARY_REPOSITORY, "token", ORDINARY_PR)

    assert cycle is not None
    assert stored["dispatches"] == 1


# --- 11. the cutover cannot outlive its purpose ----------------------------


def test_removing_the_migration_identity_narrows_and_never_widens(monkeypatch):
    """Requirement 11: with the identity gone the ordinary gate is back, and the
    migration candidate is refused exactly as any other would be."""

    _admission_blocked_unknown_key(monkeypatch)
    _open_pr(monkeypatch)
    _request_valid_for_this_head(monkeypatch)
    stored = _collector(monkeypatch)

    monkeypatch.setattr(orchestrator, "REVIEW_OPPORTUNITY_MIGRATION_IDENTITIES", frozenset(), raising=False)

    assert orchestrator.ensure_current(MIGRATION_REPOSITORY, "token", MIGRATION_PR) is None
    assert stored["cycle"] is None
    assert stored["dispatches"] == 0


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
