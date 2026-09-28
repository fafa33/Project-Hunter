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
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import multiprocessing as mp
import os
import sys
import traceback
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

#: The exact candidate files each scenario reads or imports. Recorded so the
#: digest a scenario reports is provably scoped to the modules it actually
#: touched, and so the trusted validator can recompute the identical digest
#: from its own, independent candidate checkout.
CANDIDATE_MODULE_FILES: dict[str, tuple[str, ...]] = {
    "A": (
        "scripts/hunter_pre_ready_review.py",
        "scripts/hunter_review_orchestrator.py",
        "docs/CODE_WRITE_POLICY.json",
    ),
    "B": ("scripts/hunter_review_orchestrator.py",),
    "G": (
        "scripts/hunter_governance_review_v2.py",
        "scripts/hunter_review_orchestrator.py",
    ),
}


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
    """Reviewer-opportunity timing is derived from the candidate's own pool.

    Assertions, none of which hardcodes an expected candidate number or an
    expected pool shape:

    1. The published opportunity equals the candidate's own worst-case-budget
       derivation over its own real reviewer pool (single source of truth).
    2. Raising one enabled reviewer's timeout strictly increases the
       opportunity (it is derived, not a fixed constant) by *exactly* that
       reviewer's own attempt count times the injected delta -- never more,
       proving the budget has no unbounded or open-ended retry component.
       A reviewer need not be ``retryable`` for this: this repository's own
       canonical timeout policy currently sets ``retries_per_agent: 0`` pool
       -wide ("No automatic retries. Explicit authenticated unavailability
       fails over immediately" -- CODE_WRITE_POLICY.json), so every enabled
       reviewer's attempt count is 1 regardless of its ``retryable`` flag;
       requiring a retryable reviewer to exist would make this scenario
       untestable against that architecture's own real, deliberate policy.
    3. When the pool grants zero retries, flipping a reviewer's ``retryable``
       flag alone (with ``retries_per_agent`` unchanged) must never change the
       derived opportunity -- so a hostile receipt cannot satisfy this
       scenario merely by setting ``retryable: true`` in a reported pool.
    4. The opportunity covers at least the raw worst-case reviewer-chain sum
       (a safety bound: it must never be shorter than the chain it exists to
       cover).
    5. The opportunity reserves strictly positive slack above that raw sum
       (orchestration overhead) and stays within a generous absolute ceiling
       (it is bounded, not runaway).
    """

    pre_ready = _import_candidate(candidate_root, "hunter_pre_ready_review")
    orchestrator = _import_candidate(candidate_root, "hunter_review_orchestrator")

    real_pool, error = pre_ready.load_reviewer_pool()
    if real_pool is None:
        raise AssertionError(f"candidate reviewer pool unavailable: {error}")

    baseline_seconds = orchestrator.independent_review_opportunity_seconds()
    worst_case = pre_ready.reviewer_chain_worst_case_seconds(real_pool)
    assert baseline_seconds == worst_case, (
        f"opportunity {baseline_seconds} must equal the candidate's own trusted "
        f"worst-case-budget derivation {worst_case}"
    )

    enabled = pre_ready.enabled_pool_reviewers(real_pool)
    if not enabled:
        raise AssertionError("fixture pool must enable at least one reviewer")
    retries_per_agent = int(real_pool["timeout_policy"]["retries_per_agent"])
    # Prefer a retryable reviewer when the pool has one, so a pool that does
    # grant retries still exercises the attempt-count multiplier below; a
    # pool with none (this repository's own current, deliberate policy) is
    # equally valid and falls back to any enabled reviewer.
    target = next((a for a in enabled if a.get("retryable")), None) or enabled[0]
    target_attempts = 1 + (retries_per_agent if target.get("retryable") else 0)
    delta = int(fixture["timeout_delta_seconds"])
    mutated_pool = copy.deepcopy(real_pool)
    for agent in mutated_pool["agents"]:
        if agent.get("id") == target.get("id"):
            agent["review_timeout_seconds"] = int(agent["review_timeout_seconds"]) + delta

    original_loader = pre_ready.load_reviewer_pool
    pre_ready.load_reviewer_pool = lambda *_a, **_k: (mutated_pool, "")
    try:
        mutated_seconds = orchestrator.independent_review_opportunity_seconds()
    finally:
        pre_ready.load_reviewer_pool = original_loader

    assert mutated_seconds > baseline_seconds, (
        "opportunity did not increase after a reviewer timeout increase; " "it looks hardcoded rather than pool-derived"
    )
    assert mutated_seconds - baseline_seconds == target_attempts * delta, (
        f"opportunity increased by {mutated_seconds - baseline_seconds}s for a {delta}s timeout increase on "
        f"{'a retryable' if target.get('retryable') else 'a non-retryable'} reviewer with "
        f"retries_per_agent={retries_per_agent}; expected exactly {target_attempts * delta}s "
        f"({target_attempts} attempt(s)) -- an unbounded or open-ended retry component would show up here"
    )

    if retries_per_agent == 0 and not target.get("retryable"):
        flipped_pool = copy.deepcopy(real_pool)
        for agent in flipped_pool["agents"]:
            if agent.get("id") == target.get("id"):
                agent["retryable"] = True
        flipped_worst_case = pre_ready.reviewer_chain_worst_case_seconds(flipped_pool)
        assert flipped_worst_case == worst_case, (
            "flipping a reviewer's retryable flag changed the derived opportunity even though "
            "retries_per_agent is 0 -- retryable must be inert when the pool grants no retries, "
            "so a reported pool cannot game this scenario merely by claiming retryable=true"
        )

    lower_bound = sum(
        (1 + (real_pool["timeout_policy"]["retries_per_agent"] if agent["retryable"] else 0))
        * int(agent["review_timeout_seconds"])
        for agent in enabled
    )
    assert baseline_seconds >= lower_bound, (
        f"opportunity {baseline_seconds} is shorter than the worst-case reviewer " f"chain {lower_bound} it must cover"
    )
    ceiling = int(fixture["absolute_ceiling_seconds"])
    assert lower_bound < baseline_seconds <= ceiling, (
        f"opportunity {baseline_seconds} must reserve positive overhead above "
        f"{lower_bound} and stay within the {ceiling}s sanity ceiling"
    )

    return {
        "baseline_seconds": baseline_seconds,
        "worst_case_seconds": worst_case,
        "mutated_seconds": mutated_seconds,
        "injected_delta_seconds": delta,
    }


def scenario_b(candidate_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    """Exact-head collector dispatch is idempotent under a stale status read.

    Models GitHub's read-your-write inconsistency: the combined-status read
    never observes the write a prior reconcile call just made, within the
    window this scenario exercises. ``ensure_collector`` is invoked twice, as
    two racing/repeated reconcile calls would, and exactly one dispatch must
    result; a third call, once the read genuinely catches up, must still
    return cleanly (recovery), never erroring and never dispatching again.
    """

    orchestrator = _import_candidate(candidate_root, "hunter_review_orchestrator")
    head = fixture["head_sha"]
    pr_number = int(fixture["pr_number"])
    state: dict[str, Any] = {"dispatches": 0, "published": [], "liveness_calls": 0}

    def fake_read_cycle(*_args: Any) -> tuple[str, Any, str | None]:
        return ("absent", None, None)

    def fake_reviewer_pool_config_digest() -> str:
        return fixture["config_digest"]

    def fake_current_run_id() -> int:
        return int(fixture["run_id"])

    def fake_publish_cycle(*_args: Any, cycle: Any) -> None:
        state["published"].append(cycle)

    def fake_dispatch_collector(*_args: Any) -> None:
        state["dispatches"] += 1

    def fake_collector_liveness(*_args: Any) -> tuple[str, int]:
        state["liveness_calls"] += 1
        if state["dispatches"] == 0:
            return ("missing", 0)
        return ("active", 1)

    orchestrator.read_cycle = fake_read_cycle
    orchestrator.reviewer_pool_config_digest = fake_reviewer_pool_config_digest
    orchestrator.current_run_id = fake_current_run_id
    orchestrator.publish_cycle = fake_publish_cycle
    orchestrator.dispatch_collector = fake_dispatch_collector
    orchestrator.collector_liveness = fake_collector_liveness

    first = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)
    second = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)

    assert (
        state["dispatches"] == 1
    ), f"expected exactly one dispatch under a racing/repeated reconcile, got {state['dispatches']}"
    assert first.state == "WAITING_FOR_REVIEWER" and second.state == "WAITING_FOR_REVIEWER"

    def fake_read_cycle_recovered(*_args: Any) -> tuple[str, Any, str | None]:
        return ("present", state["published"][-1], None)

    orchestrator.read_cycle = fake_read_cycle_recovered
    third = orchestrator.ensure_collector("owner/repo", "token", pr_number, head)
    assert state["dispatches"] == 1, "recovery after the read catches up must not trigger another dispatch"
    assert third.state == "WAITING_FOR_REVIEWER"

    return {"dispatches": state["dispatches"], "liveness_calls": state["liveness_calls"]}


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
