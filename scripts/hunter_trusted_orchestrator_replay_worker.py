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
    """The review opportunity is a bounded budget, and nothing but a trusted publisher can end a cycle.

    Two properties of the native model, both read through the candidate's real functions:

    * ``independent_review_opportunity_seconds`` is one strictly positive, finite budget derived by the
      candidate from its own reviewer pool (the collector job lifetime is validated against it); and
    * ``read_cycle`` believes a published cycle only when the run it cites is a trusted-publisher run on
      the default branch. A status citing the (status-less) collector run, or a run off the default
      branch, is never a cycle, so neither the collector nor elapsed time can complete one.
    """

    orchestration = _import_candidate(candidate_root, "hunter_review_orchestrator")
    budget = orchestration.independent_review_opportunity_seconds()
    assert (
        type(budget) is int and 0 < budget < 7 * 24 * 3600
    ), f"opportunity budget is not positive and finite: {budget!r}"

    head = fixture["head_sha"]
    pr_number = int(fixture["pr_number"])
    digest = fixture["config_digest"]
    runs = {
        1: {"head_branch": "main", "path": ".github/workflows/hunter-collector-publisher.yml"},
        2: {"head_branch": "main", "path": ".github/workflows/hunter-reviewer-collector.yml"},
        3: {"head_branch": "feature", "path": ".github/workflows/hunter-collector-publisher.yml"},
    }

    def believed(run_id: int) -> str:
        status = {
            "creator": {"login": "github-actions[bot]"},
            "context": f"{orchestration.CONTEXT_PREFIX}{pr_number}",
            "description": f"REVIEWER_UNAVAILABLE||{run_id}|{digest}|",
            "target_url": f"https://github.com/owner/repo/actions/runs/{run_id}",
            "created_at": "2026-01-01T00:00:00Z",
        }

        def request_json(_repository: str, _token: str, _method: str, path: str, payload: Any = None) -> Any:
            if path == f"pulls/{pr_number}":
                return {"state": "open", "head": {"sha": head}}
            if path.startswith(f"commits/{head}/statuses"):
                return [status]
            if path == "":
                return {"default_branch": "main"}
            if path == f"actions/runs/{run_id}":
                return runs[run_id]
            raise AssertionError(f"unexpected request {path!r}")

        orchestration.request_json = request_json
        found, cycle, _error = orchestration.read_cycle("owner/repo", "token", pr_number, head)
        assert (cycle is not None) == (found == "present")
        return found

    publisher, collector, off_branch = believed(1), believed(2), believed(3)
    assert publisher == "present", f"a trusted publisher cycle must be believed, got {publisher!r}"
    assert collector == "absent", "a status citing the collector run must never be a cycle"
    assert off_branch == "absent", "a status citing a run off the default branch must never be a cycle"
    return {"budget_positive": True, "publisher": publisher, "collector": collector, "off_branch": off_branch}


def scenario_b(candidate_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    """A collector receipt is usable only for the exact run, pull request, head and generation it names.

    The receipt is the one thing a collector run contributes, so the candidate's canonical
    ``collector_receipt_problem`` contract must accept a well-formed receipt and reject every
    mismatch a forged, replayed or partial receipt could carry: another pull request, head, run or
    attempt, a missing or malformed identity digest, a malformed attempt list, and the minimal
    ``{"run_attempt": 1}`` object. Replay, rerun and duplicate events therefore cannot turn one
    run's receipt into another's result.
    """

    orchestration = _import_candidate(candidate_root, "hunter_review_orchestrator")
    head = fixture["head_sha"]
    pr_number = int(fixture["pr_number"])
    run_id = int(fixture["run_id"])
    receipt = {
        "schema": orchestration.COLLECTOR_RESULTS_SCHEMA,
        "repository": "owner/repo",
        "pr_number": pr_number,
        "head_sha": head,
        "run_id": run_id,
        "run_attempt": 1,
        "claims_id": "c" * 64,
        "remediation_generation_id": "",
        "configuration_digest": fixture["config_digest"],
        "attempts": [],
    }

    def problem(candidate: Any, **overrides: Any) -> str:
        arguments: dict[str, Any] = {
            "repository": "owner/repo",
            "pr_number": pr_number,
            "head_sha": head,
            "run_id": run_id,
            "run_attempt": 1,
        }
        arguments.update(overrides)
        return orchestration.collector_receipt_problem(candidate, **arguments)

    assert problem(receipt) == "", "a well-formed receipt for its own run must be usable"
    rejected = {
        "other pull request": problem(receipt, pr_number=pr_number + 1),
        "other head": problem(receipt, head_sha="a" * 40),
        "other run": problem(receipt, run_id=run_id + 1),
        "other attempt": problem(receipt, run_attempt=2),
        "other generation": problem(receipt, generation_id="0123456789abcdef"),
        "minimal object": problem({"run_attempt": 1}),
        "not an object": problem([]),
        "bad claims": problem({**receipt, "claims_id": "short"}),
        "bad attempts": problem({**receipt, "attempts": "x"}),
    }
    accepted = [name for name, reason in rejected.items() if not reason]
    assert not accepted, f"receipts must be refused for: {accepted}"
    return {"rejected": sorted(rejected)}


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
