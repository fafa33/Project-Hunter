"""Convergence and property tests for the Merge Readiness current-state reconciler.

The invariant under test::

    same current canonical state  =>  same readiness result

regardless of which event triggered reconciliation, event delivery order,
duplicate delivery, delayed delivery, cancelled predecessor runs, replayed
``workflow_run`` payloads, stale status publication history, scheduler timing,
or prior pending states.

Each test states which numbered property of the redesign it discharges. Tests
drive the controller through its real trigger adapters (``core.main()`` with a
``GITHUB_EVENT_PATH``) wherever the trigger is the thing under test, so a
regression that reintroduced payload authority would be caught at the adapter
boundary rather than assumed away.
"""

from __future__ import annotations

import itertools

import hunter_merge_readiness as core
import pytest
from hunter_readiness_harness import (
    READY_BODY,
    FakeGitHub,
    drive,
    install,
    ready_pull_request,
)


@pytest.fixture
def gh(monkeypatch):
    server = FakeGitHub()
    install(monkeypatch, server)
    return server


def _pull_request_event(number: int, head: str) -> dict:
    return {"action": "synchronize", "pull_request": {"number": number, "head": {"sha": head}}}


def _issue_comment_event(number: int) -> dict:
    return {"action": "created", "issue": {"number": number, "pull_request": {"url": "x"}}}


def _review_event(number: int, head: str) -> dict:
    return {"action": "submitted", "pull_request": {"number": number, "head": {"sha": head}}}


def _workflow_run_event(head: str, numbers: list[int] | None = None, conclusion: str = "success") -> dict:
    return {
        "workflow_run": {
            "conclusion": conclusion,
            "head_sha": head,
            "run_started_at": "2020-01-01T00:00:00Z",
            "pull_requests": [{"number": n, "head": {"sha": head}} for n in (numbers or [])],
        }
    }


def _schedule_event() -> dict:
    return {}


def _dispatch_event(number: int) -> dict:
    return {"inputs": {"pr_number": str(number)}}


# --- 1, 2, 22, 23, 24: order, duplication and timestamps are irrelevant ------


def test_property_1_every_trigger_order_reaches_the_same_readiness_result(gh, monkeypatch, tmp_path):
    """Property 1: the same current state reached through different event orders converges."""
    head = ready_pull_request(gh)
    triggers = [
        ("pull_request_target", _pull_request_event(501, head)),
        ("issue_comment", _issue_comment_event(501)),
        ("workflow_run", _workflow_run_event(head, [501])),
        ("schedule", _schedule_event()),
    ]

    results = set()
    for order in itertools.permutations(triggers):
        for event_name, event in order:
            drive(monkeypatch, tmp_path, gh, event_name, event)
        results.add(gh.readiness_status(head))

    assert results == {("success", core.decide(core.read_current_state(501)).description)}


def test_property_2_duplicate_delivery_produces_no_semantic_drift(gh, monkeypatch, tmp_path):
    """Property 2: duplicate events give the same result and no drift."""
    head = ready_pull_request(gh)
    event = _pull_request_event(501, head)

    drive(monkeypatch, tmp_path, gh, "pull_request_target", event)
    first = gh.readiness_status(head)
    writes = gh.status_writes

    for _ in range(5):
        drive(monkeypatch, tmp_path, gh, "pull_request_target", event)

    assert gh.readiness_status(head) == first
    assert gh.status_writes == writes


def test_property_22_and_23_timestamps_do_not_affect_the_result(gh, monkeypatch, tmp_path):
    """Properties 22, 23: same-second and out-of-order timestamps are semantically irrelevant."""
    head = ready_pull_request(gh)
    drive(monkeypatch, tmp_path, gh, "schedule", _schedule_event())
    baseline = gh.readiness_status(head)
    baseline_revision = core.read_current_state(501).semantic_revision()

    for timestamp in ("2026-08-13T00:00:00Z", "1999-01-01T00:00:00Z", "2099-12-31T23:59:59Z"):
        for status in gh.statuses[head]:
            status["created_at"] = timestamp
        gh.pulls[501]["updated_at"] = timestamp
        drive(monkeypatch, tmp_path, gh, "workflow_run", _workflow_run_event(head, [501]))

        assert gh.readiness_status(head) == baseline
        assert core.read_current_state(501).semantic_revision() == baseline_revision


def test_property_24_reconciling_twice_with_identical_state_is_idempotent(gh):
    """Property 24: identical state reconciled twice gives an identical result."""
    ready_pull_request(gh)

    first = core.reconcile_pr(501)
    writes = gh.status_writes
    second = core.reconcile_pr(501)

    assert first == second
    assert gh.status_writes == writes


# --- 3, 12, 25: replay, cancellation and concurrency ------------------------


def test_property_3_replayed_workflow_run_is_not_authority_over_newer_state(gh, monkeypatch, tmp_path):
    """Property 3: a replayed workflow_run payload cannot resurrect superseded evidence."""
    old_head = ready_pull_request(gh)
    replayed = _workflow_run_event(old_head, [501], conclusion="success")
    drive(monkeypatch, tmp_path, gh, "workflow_run", replayed)
    assert gh.readiness_status(old_head)[0] == "success"

    new_head = "head_new" * 5
    gh.pulls[501]["head"]["sha"] = new_head
    gh.green_required_checks(new_head)

    # The identical old payload is delivered again, still carrying a success
    # conclusion for the old head. It may only identify PR #501.
    drive(monkeypatch, tmp_path, gh, "workflow_run", replayed)

    assert gh.readiness_status(new_head)[0] == "pending"


def test_property_12_successor_converges_after_a_cancelled_predecessor(gh, monkeypatch, tmp_path):
    """Property 12: a cancelled predecessor run leaves nothing the successor depends on."""
    head = ready_pull_request(gh)
    gh.add_unresolved_thread(501, "THREAD_1")

    # Predecessor: cancelled before it published anything at all.
    gh.resolve_thread(501, "THREAD_1")

    drive(monkeypatch, tmp_path, gh, "pull_request_target", _pull_request_event(501, head))

    assert gh.readiness_status(head)[0] == "success"


def test_property_25_concurrent_schedule_and_pull_request_triggers_converge(gh, monkeypatch, tmp_path):
    """Property 25: a sweep and a webhook run interleaved converge on one result."""
    head = ready_pull_request(gh)

    drive(monkeypatch, tmp_path, gh, "schedule", _schedule_event())
    drive(monkeypatch, tmp_path, gh, "pull_request_target", _pull_request_event(501, head))
    drive(monkeypatch, tmp_path, gh, "schedule", _schedule_event())

    assert gh.readiness_status(head)[0] == "success"
    assert {state for _, state, _ in gh.published} == {"success"}


def test_a_state_change_during_confirmation_withholds_green(gh):
    """A success decision is never published against state that moved underneath it."""
    ready_pull_request(gh)
    original_read = core.read_current_state
    reads = {"n": 0}

    def racing_read(pr_number):
        reads["n"] += 1
        if reads["n"] == 2:
            gh.pulls[pr_number]["body"] = READY_BODY + "\n\nEdited mid-reconciliation.\n"
        return original_read(pr_number)

    core.read_current_state = racing_read
    try:
        decision = core.reconcile_pr(501)
    finally:
        core.read_current_state = original_read

    assert decision.state == "pending"
    assert "changed while readiness was being confirmed" in decision.description


def test_a_blocker_landing_during_the_green_write_is_corrected_before_the_run_ends(gh):
    """A pre-publish check alone cannot close the window around the write itself.

    Models the ordering an independent review raised: this run confirms green,
    and a blocker appears while the status is being written -- a concurrent
    reconciliation may even have published `failure` for it already. The green
    write lands on top. Without the post-publish pass the pull request would sit
    green with an unacknowledged comment until the next sweep.
    """
    head = ready_pull_request(gh)
    original_publish = core.publish
    writes = {"n": 0}

    def publishing_with_a_concurrent_blocker(sha, state, description, published):
        writes["n"] += 1
        if writes["n"] == 1:
            gh.add_comment(501, 900)
        return original_publish(sha, state, description, published)

    core.publish = publishing_with_a_concurrent_blocker
    try:
        decision = core.reconcile_pr(501)
    finally:
        core.publish = original_publish

    assert decision.state == "failure"
    assert gh.readiness_status(head)[0] == "failure"
    assert "900" in gh.readiness_status(head)[1]


def test_a_blocker_cleared_during_the_green_write_leaves_green_in_place(gh):
    """The post-publish pass corrects in both directions, and stays quiet otherwise."""
    head = ready_pull_request(gh)
    original_publish = core.publish
    writes = {"n": 0}

    def publishing_with_a_concurrent_clearance(sha, state, description, published):
        writes["n"] += 1
        if writes["n"] == 1:
            gh.acknowledge(900)
        return original_publish(sha, state, description, published)

    gh.add_comment(501, 900)
    gh.acknowledge(900)
    core.publish = publishing_with_a_concurrent_clearance
    try:
        decision = core.reconcile_pr(501)
    finally:
        core.publish = original_publish

    assert decision.state == "success"
    assert gh.readiness_status(head)[0] == "success"


def test_exhaustion_leaves_no_green_of_its_own_on_the_settled_head(gh):
    """The guarantee is about what the run leaves behind, not about one write.

    Models a force-push storm that returns to previously observed SHAs and then
    settles. Whatever the ordering, when the run returns no `success` it
    published may still be standing on the pull request's current head.
    """
    head_a = "cycling_head_a"
    head_b = "cycling_head_b"
    gh.add_pull_request(501, head_sha=head_a)
    gh.green_required_checks(head_a)
    gh.publish_governance(501)

    original_publish = core.publish
    writes = {"n": 0}
    flips = 5

    def publishing_into_a_force_push_storm(sha, state, description, published):
        result = original_publish(sha, state, description, published)
        writes["n"] += 1
        if writes["n"] > flips:
            return result  # the storm subsides; the head settles
        current = gh.head_sha(501)
        nxt = head_b if current == head_a else head_a
        gh.pulls[501]["head"]["sha"] = nxt
        gh.green_required_checks(nxt)
        gh.statuses[nxt] = [s for s in gh.statuses.get(nxt, []) if s["context"] != "Hunter Governance Review"]
        gh.publish_governance(501)
        return result

    core.publish = publishing_into_a_force_push_storm
    try:
        decision = core.reconcile_pr(501)
    finally:
        core.publish = original_publish

    assert decision.state == "pending"
    settled_head = gh.head_sha(501)
    assert gh.readiness_status(settled_head)[0] != "success"
    assert gh.readiness_status(settled_head)[0] == "pending"


def test_a_sibling_on_another_base_branch_still_blocks_green(gh):
    """Sibling detection must not inherit the sweep's base scoping.

    The candidate-list endpoint is scoped to `base=main`, but a pull request
    targeting a different protected branch shares the very (SHA, context) status
    slot this controller writes. Detecting siblings through that base-scoped list
    would miss it and publish green on a slot another pull request is judged by.
    """
    shared = "cross_base_shared_head"
    gh.add_pull_request(651, head_sha=shared, base_ref="main")
    gh.add_pull_request(652, head_sha=shared, base_ref="release/3.6")
    gh.green_required_checks(shared)
    gh.publish_governance(651)

    state = core.read_current_state(651)

    assert state.shared_head_pull_requests == (652,)
    assert core.decide(state).state == "pending"
    assert "shared with open PR #652" in core.decide(state).description


def test_sibling_detection_ignores_closed_and_non_head_associations(gh):
    """The commit->pulls endpoint is a superset; both filters must do real work.

    Three association shapes reach the filter, and only one of them shares the
    status slot:

    - a *closed* pull request on this exact head — not a sibling;
    - an *open* pull request merely associated with the commit, whose own head
      is elsewhere — not a sibling, because its readiness is judged at its own
      head, not this one;
    - an open pull request whose current head is this commit — a real sibling.
    """
    head = "superset_head"
    gh.add_pull_request(661, head_sha=head)
    gh.add_pull_request(662, head_sha=head, state="closed")
    gh.add_pull_request(663, head_sha="a_completely_different_head")
    gh.associate_commit(663, head)
    gh.green_required_checks(head)
    gh.publish_governance(661)

    state = core.read_current_state(661)

    assert state.shared_head_pull_requests == ()
    assert core.decide(state).state == "success"

    # And the real sibling shape is still caught.
    gh.add_pull_request(664, head_sha=head)

    assert core.read_current_state(661).shared_head_pull_requests == (664,)


def test_a_retracted_green_does_not_survive_on_a_commit_the_head_returns_to(gh):
    """A green on a commit the pull request left must not resurface with the head.

    The run greens a commit, then decides against green while the head is
    elsewhere. Retraction is unconditional, so when the head returns to the
    greened commit it finds `pending`, not this run's stale `success`.
    """
    head_a = "retract_head_a"
    head_b = "retract_head_b"
    gh.add_pull_request(501, head_sha=head_a)
    gh.green_required_checks(head_a)
    gh.publish_governance(501)

    original_publish = core.publish
    writes = {"n": 0}

    def publishing_then_moving_and_blocking(sha, state, description, published):
        result = original_publish(sha, state, description, published)
        writes["n"] += 1
        if writes["n"] == 1:
            # Move to a different head, and make that state non-green.
            gh.pulls[501]["head"]["sha"] = head_b
            gh.green_required_checks(head_b)
            gh.publish_governance(501)
            gh.add_unresolved_thread(501, "THREAD_AFTER_GREEN")
        return result

    core.publish = publishing_then_moving_and_blocking
    try:
        decision = core.reconcile_pr(501)
    finally:
        core.publish = original_publish

    assert decision.state == "failure"
    # The commit this run greened must no longer carry that green, even though
    # it is not the head right now -- a force push can make it the head again.
    assert gh.readiness_status(head_a)[0] == "pending"
    assert gh.readiness_status(head_b)[0] == "failure"


def test_a_converged_success_is_still_held_to_admission(gh, monkeypatch):
    """A success reached during post-publish convergence is not a lesser success.

    The pull request starts as an ordinary, genuinely green change, so the first
    publication is legitimately confirmed. It then becomes a controller-upgrade
    candidate *with* a blocker while the green status is being written. With
    `decide` weakened so it green-lights anything, the independent admission
    kernel is the only thing standing between that state and a published green --
    and it must be consulted on the converged success, not only the first one.
    """
    head = ready_pull_request(gh)
    original_publish = core.publish
    writes = {"n": 0}

    def publishing_then_becoming_a_candidate(sha, state, description, published):
        result = original_publish(sha, state, description, published)
        writes["n"] += 1
        if writes["n"] == 1:
            gh.files[501] = ["scripts/hunter_merge_readiness.py"]
            gh.add_unresolved_thread(501, "THREAD_AFTER_GREEN")
            gh.statuses[sha] = [s for s in gh.statuses[sha] if s["context"] != "Hunter Governance Review"]
            gh.publish_governance(501)
        return result

    monkeypatch.setattr(core, "decide", lambda state: core.ReadinessDecision("success", "weakened rule says ready"))
    core.publish = publishing_then_becoming_a_candidate
    try:
        decision = core.reconcile_pr(501)
    finally:
        core.publish = original_publish

    assert decision.state == "pending"
    assert "Controller-upgrade admission pending" in decision.description
    assert gh.readiness_status(head)[0] == "pending"


def test_retraction_attempts_every_tracked_head_despite_a_failing_write(gh):
    """One failing write must not abandon the remaining retractions.

    Retraction is best-effort by nature -- a job can be cancelled between any two
    statements -- but a transient error on one commit is no reason to skip the
    others, and only commits actually retracted may be dropped from the tracked
    set.
    """
    original_publish = core.publish
    attempted: list[str] = []

    def publishing_with_one_transient_failure(sha, state, description, published):
        attempted.append(sha)
        if sha == "green_b":
            raise RuntimeError("transient GitHub failure")
        return original_publish(sha, state, description, published)

    tracked = {"green_a", "green_b", "green_c"}
    core.publish = publishing_with_one_transient_failure
    try:
        core._retract_greens(tracked)
    finally:
        core.publish = original_publish

    assert sorted(attempted) == ["green_a", "green_b", "green_c"]
    # The failed head stays tracked; the successful ones are cleared.
    assert tracked == {"green_b"}
    assert gh.readiness_status("green_a")[0] == "pending"
    assert gh.readiness_status("green_c")[0] == "pending"


def test_state_changing_faster_than_it_can_be_confirmed_withholds_green(gh):
    """On exhaustion the run withholds green rather than leaving one unconfirmed."""
    head = ready_pull_request(gh)
    original_publish = core.publish
    writes = {"n": 0}

    def publishing_into_a_moving_target(sha, state, description, published):
        writes["n"] += 1
        # Every write is immediately overtaken by a fresh, still-green edit, so
        # the revision never settles but the decision never stops being success.
        gh.pulls[501]["title"] = f"fix: converge merge readiness (edit {writes['n']})"
        gh.statuses[sha] = [s for s in gh.statuses[sha] if s["context"] != "Hunter Governance Review"]
        gh.publish_governance(501)
        return original_publish(sha, state, description, published)

    core.publish = publishing_into_a_moving_target
    try:
        decision = core.reconcile_pr(501)
    finally:
        core.publish = original_publish

    assert decision.state == "pending"
    assert gh.readiness_status(head)[0] == "pending"
    assert "changing faster than readiness can be confirmed" in gh.readiness_status(head)[1]


# --- 4, 5, 6, 20, 21, 26: governance evidence identity ----------------------


def test_property_5_governance_success_for_the_exact_revision_satisfies_readiness(gh):
    """Property 5: evidence naming the exact current revision may satisfy readiness."""
    ready_pull_request(gh)
    state = core.read_current_state(501)

    assert state.governance is not None
    assert state.governance.revision == state.governance_revision
    assert core.decide(state).state == "success"


def test_property_4_governance_success_for_a_previous_revision_cannot_satisfy(gh):
    """Property 4: evidence naming a superseded revision is unusable."""
    head = ready_pull_request(gh)
    superseded = gh.governance_revision_for(501)
    gh.pulls[501]["title"] = "fix: converge merge readiness from current state (v2)"
    gh.statuses[head] = []
    gh.publish_governance(501, "success", revision=superseded)

    state = core.read_current_state(501)

    assert state.governance is None
    assert any("superseded revision" in reason for reason in state.unusable_governance_reasons)
    assert core.decide(state).state == "pending"


def test_property_6_governance_boundary_feedback_invalidates_evidence(gh):
    """Property 6a: human review feedback that changes a governance input invalidates evidence.

    The Hunter Governance Review authority boundary contains the pull request
    body, so acting on review feedback by editing the body changes the governance
    revision and the previous verdict stops applying.
    """
    ready_pull_request(gh)
    assert core.reconcile_pr(501).state == "success"

    gh.pulls[501]["body"] = READY_BODY + "\n\nAddressed reviewer feedback: added replay evidence.\n"

    state = core.read_current_state(501)
    assert state.governance is None
    assert core.decide(state).state == "pending"


def test_property_6_non_governance_feedback_blocks_live_without_invalidating_evidence(gh):
    """Property 6b: feedback outside the governance boundary blocks live, from current state.

    Unresolved threads, CHANGES_REQUESTED reviews and unacknowledged comments are
    not inputs to Hunter Governance Review, so they cannot make its verdict wrong.
    They change the readiness semantic revision and block directly; inventing a
    governance dependency for them is what previously stranded pull requests in a
    permanent "waiting for a fresh Governance Review" state that nothing cleared.
    """
    ready_pull_request(gh)
    before = core.read_current_state(501)

    gh.add_unresolved_thread(501, "THREAD_1")
    after = core.read_current_state(501)

    assert after.semantic_revision() != before.semantic_revision()
    assert after.governance_revision == before.governance_revision
    assert after.governance is not None
    assert core.decide(after).state == "failure"


def test_property_20_base_advance_updates_the_governance_revision(gh):
    """Property 20: the base advancing while the head is constant changes the revision."""
    ready_pull_request(gh)
    before = core.read_current_state(501)
    assert core.decide(before).state == "success"

    gh.base_oids[501] = "base_main_advanced"
    after = core.read_current_state(501)

    assert after.head_sha == before.head_sha
    assert after.governance_revision != before.governance_revision
    assert after.governance is None
    assert core.decide(after).state == "pending"


def test_property_21_head_change_invalidates_prior_governance_evidence(gh):
    """Property 21: evidence for a previous head cannot satisfy a new head."""
    old_head = ready_pull_request(gh)
    assert core.reconcile_pr(501).state == "success"

    new_head = "head_z" * 6
    gh.pulls[501]["head"]["sha"] = new_head
    gh.green_required_checks(new_head)

    state = core.read_current_state(501)

    assert state.governance is None
    assert core.decide(state).state == "pending"
    assert gh.readiness_status(old_head)[0] == "success"


def test_property_26_unattributable_governance_evidence_fails_closed(gh):
    """Property 26: evidence that proves neither owner nor revision is never used."""
    head = ready_pull_request(gh)
    gh.statuses[head] = []
    gh.publish_governance(501, "success", marker=False)

    state = core.read_current_state(501)

    assert state.governance is None
    assert core.decide(state).state == "pending"


# --- 7, 8: noise must not move the semantic revision ------------------------


def test_property_7_trusted_bot_advisory_comment_does_not_change_the_revision(gh):
    """Property 7: a trusted repository-automation advisory comment is not semantic."""
    ready_pull_request(gh)
    before = core.read_current_state(501).semantic_revision()

    gh.add_comment(
        501,
        4242,
        login="github-actions[bot]",
        body="<!-- dependency-review-pr-comment-marker -->\nNo vulnerabilities found.",
    )
    after = core.read_current_state(501)

    assert after.semantic_revision() == before
    assert core.decide(after).state == "success"


def test_an_unknown_bot_comment_still_blocks(gh):
    """The bot exemption is structural, not a blanket bot allowance."""
    ready_pull_request(gh)
    gh.add_comment(501, 4243, login="some-other[bot]", body="advisory")

    assert core.decide(core.read_current_state(501)).state == "failure"


def test_property_8_controller_status_writes_do_not_change_the_revision(gh):
    """Property 8: the controller's own publications are not part of the state it decides from."""
    head = ready_pull_request(gh)
    before = core.read_current_state(501).semantic_revision()

    core.reconcile_pr(501)
    gh.seed_readiness(head, "pending", "an older controller generation left this behind")
    gh.seed_readiness(head, "failure", "and this too")

    assert core.read_current_state(501).semantic_revision() == before


# --- 9, 10, 11: pull-request boundary ---------------------------------------


def test_property_9_governance_evidence_never_crosses_a_shared_head(gh):
    """Property 9: two open pull requests on one head SHA never share evidence.

    Preserves the #257 invariant: GovernanceEvidence(PR=A, HEAD=H) must never
    satisfy MergeReadiness(PR=B, HEAD=H) when A != B.
    """
    shared = "shared_head" * 3
    gh.add_pull_request(601, head_sha=shared)
    gh.add_pull_request(602, head_sha=shared)
    gh.green_required_checks(shared)
    gh.publish_governance(601, "success")

    first = core.read_current_state(601)
    second = core.read_current_state(602)

    assert first.governance is not None
    assert second.governance is None
    assert any("produced for PR #601" in reason for reason in second.unusable_governance_reasons)
    # And neither may publish green: see the publication rule below.
    assert core.decide(first).state == "pending"
    assert core.decide(second).state == "pending"


def test_a_shared_head_withholds_green_because_the_status_slot_is_shared(gh):
    """A readiness status is keyed by (SHA, context), not by pull request.

    Branch protection evaluates that same status object for every open pull
    request whose head is that commit. Publishing green for a ready pull request
    would therefore assert mergeability for a pull request sharing the head whose
    own blockers were never evaluated, so a shared head must fail closed.
    """
    shared = "shared_publication_head"
    gh.add_pull_request(611, head_sha=shared)
    gh.add_pull_request(612, head_sha=shared)
    gh.green_required_checks(shared)
    gh.publish_governance(611, "success")
    gh.publish_governance(612, "success")
    # PR #612 carries a blocker; PR #611 is fully ready.
    gh.add_comment(612, 900)

    ready = core.decide(core.read_current_state(611))
    blocked = core.decide(core.read_current_state(612))

    assert blocked.state == "failure"
    assert ready.state == "pending"
    assert "shared with open PR #612" in ready.description


def test_a_shared_head_that_becomes_unique_converges_to_green(gh):
    """The withholding is derived from current state, not a sticky penalty."""
    shared = "shared_then_unique_head"
    gh.add_pull_request(621, head_sha=shared)
    gh.add_pull_request(622, head_sha=shared)
    gh.green_required_checks(shared)
    gh.publish_governance(621, "success")
    assert core.reconcile_pr(621).state == "pending"

    gh.pulls[622]["head"]["sha"] = "a_different_head"

    assert core.reconcile_pr(621).state == "success"
    assert gh.readiness_status(shared)[0] == "success"


def test_property_10_ambiguous_trigger_identity_reconciles_each_pr_on_its_own_evidence(gh, monkeypatch, tmp_path):
    """Property 10: an ambiguous trigger identifies candidates; it never lends authority.

    A workflow_run payload carrying several associated pull requests (or none, on
    a head shared by several) cannot say which pull request was evaluated. Under
    the current-state model that is harmless for identification -- every candidate
    is reconciled -- and still fails closed for evidence, because each pull request
    is judged solely against evidence that names it.
    """
    shared = "ambiguous_head" * 2
    gh.add_pull_request(701, head_sha=shared)
    gh.add_pull_request(702, head_sha=shared)
    gh.green_required_checks(shared)
    gh.publish_governance(701, "success")

    drive(monkeypatch, tmp_path, gh, "workflow_run", _workflow_run_event(shared, [701, 702]))

    # Both candidates were identified and reconciled, each against evidence
    # naming it -- and neither published green, because the shared status slot
    # cannot express readiness for one pull request without asserting it for the
    # other.
    assert {state for _, state, _ in gh.published} == {"pending"}
    assert core.read_current_state(701).governance is not None
    assert core.read_current_state(702).governance is None


def test_property_11_unique_exact_attribution_takes_the_normal_path(gh, monkeypatch, tmp_path):
    """Property 11: unambiguous attribution works normally."""
    head = ready_pull_request(gh)

    drive(monkeypatch, tmp_path, gh, "workflow_run", _workflow_run_event(head, [501]))

    assert gh.readiness_status(head)[0] == "success"


def test_workflow_run_without_associations_recovers_candidates_by_head(gh, monkeypatch, tmp_path):
    head = ready_pull_request(gh)

    drive(monkeypatch, tmp_path, gh, "workflow_run", _workflow_run_event(head, []))

    assert gh.readiness_status(head)[0] == "success"


# --- 13, 14, 15, 16, 17, 18, 19: repair paths converge ----------------------


def test_property_13_scheduled_sweep_converges_after_a_missed_event(gh, monkeypatch, tmp_path):
    """Property 13: a sweep converges even though no event ever announced the change."""
    head = ready_pull_request(gh)
    gh.seed_readiness(head, "pending", "Waiting for current Hunter Governance Review.")

    drive(monkeypatch, tmp_path, gh, "schedule", _schedule_event())

    assert gh.readiness_status(head)[0] == "success"


def test_property_14_manual_dispatch_converges_a_stuck_status(gh, monkeypatch, tmp_path):
    """Property 14: manual reconciliation uses the same path and converges."""
    head = ready_pull_request(gh)
    gh.seed_readiness(head, "pending", "Waiting for a fresh Hunter Governance Review (stuck forever).")

    drive(monkeypatch, tmp_path, gh, "workflow_dispatch", _dispatch_event(501))

    assert gh.readiness_status(head)[0] == "success"


def test_manual_and_scheduled_dispatch_have_no_special_decision_semantics(gh, monkeypatch, tmp_path):
    """Neither schedule nor workflow_dispatch may decide differently from a webhook."""
    head = ready_pull_request(gh)
    gh.add_unresolved_thread(501, "THREAD_1")

    results = []
    for event_name, event in (
        ("schedule", _schedule_event()),
        ("workflow_dispatch", _dispatch_event(501)),
        ("pull_request_target", _pull_request_event(501, head)),
        ("issue_comment", _issue_comment_event(501)),
        ("pull_request_review", _review_event(501, head)),
    ):
        drive(monkeypatch, tmp_path, gh, event_name, event)
        results.append(gh.readiness_status(head))

    assert len(set(results)) == 1
    assert results[0][0] == "failure"


def test_property_15_stale_pending_is_replaced_by_the_current_terminal_state(gh, monkeypatch, tmp_path):
    """Property 15: a stale pending from a historical controller is replaced."""
    head = ready_pull_request(gh)
    gh.seed_readiness(head, "pending", "Waiting for a fresh Hunter Governance Review (last run was older...).")

    drive(monkeypatch, tmp_path, gh, "schedule", _schedule_event())

    assert gh.readiness_status(head)[0] == "success"


def test_property_16_stale_failure_is_replaced_when_current_state_is_ready(gh, monkeypatch, tmp_path):
    """Property 16: a stale failure is replaced once current state is ready."""
    head = ready_pull_request(gh)
    gh.seed_readiness(head, "failure", "Timed out waiting for fresh exact-head prerequisites.")

    drive(monkeypatch, tmp_path, gh, "schedule", _schedule_event())

    assert gh.readiness_status(head)[0] == "success"


def test_property_17_and_18_thread_state_is_always_read_from_current_truth(gh, monkeypatch, tmp_path):
    """Properties 17, 18: an unresolved thread blocks; resolving it converges next time."""
    head = ready_pull_request(gh)
    gh.add_unresolved_thread(501, "THREAD_1")
    drive(monkeypatch, tmp_path, gh, "schedule", _schedule_event())
    assert gh.readiness_status(head)[0] == "failure"

    gh.resolve_thread(501, "THREAD_1")
    drive(monkeypatch, tmp_path, gh, "schedule", _schedule_event())

    assert gh.readiness_status(head)[0] == "success"


def test_property_19_owner_acknowledgement_converges_without_event_history(gh, monkeypatch, tmp_path):
    """Property 19: an acknowledgement takes effect with no knowledge of how it arrived.

    GitHub emits no event for a reaction, so nothing announces this change. The
    next reconciliation sees it purely by reading current state.
    """
    head = ready_pull_request(gh)
    gh.add_comment(501, 900)
    drive(monkeypatch, tmp_path, gh, "schedule", _schedule_event())
    assert gh.readiness_status(head)[0] == "failure"

    gh.acknowledge(900)
    drive(monkeypatch, tmp_path, gh, "schedule", _schedule_event())

    assert gh.readiness_status(head)[0] == "success"


# --- 27: publication target -------------------------------------------------


def test_property_27_readiness_is_never_published_against_a_base_sha(gh, monkeypatch, tmp_path):
    """Property 27: no path publishes readiness against the base/main SHA."""
    head = ready_pull_request(gh)
    base = gh.base_oids[501]

    for event_name, event in (
        ("schedule", _schedule_event()),
        ("workflow_run", _workflow_run_event(head, [501])),
        ("workflow_dispatch", _dispatch_event(501)),
        ("pull_request_target", _pull_request_event(501, head)),
    ):
        drive(monkeypatch, tmp_path, gh, event_name, event)

    assert {sha for sha, _, _ in gh.published} == {head}
    assert base not in {sha for sha, _, _ in gh.published}


# --- the decision function itself -------------------------------------------


def _state_matrix(gh) -> list:
    """A spread of distinct current states, each read live from the harness."""
    states = []
    ready_pull_request(gh)
    states.append(core.read_current_state(501))

    gh.add_unresolved_thread(501, "THREAD_1")
    states.append(core.read_current_state(501))
    gh.resolve_thread(501, "THREAD_1")

    gh.request_changes(501, "reviewer")
    states.append(core.read_current_state(501))
    gh.reviews[501] = []

    gh.add_comment(501, 900)
    states.append(core.read_current_state(501))
    gh.acknowledge(900)

    gh.pulls[501]["draft"] = True
    states.append(core.read_current_state(501))
    gh.pulls[501]["draft"] = False

    gh.base_oids[501] = "base_main_advanced"
    states.append(core.read_current_state(501))
    return states


def test_decide_is_a_function_of_the_semantic_revision_alone(gh):
    """Equal semantic revision implies equal decision; different decision implies different revision."""
    by_revision: dict[str, core.ReadinessDecision] = {}
    for state in _state_matrix(gh):
        decision = core.decide(state)
        revision = state.semantic_revision()
        if revision in by_revision:
            assert by_revision[revision] == decision
        else:
            by_revision[revision] = decision

    decisions = list(by_revision.values())
    assert len(set(decisions)) > 1, "the matrix must exercise genuinely different outcomes"


def test_decide_never_consults_the_event_name(gh):
    """The trigger cannot influence the decision, because decide() cannot see it."""
    ready_pull_request(gh)
    state = core.read_current_state(501)

    results = set()
    for name in ("schedule", "workflow_run", "workflow_dispatch", "pull_request_target", "", "anything"):
        core.event_name = name
        results.add(core.decide(state))

    assert len(results) == 1


# --- non-vacuousness --------------------------------------------------------


def test_non_vacuousness_bypassing_revision_matching_breaks_convergence(gh):
    """Reverting the new reconciler logic must make these properties fail.

    This reintroduces the pre-redesign behaviour -- accept any Governance Review
    status on the exact head, without proving which pull request or which state it
    describes -- and shows that properties 4, 9 and 26 stop holding. If a future
    change made the current tests pass for the wrong reason, this test would fail
    first, because it asserts that the weakened implementation is *detectably*
    different.
    """
    # An unshared head carrying evidence stamped for a different pull request.
    # Deliberately not a shared head, so the demonstration isolates the identity
    # check rather than the shared-head publication rule.
    head = "foreign_marker_head"
    gh.add_pull_request(802, head_sha=head)
    gh.green_required_checks(head)
    gh.publish_governance(802, "success", marked_pull_request=801)

    # Current behaviour: PR #802 cannot use evidence stamped for PR #801.
    assert core.read_current_state(802).governance is None
    assert core.decide(core.read_current_state(802)).state == "pending"

    def legacy_resolve(statuses, pr_number, revision):
        """Pre-redesign resolution: newest same-head status wins, no identity proof."""
        matches = [s for s in statuses if s.get("context") == core.governance_context]
        if not matches:
            return None, ()
        newest = max(matches, key=lambda s: int(s.get("id", 0)))
        return (
            core.GovernanceEvidence(
                state=(newest.get("state") or "pending").strip(),
                pull_request_number=int(pr_number),
                revision=revision,
            ),
            (),
        )

    original = core.resolve_governance_evidence
    core.resolve_governance_evidence = legacy_resolve
    try:
        leaked = core.decide(core.read_current_state(802))
    finally:
        core.resolve_governance_evidence = original

    assert leaked.state == "success", "the weakened implementation must actually leak, or this proves nothing"
    assert core.decide(core.read_current_state(802)).state == "pending"


def test_non_vacuousness_trusting_the_trigger_payload_breaks_replay_safety(gh, monkeypatch, tmp_path):
    """A payload-trusting adapter must be observably different from the current one.

    The pre-redesign controller passed the ``workflow_run`` payload's conclusion
    and start time into the evaluation. Simulating that here shows a replayed
    payload would decide readiness for state the payload never observed, which is
    exactly what ``candidate_pull_requests`` now structurally prevents by
    returning only pull-request numbers.
    """
    head = ready_pull_request(gh)
    payload = _workflow_run_event(head, [501], conclusion="success")

    # Structural proof: the adapter's entire output is a list of PR numbers.
    core.event_name = "workflow_run"
    assert core.candidate_pull_requests("workflow_run", payload) == [501]

    # Even a payload claiming success for a head the PR has moved past cannot
    # produce green, because the payload is never consulted for a verdict.
    new_head = "moved_head" * 4
    gh.pulls[501]["head"]["sha"] = new_head
    gh.green_required_checks(new_head)
    drive(monkeypatch, tmp_path, gh, "workflow_run", payload)

    assert gh.readiness_status(new_head)[0] == "pending"
