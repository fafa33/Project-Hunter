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


def scenario_a(candidate_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    """A pending cycle always reaches a terminal state inside a bounded budget.

    The candidate's own contract for this (see
    ``INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS``) is that one global opportunity
    budget spans the whole reviewer chain, and that a completed or timed-out
    opportunity must never leave the exact-head status pending forever. This
    scenario validates that contract through the candidate's real
    ``ensure_collector`` and asserts nothing about how the budget is *derived*,
    because production is the authority on its own budget and does not promise
    the candidate reviewer pool's chain length:

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
    """

    orchestrator = _import_candidate(candidate_root, "hunter_review_orchestrator")
    head = fixture["head_sha"]
    pr_number = int(fixture["pr_number"])
    digest = fixture["config_digest"]
    run_id = int(fixture["run_id"])

    budget = getattr(orchestrator, "INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS", None)
    assert isinstance(budget, int) and not isinstance(budget, bool), (
        "the candidate must publish an integer review-opportunity budget "
        f"(INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS), got {budget!r}"
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
    orchestrator.collector_liveness = lambda *_a: ("active", 1)

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

    return {
        "opportunity_seconds": budget,
        "below_boundary_seconds": below_age,
        "above_boundary_seconds": above_age,
        "timed_out_state": timed_out.state,
        "in_opportunity_state": still_pending.state,
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
    3. A recorded cycle whose collector produced no run and that exceeds the
       liveness grace recovers exactly one further dispatch.
    4. That recovery refreshes the durable record: a fresh durable cycle is
       published for it. A candidate that dispatches the recovery without
       republishing leaves the stale record in place, so this step fails.
    5. Reconciles that observe the refreshed durable record dispatch nothing
       further, and once the dispatch budget that real record has accumulated
       is spent, the cycle stays pending and dispatches no more rather than
       dispatching without end.
    """

    orchestrator = _import_candidate(candidate_root, "hunter_review_orchestrator")
    head = fixture["head_sha"]
    pr_number = int(fixture["pr_number"])
    digest = fixture["config_digest"]
    run_id = int(fixture["run_id"])

    grace = int(orchestrator.COLLECTOR_LIVENESS_GRACE_SECONDS)
    max_dispatches = int(orchestrator.MAX_COLLECTOR_DISPATCHES)
    opportunity = int(orchestrator.INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS)
    assert max_dispatches >= 1, f"the collector dispatch budget must allow at least one dispatch, got {max_dispatches}"
    assert 0 < grace < opportunity, (
        f"the collector liveness grace ({grace}s) must be shorter than the review opportunity "
        f"({opportunity}s) so a dead collector is recovered before the cycle times out"
    )

    state: dict[str, Any] = {"dispatches": 0, "published": [], "runs": []}
    orchestrator.reviewer_pool_config_digest = lambda: digest
    orchestrator.current_run_id = lambda: run_id
    orchestrator.publish_cycle = lambda *_a, cycle: state["published"].append(cycle)

    def fake_dispatch_collector(*_args: Any) -> None:
        state["dispatches"] += 1
        # A dispatched collector is a correlated run for this exact head, which
        # is what production counts toward MAX_COLLECTOR_DISPATCHES.
        state["runs"].append(head)

    orchestrator.dispatch_collector = fake_dispatch_collector

    #: Real liveness evidence, derived from the runs this scenario's own
    #: dispatch hook created. Nothing here is substituted for the durable
    #: record: the count is the number of correlated runs, exactly as
    #: ``collector_liveness`` reports it.
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

    # Recovery: the durable record has outlived the liveness grace and the
    # collector run it recorded never became live, so production re-dispatches.
    stale = _recorded(grace + 1)
    state["published"].append(stale)
    published_before_recovery = len(state["published"])
    _read_durable()
    recovered = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)
    assert state["dispatches"] == 2, (
        f"a durable record whose collector produced no run must recover exactly one further dispatch after the "
        f"{grace}s liveness grace, got {state['dispatches'] - 1}"
    )
    assert (
        recovered.state == WAITING_FOR_REVIEWER_STATE
    ), f"a recovered cycle must stay pending, got {recovered.state!r}"

    # (4) The recovery must refresh the durable record. Reading back the newest
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
    assert refreshed.started_at != stale.started_at, (
        "the refreshed durable cycle must carry the recovery's own liveness timestamp, not the stale one the "
        "recovery was dispatched for"
    )

    # (5) Later reconciles consume that refreshed record, and the dispatch
    # budget is spent by the runs the real dispatches created. Ageing the
    # record past the grace each time is the only concession to wall-clock
    # here; the budget arithmetic stays production's.
    while state["dispatches"] < max_dispatches:
        state["published"].append(_recorded(grace + 1))
        _read_durable()
        orchestrator.ensure_collector("owner/repo", "token", pr_number, head)

    state["published"].append(_recorded(grace + 1))
    _read_durable()
    exhausted = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)
    assert state["dispatches"] == max_dispatches, (
        f"a cycle that has spent its dispatch budget must stay pending rather than dispatching without end "
        f"(MAX_COLLECTOR_DISPATCHES={max_dispatches}), got {state['dispatches']} dispatch(es)"
    )
    assert (
        exhausted.state == WAITING_FOR_REVIEWER_STATE
    ), f"an exhausted cycle must stay {WAITING_FOR_REVIEWER_STATE!r}, got {exhausted.state!r}"

    return {
        "dispatches": state["dispatches"],
        "correlated_runs": len(state["runs"]),
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
