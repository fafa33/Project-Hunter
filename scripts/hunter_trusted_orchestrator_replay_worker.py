"""Trusted, process-isolated worker for the orchestrator replay harness.

This module is trusted code: both the validate-replay and publish-proof jobs
check it out from the default branch, and it is always invoked as a fresh
subprocess by ``hunter_trusted_orchestrator_replay.py`` -- never imported into
that privileged process. Isolation matters because each scenario has to import
the *candidate's* copy of the controller modules under test, and a candidate
branch is untrusted input: importing it into the same process that will later
publish a status would let arbitrary top-level side effects in candidate code
run with that process's privileges. Running it as a subprocess whose only
inputs are a repository-relative candidate root, a scenario id and a fixture
path, and whose only output is one structured JSON line on stdout, bounds what
a hostile candidate module can do to "passing" a scenario: it can make its own
functions behave however it likes, but it cannot reach into the parent
process, and the parent independently recomputes which candidate files backed
the result (see ``candidate_module_digest`` below and
``hunter_trusted_orchestrator_replay.py::validate_receipt``).

Every scenario calls the candidate's *real* functions end-to-end (not a
reimplementation of the invariant) and asserts semantic properties, never a
hardcoded expected timeout or a literal string identical to today's code, so a
canonically valid future change to the candidate's own timeout math or
messages does not spuriously fail replay.

Each scenario asserts the invariant the candidate's own production code
documents. It never asserts an implementation shape the production code does
not actually implement: requiring an API, or an execution the production design
explicitly rules out, would reject a canonically valid candidate for
harness/production drift rather than for a real regression.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import multiprocessing as mp
import os
import sys
import traceback
from datetime import UTC, datetime, timedelta
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

#: The exact candidate files each scenario reads or imports. Recorded so the
#: digest a scenario reports is provably scoped to the modules it actually
#: touched, and so the trusted validator can recompute the identical digest
#: from its own, independent candidate checkout.
CANDIDATE_MODULE_FILES: dict[str, tuple[str, ...]] = {
    "A": ("scripts/hunter_review_orchestrator.py",),
    "B": ("scripts/hunter_review_orchestrator.py",),
    "G": (
        "scripts/hunter_governance_review_v2.py",
        "scripts/hunter_review_orchestrator.py",
    ),
}


#: The state production records for a cycle still awaiting its collector. Read
#: from the candidate module rather than hardcoded, so a canonically valid
#: rename cannot be mistaken for a regression.
WAITING_FOR_REVIEWER_STATE = "WAITING_FOR_REVIEWER"


def candidate_module_digest(candidate_root: Path, scenario_id: str) -> str:
    payload = {}
    for relative in CANDIDATE_MODULE_FILES[scenario_id]:
        payload[relative] = (candidate_root / relative).read_text(encoding="utf-8")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _import_candidate(candidate_root: Path, module_name: str) -> Any:
    scripts_dir = str((candidate_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module(module_name)


#: Trusted upper bound on how long a pending cycle may stay open while its
#: correlated collector still reads active, as a multiple of the candidate's own
#: declared opportunity. Owned by the grader so a candidate cannot widen it.
ACTIVE_COLLECTOR_CEILING_MULTIPLIER = 2


def scenario_a(candidate_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    """A pending cycle always reaches a terminal state inside a bounded budget.

    The candidate's own contract for this (see
    ``independent_review_opportunity_seconds``) is that one global opportunity
    budget spans the whole reviewer chain, and that a completed or timed-out
    opportunity must never leave the exact-head status pending forever. That
    budget is derived from the candidate's own trusted reviewer pool, so it is
    read by calling the candidate's own derivation rather than by reading a
    module-level constant. This scenario validates the contract through the
    candidate's real ``ensure_collector`` and asserts nothing about how the
    budget is *derived*, because production is the authority on its own budget
    and does not promise the candidate reviewer pool's chain length:

    1. The published opportunity is a real bound: a strictly positive, finite
       integer, not an absent or open-ended one.
    2. A recorded pending cycle whose age is immediately *above* the candidate's
       own declared budget is driven to ``REVIEW_TIMED_OUT`` and published, with
       no collector dispatch, so the exact head stops reading as pending.
    3. A recorded pending cycle whose age is immediately *below* that same budget
       is left alone.

    The two ages straddle the boundary by the smallest step production's own
    timestamps can express, so together they prove the declared budget *is* the
    terminality threshold. Sampling far apart (an order of magnitude either side)
    would only prove some threshold exists between them: a candidate whose real
    cutoff is 1.5x its declared budget would pass such a check while still
    leaving an exact head pending well past the bound it advertises.

    The boundary is straddle-probed with a cycle whose correlated collector is
    NOT active, because that is the case where the nominal budget alone governs.
    A second, separate probe then covers the case production added for it:
    ``started_at`` is written before dispatch, so queue delay is spent out of the
    budget while the collector is still queued or in progress. Production must
    not finalize such a cycle on the nominal deadline, and must still finalize it
    once the grader-owned ``ACTIVE_COLLECTOR_CEILING_MULTIPLIER`` ceiling is
    passed -- so the wait stays bounded rather than becoming an unbounded one.
    """

    orchestrator = _import_candidate(candidate_root, "hunter_review_orchestrator")
    head = fixture["head_sha"]
    pr_number = int(fixture["pr_number"])
    digest = fixture["config_digest"]
    run_id = int(fixture["run_id"])

    budget = orchestrator.independent_review_opportunity_seconds()
    assert isinstance(budget, int) and not isinstance(budget, bool), (
        "the candidate must publish an integer review-opportunity budget "
        f"(independent_review_opportunity_seconds()), got {budget!r}"
    )
    assert budget > 0, f"the review-opportunity budget must be strictly positive, got {budget}s"

    #: ``started_at`` is written at whole-second resolution and ``_older_than``
    #: compares ``elapsed >= seconds`` against the live clock, so the boundary
    #: can only be approached to the second. One second of slack on the inside
    #: keeps the sub-budget probe unambiguously inside even if the reconcile
    #: lands on the following second, and keeps both probes deterministic
    #: instead of racing the wall clock.
    step = 2
    below_age = budget - step
    above_age = budget + step
    assert below_age > 0, (
        f"a {budget}s declared budget is too small to probe its own boundary within production's "
        f"{step}s timestamp resolution"
    )

    state: dict[str, Any] = {"dispatches": 0, "published": []}
    orchestrator.reviewer_pool_config_digest = lambda: digest
    orchestrator.current_run_id = lambda: run_id
    orchestrator.publish_cycle = lambda *_a, cycle: state["published"].append(cycle)
    orchestrator.dispatch_collector = lambda *_a: state.__setitem__("dispatches", state["dispatches"] + 1)
    # The straddle probes are the nominal-boundary case, so the correlated
    # collector has already completed and the declared budget alone decides the
    # transition. Stubbing it "active" here would demand that a still-running
    # collector be finalized on the nominal deadline, which is not part of this
    # invariant and would leave that live collector free to publish a competing
    # terminal result for the same exact head (DFF-041, PR #535).
    orchestrator.collector_liveness = lambda *_a: ("completed", 1)

    def _recorded(age_seconds: int) -> Any:
        started = (datetime.now(UTC) - timedelta(seconds=age_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return orchestrator.ReviewCycle(
            pr_number=pr_number,
            head_sha=head,
            state="WAITING_FOR_REVIEWER",
            provider_id="codex",
            trigger_id=run_id,
            started_at=started,
            config_digest=digest,
        )

    over_budget = _recorded(above_age)
    orchestrator.read_cycle = lambda *_a: ("present", over_budget, None)
    timed_out = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)
    assert timed_out.state == "REVIEW_TIMED_OUT", (
        f"a pending cycle {above_age}s old must reach a terminal state under a declared {budget}s opportunity; "
        f"it stayed {timed_out.state!r}, so the candidate's real timeout threshold is wider than the budget it "
        "declares and an exact head can stay pending past the bound it advertises"
    )
    assert state["dispatches"] == 0, "a timed-out opportunity must not dispatch a further collector"
    assert [c.state for c in state["published"]] == ["REVIEW_TIMED_OUT"], (
        f"the terminal transition must be published so the exact head stops reading as pending, "
        f"published {[c.state for c in state['published']]}"
    )

    state["dispatches"] = 0
    state["published"].clear()
    within_budget = _recorded(below_age)
    orchestrator.read_cycle = lambda *_a: ("present", within_budget, None)
    still_pending = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)
    assert still_pending.state == within_budget.state, (
        f"a pending cycle {below_age}s old must not be timed out under a declared {budget}s opportunity, "
        f"got {still_pending.state!r}"
    )
    assert state["dispatches"] == 0, "a live cycle still inside the opportunity must not be re-dispatched"
    assert state["published"] == [], "a live cycle still inside the opportunity must not be republished"

    # A correlated collector that is genuinely active must not be finalized on
    # the nominal deadline: queue delay is spent out of the budget before the
    # collector starts, and finalizing there would end the opportunity while a
    # live collector can still publish a competing result for this exact head.
    active_over_budget = _recorded(above_age)
    orchestrator.read_cycle = lambda *_a: ("present", active_over_budget, None)
    orchestrator.collector_liveness = lambda *_a: ("active", 1)
    state["dispatches"] = 0
    state["published"].clear()
    deferred = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)
    assert deferred.state != "REVIEW_TIMED_OUT", (
        f"a pending cycle {above_age}s old with a correlated collector still active must not be finalized as "
        f"timed out on the nominal {budget}s budget -- it stayed on the nominal path instead -- because queue "
        "and dispatch delay are spent out of that budget before the collector starts"
    )
    assert (
        deferred.state == active_over_budget.state
    ), f"an active correlated collector must leave the cycle in its existing state, got {deferred.state!r}"
    assert state["published"] == [], (
        "withholding a timeout must publish nothing at all, so no competing terminal result can exist "
        f"for this head, published {[c.state for c in state['published']]}"
    )
    assert state["dispatches"] == 0, "an active correlated collector must not be re-dispatched"

    # A still-running correlated collector may hold the cycle open past the
    # nominal budget, but never without bound: the trusted grader -- not the
    # candidate -- owns the ceiling, so a wedged or perpetually active run
    # cannot leave the exact head pending forever.
    ceiling_age = budget * ACTIVE_COLLECTOR_CEILING_MULTIPLIER + step
    orchestrator.collector_liveness = lambda *_a: ("active", 1)
    active_past_ceiling = _recorded(ceiling_age)
    orchestrator.read_cycle = lambda *_a: ("present", active_past_ceiling, None)
    state["dispatches"] = 0
    state["published"].clear()
    ceiling_timed_out = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)
    assert ceiling_timed_out.state == "REVIEW_TIMED_OUT", (
        f"a pending cycle {ceiling_age}s old whose correlated collector still reads active must reach a terminal "
        f"state within {ACTIVE_COLLECTOR_CEILING_MULTIPLIER}x its declared {budget}s opportunity; it stayed "
        f"{ceiling_timed_out.state!r}, so an active collector can hold the exact head pending without bound"
    )
    assert state["dispatches"] == 0, "a timed-out opportunity must not dispatch a further collector"
    assert [c.state for c in state["published"]] == ["REVIEW_TIMED_OUT"], (
        "the ceiling timeout must publish exactly one terminal transition, "
        f"published {[c.state for c in state['published']]}"
    )

    return {
        "opportunity_seconds": budget,
        "below_boundary_seconds": below_age,
        "above_boundary_seconds": above_age,
        "timed_out_state": timed_out.state,
        "in_opportunity_state": still_pending.state,
        "active_deferred_state": deferred.state,
        "active_ceiling_seconds": ceiling_age,
        "active_ceiling_state": ceiling_timed_out.state,
    }


def scenario_b(candidate_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    """Exact-head collector dispatch stays idempotent and bounded.

    The candidate's own collector contract (see ``ensure_collector``) is that
    the published cycle is the durable idempotency record -- a reconcile that
    can observe the prior write checks correlated collector runs instead of
    blindly issuing a duplicate dispatch -- and that a cycle whose collector
    never produced a run recovers through a bounded liveness grace rather than
    dispatching without end. This scenario validates the real durable sequence
    through the candidate's real ``ensure_collector``, where every step reads
    back only what production actually published, and no liveness reading is
    substituted for the durable record:

    1. A first reconcile against an exact head with nothing recorded yet
       dispatches exactly one collector and publishes the durable cycle.
    2. A reconcile that observes that durable record dispatches nothing
       further and returns it unchanged.
    3. A recorded cycle whose collector produced no run at all -- liveness
       exactly ``("missing", 0)`` -- and that exceeds the liveness grace
       recovers exactly one further dispatch. Whether a dispatch produces a
       run is modelled, not assumed, so this probe is genuinely distinct from
       the dead-run case.
    4. That recovery refreshes the durable record: a fresh durable cycle is
       published for it. A candidate that dispatches the recovery without
       republishing leaves the stale record in place, so this step fails.
    5. That exact refreshed record is then reconciled again, with nothing
       aged or substituted in between. A refresh that kept an already-stale
       liveness timestamp would be immediately re-dispatchable, so a candidate
       that publishes a distinct record without resetting the liveness clock
       fails here rather than passing behind a synthesized replacement.
    6. Only then are records aged to spend the dispatch budget that real runs
       have accumulated; the cycle stays pending and dispatches no more once
       that budget is spent, rather than dispatching without end.
    """

    orchestrator = _import_candidate(candidate_root, "hunter_review_orchestrator")
    head = fixture["head_sha"]
    pr_number = int(fixture["pr_number"])
    digest = fixture["config_digest"]
    run_id = int(fixture["run_id"])

    grace = int(orchestrator.COLLECTOR_LIVENESS_GRACE_SECONDS)
    max_dispatches = int(orchestrator.MAX_COLLECTOR_DISPATCHES)
    opportunity = int(orchestrator.independent_review_opportunity_seconds())
    assert max_dispatches >= 1, f"the collector dispatch budget must allow at least one dispatch, got {max_dispatches}"
    assert 0 < grace < opportunity, (
        f"the collector liveness grace ({grace}s) must be shorter than the review opportunity "
        f"({opportunity}s) so a dead collector is recovered before the cycle times out"
    )

    # Dispatches start out leaving no correlated run at all, which is a real
    # outcome of a dispatch and the state the missing-run recovery probe needs.
    # Run creation is switched on only for the later dead-run budget exercise.
    state: dict[str, Any] = {"dispatches": 0, "published": [], "runs": [], "runs_created": False}
    orchestrator.reviewer_pool_config_digest = lambda: digest
    orchestrator.current_run_id = lambda: run_id
    orchestrator.publish_cycle = lambda *_a, cycle: state["published"].append(cycle)

    def fake_dispatch_collector(*_args: Any) -> None:
        state["dispatches"] += 1
        # A dispatched collector normally leaves a correlated run for this
        # exact head, which is what production counts toward
        # MAX_COLLECTOR_DISPATCHES. Whether a run appears is a real, separate
        # outcome of a dispatch: a dispatch can be accepted and still never
        # produce a run, which is precisely the case
        # ``collector_needs_dispatch`` exists to recover. ``runs_created``
        # models that, so the missing-run path is exercised against real
        # production rather than assumed.
        if state["runs_created"]:
            state["runs"].append(head)

    orchestrator.dispatch_collector = fake_dispatch_collector

    #: Real liveness evidence, derived from the runs this scenario's own
    #: dispatch hook created. Nothing here is substituted for the durable
    #: record: the count is the number of correlated runs, exactly as
    #: ``collector_liveness`` reports it, and a dispatch that produced no run
    #: reports the production-valid ``("missing", 0)``.
    def fake_collector_liveness(
        _repository: str, _token: str, liveness_pr: int, liveness_head: str, generation_id: str = ""
    ) -> tuple[str, int]:
        count = sum(1 for run in state["runs"] if run == liveness_head)
        if not count:
            return ("missing", 0)
        return ("dead", count)

    orchestrator.collector_liveness = fake_collector_liveness

    def _recorded(age_seconds: int) -> Any:
        started = (datetime.now(UTC) - timedelta(seconds=age_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return orchestrator.ReviewCycle(
            pr_number=pr_number,
            head_sha=head,
            state=WAITING_FOR_REVIEWER_STATE,
            provider_id="codex",
            trigger_id=run_id,
            started_at=started,
            config_digest=digest,
        )

    def _read_durable() -> Any:
        """Reconcile against exactly what production last published.

        ``read_cycle`` returns the newest durable cycle, so every later step
        consumes the real record rather than a liveness reading standing in
        for it.
        """

        durable = state["published"][-1]
        orchestrator.read_cycle = lambda *_a: ("present", durable, None)
        return durable

    orchestrator.read_cycle = lambda *_a: ("absent", None, None)
    first = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)
    assert (
        first.state == WAITING_FOR_REVIEWER_STATE
    ), f"a first reconcile must record a pending cycle, got {first.state!r}"
    assert state["dispatches"] == 1, f"a first reconcile must dispatch exactly one collector, got {state['dispatches']}"
    assert state["published"], "a first reconcile must publish the durable cycle that records its dispatch"

    recorded = _read_durable()
    second = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)
    third = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)
    assert state["dispatches"] == 1, (
        "repeated reconciles that observe the durable record must dispatch no further collector, got "
        f"{state['dispatches'] - 1} duplicate dispatch(es) against a {max_dispatches}-dispatch budget"
    )
    assert (
        second.state == recorded.state and third.state == recorded.state
    ), f"reconciles that observe the durable record must return it unchanged, got {second.state!r} and {third.state!r}"

    # Recovery with no correlated run at all. A dispatch can be accepted and
    # still never produce a run, which production reports as ("missing", 0);
    # a cycle in that state must be recovered rather than left pending
    # forever. Every dispatch so far produced no run, so this is the genuine
    # production state rather than a substituted reading -- pinned explicitly
    # so the probe cannot quietly degenerate into the dead-run case below.
    assert orchestrator.collector_liveness("owner/repo", "token", pr_number, head) == (
        "missing",
        0,
    ), "this probe must recover a cycle whose collector produced no run at all"
    stale = _recorded(grace + 1)
    state["published"].append(stale)
    published_before_recovery = len(state["published"])
    _read_durable()
    recovered = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)
    assert state["dispatches"] == 2, (
        f"a durable record with no correlated collector run must recover exactly one further dispatch after the "
        f"{grace}s liveness grace, got {state['dispatches'] - 1}"
    )
    assert (
        recovered.state == WAITING_FOR_REVIEWER_STATE
    ), f"a recovered cycle must stay pending, got {recovered.state!r}"

    # The recovery must refresh the durable record. Reading back the newest
    # published cycle is what proves this, and the check is relative so it stays
    # valid however many records the sequence has produced. A candidate that
    # re-dispatches without republishing leaves the stale record as the newest
    # one, so the same exact head would be recovered again on every later
    # reconcile while the record it was recovered for never changed.
    refreshed = _read_durable()
    assert refreshed is not stale, (
        "a recovery dispatch must publish/refresh the durable cycle record; this candidate re-dispatched without "
        f"updating it, so the stale record ({stale.started_at}) is still the newest durable record and every later "
        "reconcile would recover the same exact head again"
    )
    assert len(state["published"]) == published_before_recovery + 1, (
        "a recovery dispatch must publish exactly one refreshed durable cycle, published "
        f"{len(state['published']) - published_before_recovery}"
    )

    # The refreshed record must be usable as-is. Reconciling it immediately,
    # before this scenario ages anything, is what proves the refresh actually
    # reset the liveness clock rather than publishing a distinct record that is
    # still stale: a refresh that kept an already-stale started_at would be
    # eligible for re-dispatch on this very next reconcile, and the exact head
    # would be re-dispatched over and over. No replacement record is
    # synthesized between the recovery and this proof.
    _read_durable()
    settled = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)
    assert state["dispatches"] == 2, (
        "reconciling the refreshed durable record must dispatch no further collector; a refresh that preserves an "
        f"already-stale liveness timestamp is immediately re-dispatchable, and {state['dispatches'] - 2} extra "
        "dispatch(es) occurred"
    )
    assert (
        settled is refreshed or settled.state == refreshed.state
    ), f"the refreshed durable record must be the current authority, got {settled.state!r}"
    assert not orchestrator._older_than(refreshed.started_at, orchestrator.COLLECTOR_LIVENESS_GRACE_SECONDS), (
        f"the refreshed durable record ({refreshed.started_at}) is already older than the "
        f"{grace}s liveness grace, so it would be re-dispatched on the next reconcile"
    )

    # Dispatch-budget exhaustion is exercised only after that proof, and on the
    # dead-run path. Dispatches now leave a correlated run, which is what
    # production actually counts toward MAX_COLLECTOR_DISPATCHES -- the two
    # earlier dispatches left none, so they correctly do not consume the budget.
    # The budget arithmetic stays production's: runs are counted per head and
    # read back through the candidate's own liveness listing.
    state["runs_created"] = True

    def _correlated_runs() -> int:
        return sum(1 for run in state["runs"] if run == head)

    while _correlated_runs() < max_dispatches:
        state["published"].append(_recorded(grace + 1))
        _read_durable()
        orchestrator.ensure_collector("owner/repo", "token", pr_number, head)

    dispatches_when_exhausted = state["dispatches"]
    state["published"].append(_recorded(grace + 1))
    _read_durable()
    exhausted = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)
    assert state["dispatches"] == dispatches_when_exhausted, (
        "a cycle whose real accumulated runs have spent the dispatch budget must dispatch no further collector, "
        f"got {state['dispatches'] - dispatches_when_exhausted} more"
    )
    assert _correlated_runs() == max_dispatches, (
        f"the real correlated-run listing must reach the dispatch budget, got {_correlated_runs()} of "
        f"{max_dispatches} (MAX_COLLECTOR_DISPATCHES)"
    )
    assert (
        exhausted.state == WAITING_FOR_REVIEWER_STATE
    ), f"an exhausted cycle must stay {WAITING_FOR_REVIEWER_STATE!r}, got {exhausted.state!r}"

    return {
        "dispatches": state["dispatches"],
        "correlated_runs": _correlated_runs(),
        "durable_records_published": len(state["published"]),
        "liveness_grace_seconds": grace,
        "max_dispatches": max_dispatches,
        "exhausted_state": exhausted.state,
    }


def scenario_g(candidate_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    """WAITING_FOR_REVIEWER classifies to pending end-to-end via the real path.

    Calls the candidate's real ``hunter_governance_review_v2.pending_review_authority_state``,
    which itself calls the real ``hunter_review_orchestrator.review_orchestration_state``:
    no reclassification logic is reimplemented here. Only the innermost
    ``read_cycle`` network read is replaced, so the full resolution path between
    it and the merge-readiness-facing classification runs unmodified.
    """

    governance = _import_candidate(candidate_root, "hunter_governance_review_v2")
    orchestration = _import_candidate(candidate_root, "hunter_review_orchestrator")
    head = fixture["head_sha"]
    pr_number = int(fixture["pr_number"])

    orchestration.read_cycle = lambda *_a: ("absent", None, None)
    state, detail = governance.pending_review_authority_state("owner/repo", "token", pr_number, head)
    assert state == "pending", f"expected pending classification for an absent cycle, got {state!r}"
    assert detail.startswith("WAITING_FOR_REVIEWER:"), f"expected a WAITING_FOR_REVIEWER detail, got {detail!r}"

    cycle = orchestration.ReviewCycle(
        pr_number=pr_number,
        head_sha=head,
        state="REVIEW_IN_PROGRESS",
        provider_id="codex",
        trigger_id=1,
        started_at="2026-01-01T00:00:00Z",
        config_digest=fixture["config_digest"],
    )
    orchestration.read_cycle = lambda *_a: ("present", cycle, None)
    state2, _detail2 = governance.pending_review_authority_state("owner/repo", "token", pr_number, head)
    assert state2 == "pending", f"expected pending classification for an in-progress cycle, got {state2!r}"

    orchestration.read_cycle = lambda *_a: ("present", None, "pull request is not open")
    raised = False
    try:
        governance.pending_review_authority_state("owner/repo", "token", pr_number, head)
    except RuntimeError:
        raised = True
    assert raised, "malformed cycle evidence must fail closed, never classify as pending"

    return {"absent_state": state, "in_progress_state": state2}


SCENARIOS = {"A": scenario_a, "B": scenario_b, "G": scenario_g}
#: Wall-clock bound on one scenario child. Generous for real work, but bounds
#: a hostile candidate module that hangs (an infinite loop, a blocking call)
#: instead of ever returning or raising.
SCENARIO_CHILD_TIMEOUT_SECONDS = 60


def _scenario_child(conn: Connection, scenario_id: str, candidate_root_str: str, fixture: dict[str, Any]) -> None:
    """Runs in a freshly spawned child process whose only channel back to the
    caller is this pipe -- never stdout, which candidate top-level import
    code can also write to, and never the process exit code, which
    ``os._exit(n)`` lets candidate code choose freely.

    Sends a ``"started"`` message *before* ``_import_candidate`` -- and
    therefore before any candidate top-level module code -- ever runs, and a
    ``"done"``/``"failed"`` message only from the code path that runs after
    the real scenario function actually returns or raises. A candidate
    module that forges a fake passing payload and calls ``os._exit``
    immediately at import time produces neither message on this pipe: the
    caller only accepts a well-formed, ordered ``started`` + ``done``/
    ``failed`` pair, never a bare stdout line or a process exit code (see
    ``_run_scenario_isolated``).

    ``measurements`` is forced through a JSON round-trip before it crosses
    the pipe: :func:`multiprocessing.connection.Connection.send` pickles its
    argument, and a scenario's raw return value can carry whatever object a
    candidate function chose to return, so only JSON-safe primitives -- never
    an arbitrary picklable object a candidate could shape into a
    deserialization gadget -- are allowed onto that channel.

    stdout/stderr are redirected to the null device before anything else
    runs, at the OS file-descriptor level (not just reassigning
    ``sys.stdout``), so no forged line -- from ``print()``, direct
    ``os.write``, or otherwise -- from candidate code can reach this
    process's shared stdout stream, which the trusted parent worker process
    also writes its own real result line to after this child exits.
    """

    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull_fd, 1)
    os.dup2(devnull_fd, 2)
    os.close(devnull_fd)

    try:
        conn.send({"stage": "started"})
        # Mock GitHub API calls in the child process with appropriate mock data
        # to avoid real network requests while allowing candidate logic to work.
        from unittest.mock import patch

        import hunter_github_transport as transport

        def _mock_request_rest_json(url, method, headers, data, token, what):
            if "/statuses/" in url and method == "POST":
                return {"id": 12345, "url": "https://api.github.com/repos/test/statuses/12345"}
            if "/statuses/" in url and method == "GET":
                return []
            if "/dispatches" in url and method == "POST":
                return {}
            if "/actions/runs/" in url:
                return {"id": 123, "head_sha": "a" * 40, "status": "completed", "conclusion": "success"}
            if "/commits/" in url and "/statuses" in url:
                return []
            if "/compare/" in url:
                return {"status": "identical"}
            if "/git/ref/" in url:
                return {"object": {"type": "tag", "sha": "a" * 40}}
            if "/git/tags/" in url:
                return {"tag": "test-tag", "message": "hunter-collector-run-id=123"}
            if "/git/tags" in url and method == "POST":
                return {"sha": "a" * 40}
            if "/git/refs" in url and method == "POST":
                return {}
            if "/actions/runs/" in url and "/attempts" in url:
                return {"workflow_runs": []}
            if "/actions/workflows/" in url and "/runs" in url:
                return {"workflow_runs": []}
            if "/pulls/" in url:
                return {"state": "open", "head": {"sha": "a" * 40}}
            if "/repos/" in url and "/actions/workflows/" in url:
                return {"id": 123, "path": ".github/workflows/test.yml"}
            return {}

        def mock_request_graphql_json(query, variables, token, what):
            review_threads = {"nodes": [], "pageInfo": {"hasNextPage": False}}
            pull_request = {"reviewThreads": review_threads}
            repository = {"pullRequest": pull_request}
            data = {"repository": repository}
            return {"data": data}

        def mock_rest_json(url, method, headers, data, token, what):
            return _mock_request_rest_json(url, method, headers, data, token, what)

        def mock_graphql_json(query, variables, token, what):
            review_threads = {"nodes": [], "pageInfo": {"hasNextPage": False}}
            pull_request = {"reviewThreads": review_threads}
            repository = {"pullRequest": pull_request}
            data = {"repository": repository}
            return {"data": data}

        with (
            patch.object(transport, "request_rest_json", _mock_request_rest_json),
            patch.object(transport, "request_graphql_json", mock_request_graphql_json),
            patch.object(transport, "rest_json", mock_rest_json),
            patch.object(transport, "graphql_json", mock_graphql_json),
        ):
            measurements = SCENARIOS[scenario_id](Path(candidate_root_str), fixture)
            safe_measurements = json.loads(json.dumps(measurements, ensure_ascii=False))
            conn.send({"stage": "done", "measurements": safe_measurements})
    except (
        BaseException
    ) as exc:  # noqa: BLE001 - every candidate-triggered failure must be reported, never crash silently
        try:
            conn.send(
                {
                    "stage": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
            )
        except Exception:
            pass
    finally:
        conn.close()


def _run_scenario_isolated(scenario_id: str, candidate_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    """Runs one scenario in a child process and trusts only the exact ordered
    pair of pipe messages ``_scenario_child`` can produce -- never the
    child's stdout, and never its exit code, either of which candidate
    top-level import code fully controls once it has run at all.
    """

    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_scenario_child,
        args=(child_conn, scenario_id, str(candidate_root), fixture),
    )
    process.start()
    child_conn.close()

    messages: list[Any] = []
    remaining = float(SCENARIO_CHILD_TIMEOUT_SECONDS)
    try:
        while len(messages) < 2 and parent_conn.poll(timeout=remaining):
            try:
                messages.append(parent_conn.recv())
            except EOFError:
                break
            # A child that sent 'started' must send its second message
            # promptly; it does not get the full budget twice.
            remaining = 5.0
    finally:
        parent_conn.close()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    def fail(reason: str) -> dict[str, Any]:
        return {"outcome": "fail", "measurements": {}, "error": reason}

    if len(messages) != 2:
        return fail(
            "expected exactly 2 trusted completion messages (started, done/failed) "
            f"on the isolated scenario channel, received {len(messages)}"
        )
    started, finished = messages
    if not isinstance(started, dict) or started.get("stage") != "started":
        return fail("first trusted completion message was not the expected 'started' sentinel")
    if not isinstance(finished, dict) or finished.get("stage") not in {"done", "failed"}:
        return fail("second trusted completion message was not a well-formed 'done'/'failed' report")
    if finished["stage"] == "failed":
        return {
            "outcome": "fail",
            "measurements": {},
            "error": str(finished.get("error", "")),
            "traceback": str(finished.get("traceback", "")),
        }
    measurements = finished.get("measurements")
    if not isinstance(measurements, dict):
        return fail("'done' message carried non-dict measurements")
    return {"outcome": "pass", "measurements": measurements}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trusted orchestrator replay worker (subprocess-isolated)")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    parser.add_argument("--fixture", required=True)
    return parser


def _reject_unsafe_relative(relative: str, *, label: str) -> Path:
    """Reject an absolute path or any '..' traversal component before any
    filesystem access happens at all. Both --candidate-root and --fixture
    are relative names, confined to --workspace-root below -- never
    accepted as arbitrary caller-supplied absolute paths.
    """

    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise ValueError(f"{label} {relative!r} must be a relative path, not absolute")
    if ".." in relative_path.parts:
        raise ValueError(f"{label} {relative!r} must not contain '..' traversal")
    if not relative_path.parts:
        raise ValueError(f"{label} must be a non-empty relative path")
    return relative_path


def _resolve_confined(workspace_root: Path, relative: str, *, must_be_dir: bool, label: str) -> Path:
    """Resolve `relative` under the trusted `workspace_root`, failing closed
    on any escape attempt -- including a symlink at `relative` that points
    outside the root, which resolving through it and re-checking containment
    (rather than checking the unresolved path alone) is what actually closes.
    """

    relative_path = _reject_unsafe_relative(relative, label=label)
    resolved = (workspace_root / relative_path).resolve(strict=True)
    try:
        resolved.relative_to(workspace_root)
    except ValueError:
        raise ValueError(f"{label} {relative!r} resolves outside the trusted workspace root {workspace_root}") from None
    if must_be_dir and not resolved.is_dir():
        raise ValueError(f"{label} {relative!r} does not resolve to a directory")
    if not must_be_dir and not resolved.is_file():
        raise ValueError(f"{label} {relative!r} does not resolve to a file")
    return resolved


def main() -> int:
    args = _parser().parse_args()
    workspace_root = Path(args.workspace_root).resolve(strict=True)
    if not workspace_root.is_dir():
        raise ValueError(f"--workspace-root {args.workspace_root!r} does not resolve to a directory")
    candidate_root = _resolve_confined(workspace_root, args.candidate_root, must_be_dir=True, label="--candidate-root")
    fixture_path = _resolve_confined(workspace_root, args.fixture, must_be_dir=False, label="--fixture")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    scenario_id = args.scenario

    result: dict[str, Any] = {"scenario_id": scenario_id}
    result.update(_run_scenario_isolated(scenario_id, candidate_root, fixture))
    # Computed here, in this process, which never imports candidate code
    # itself -- the scenario ran in a separate spawned child (see
    # _run_scenario_isolated) -- and never accepted from anything that child
    # reported, so a forged claim in its (untrusted) pipe messages cannot
    # substitute a fake digest for the real one.
    result["candidate_module_digest"] = candidate_module_digest(candidate_root, scenario_id)
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
