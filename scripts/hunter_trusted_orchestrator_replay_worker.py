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
import ast
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

#: The candidate *root* modules each scenario imports. The files whose content
#: a scenario's result depends on are the transitive local-import closure of
#: these roots plus ``CANDIDATE_CONFIG_FILES`` (see ``candidate_input_closure``),
#: never only the roots: a candidate-controlled helper such as
#: ``hunter_pre_ready_review.py`` or ``docs/CODE_WRITE_POLICY.json`` changes what
#: ``independent_review_opportunity_seconds()`` returns without touching the
#: orchestrator itself. Keys are (contract_version, scenario_id) tuples.
CANDIDATE_MODULE_FILES: dict[tuple[str, str], tuple[str, ...]] = {
    ("v1", "A"): ("scripts/hunter_review_orchestrator.py",),
    ("v1", "B"): ("scripts/hunter_review_orchestrator.py",),
    ("v1", "G"): (
        "scripts/hunter_governance_review_v2.py",
        "scripts/hunter_review_orchestrator.py",
    ),
    ("v2", "A"): ("scripts/hunter_review_orchestrator.py",),
    ("v2", "B"): ("scripts/hunter_review_orchestrator.py",),
    ("v2", "G"): (
        "scripts/hunter_governance_review_v2.py",
        "scripts/hunter_review_orchestrator.py",
    ),
}

#: Candidate configuration the imported modules read at call time. They are
#: behavioral inputs (the reviewer pool and its timeouts live here), so they are
#: part of every scenario's digest and must exist inside the candidate root.
CANDIDATE_CONFIG_FILES: tuple[str, ...] = ("docs/CODE_WRITE_POLICY.json",)

#: The trusted checkout's scripts directory, which this worker's own import path also reaches.
TRUSTED_SCRIPTS_DIR = Path(__file__).resolve().parent


#: The state production records for a cycle still awaiting its collector. Read
#: from the candidate module rather than hardcoded, so a canonically valid
#: rename cannot be mistaken for a regression.
WAITING_FOR_REVIEWER_STATE = "WAITING_FOR_REVIEWER"


def resolve_candidate_file(candidate_root: Path, relative: str) -> Path:
    """Resolve one candidate-relative file, failing closed on any escape.

    The candidate checkout is untrusted: it can commit a symlink in place of a
    module (or of one of its parent directories) pointing at the trusted
    checkout, so that reading, classifying, importing or hashing it silently
    follows the link into trusted code. Every component below ``candidate_root``
    is therefore required to be a real (non-symlink) entry, and the fully
    resolved path must still lie beneath the resolved ``candidate_root``. This
    runs before any classification, comparison, import or hashing.
    """

    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts or not relative_path.parts:
        raise ValueError(f"candidate path {relative!r} must be a non-empty relative path without '..'")
    root = candidate_root.resolve(strict=True)
    current = root
    for part in relative_path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"candidate path {relative!r} traverses a symlink at {part!r}; symlinks are rejected")
    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(f"candidate path {relative!r} resolves outside the candidate root") from None
    if not resolved.is_file():
        raise ValueError(f"candidate path {relative!r} is not a regular file")
    return resolved


def read_candidate_text(candidate_root: Path, relative: str) -> str:
    return resolve_candidate_file(candidate_root, relative).read_text(encoding="utf-8")


def _candidate_module_path(candidate_root: Path, module: str) -> str | None:
    parts = module.split(".")
    if not parts or not all(part.isidentifier() for part in parts):
        raise ValueError(f"invalid candidate import: {module!r}")
    for prefix in ("scripts", "src"):
        base = Path(prefix).joinpath(*parts)
        for relative in (str(base) + ".py", str(base / "__init__.py")):
            if os.path.lexists(candidate_root / relative):
                return relative
    return None


def _local_imports(source: str, relative: str) -> set[str]:
    imports: set[str] = set()
    path = Path(relative)
    package = list(path.parent.parts[1:])
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level > len(package) + 1:
                    raise ValueError(f"relative import escapes candidate package in {relative}")
                parent = package[: len(package) - node.level + 1]
                target = ".".join(parent + (node.module.split(".") if node.module else []))
            else:
                target = node.module or ""
            if target:
                imports.add(target)
                imports.update(target + "." + alias.name for alias in node.names if alias.name != "*")
    return imports


def candidate_input_closure(candidate_root: Path, scenario_id: str, contract_version: str) -> tuple[str, ...]:
    pending = list(CANDIDATE_MODULE_FILES[(contract_version, scenario_id)])
    closure: set[str] = set()
    while pending:
        relative = pending.pop()
        if relative in closure:
            continue
        source = read_candidate_text(candidate_root, relative)
        closure.add(relative)
        for name in _local_imports(source, relative):
            parts = name.split(".")
            for length in range(1, len(parts) + 1):
                prefix = ".".join(parts[:length])
                dependency = _candidate_module_path(candidate_root, prefix)
                if dependency and dependency not in closure:
                    pending.append(dependency)
                elif not dependency:
                    for trusted_base in (TRUSTED_SCRIPTS_DIR, TRUSTED_SCRIPTS_DIR.parent / "src"):
                        if trusted_base.joinpath(*parts[:length]).with_suffix(".py").exists():
                            raise ValueError(f"candidate missing local dependency {prefix!r} from {relative!r}")
    for relative in CANDIDATE_CONFIG_FILES:
        resolve_candidate_file(candidate_root, relative)
        closure.add(relative)
    return tuple(sorted(closure))


def candidate_module_digest(candidate_root: Path, scenario_id: str, contract_version: str) -> str:
    """Bind all candidate-controlled files, including dynamic imports and data reads.

    A static import walk is not a complete execution dependency closure: Python
    permits importlib calls and arbitrary file reads. In addition to requiring
    all known controller modules and configs, hash the entire candidate tree,
    not just AST-detected imports. Ephemeral Git/bytecode caches are ignored
    only if they are absent from the candidate's tracked Git tree; otherwise
    replay fails closed instead of allowing unbound candidate-readable input.
    In hosted replay, candidate execution is also under a non-owner account
    so the candidate cannot rewrite the tree between hashing and execution.
    """
    declared = candidate_input_closure(candidate_root, scenario_id, contract_version)
    excluded = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    root = candidate_root.resolve(strict=True)
    import subprocess

    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached"],
        check=True,
        capture_output=True,
        timeout=15,
    ).stdout.split(b"\0")
    for encoded in tracked:
        if not encoded:
            continue
        relative = encoded.decode("utf-8", errors="strict")
        if (
            any(component in excluded for component in Path(relative).parts)
            or relative.endswith((".pyc", ".pyo"))
            or Path(relative).name == ".DS_Store"
        ):
            raise ValueError(f"tracked candidate input is excluded from digest: {relative}")
    payload: dict[str, str] = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(name for name in dirs if name not in excluded)
        for name in dirs:
            if (current_path / name).is_symlink():
                raise ValueError(f"candidate directory is a symlink: {current_path / name}")
        for name in sorted(files):
            if name in {".DS_Store"} or name.endswith((".pyc", ".pyo")):
                continue
            absolute = current_path / name
            relative = absolute.relative_to(root).as_posix()
            safe = resolve_candidate_file(root, relative)
            payload[relative] = hashlib.sha256(safe.read_bytes()).hexdigest()
    if not set(declared) <= payload.keys():
        raise ValueError("declared replay dependencies missing from complete candidate tree")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _import_candidate(candidate_root: Path, module_name: str) -> Any:
    scripts_dir = str((candidate_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    module = importlib.import_module(module_name)
    expected = resolve_candidate_file(candidate_root, f"scripts/{module_name}.py")
    if Path(str(getattr(module, "__file__", ""))).resolve() != expected:
        raise ImportError(f"{module_name} did not load from the candidate checkout at {expected}")
    return module


def _probe_v1_grace_boundary(orchestration: Any, fixture: dict[str, Any], grace: int) -> None:
    """Exercise actual elapsed-time semantics, including the exact boundary.

    Never mock _older_than: that would mask a candidate ignoring the configured
    grace or implementing the comparison incorrectly.
    """
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    base = orchestration.ReviewCycle(
        pr_number=fixture["pr_number"],
        head_sha=fixture["head_sha"],
        state="WAITING_FOR_REVIEWER",
        provider_id="",
        trigger_id=123,
        started_at="2020-01-01T00:00:00Z",
        config_digest="fixture-digest",
    )
    now = datetime.now(UTC)
    # Give the boundary a 2-second margin to avoid scheduler timing flakiness.
    inside = replace(base, started_at=(now - timedelta(seconds=max(0, grace - 2))).isoformat())
    outside = replace(base, started_at=(now - timedelta(seconds=grace + 2)).isoformat())
    with patch.object(orchestration, "collector_liveness", return_value=("absent", 0)):
        assert not orchestration.collector_needs_dispatch("fixture/repo", "", inside), "dispatch within grace"
        assert orchestration.collector_needs_dispatch("fixture/repo", "", outside), "missing dispatch beyond grace"
    with patch.object(orchestration, "collector_liveness", return_value=("active", 1)):
        assert not orchestration.collector_needs_dispatch("fixture/repo", "", outside), "duplicate active dispatch"
    assert not orchestration._older_than(now.isoformat(), grace), "fresh timestamp expired"
    assert orchestration._older_than(outside.started_at, grace), "elapsed boundary ignored"

    # A dead collector may recover only within the finite dispatch budget.
    budget = orchestration.MAX_COLLECTOR_DISPATCHES
    assert type(budget) is int and budget > 0, "invalid collector dispatch budget"
    with patch.object(orchestration, "collector_liveness", return_value=("absent", budget)):
        assert not orchestration.collector_needs_dispatch("fixture/repo", "", outside), "unbounded redispatch"
    with patch.object(orchestration, "collector_liveness", return_value=("completed", 1)):
        assert not orchestration.collector_needs_dispatch(
            "fixture/repo", "", outside
        ), "completed collector redispatched"

    # Active collector protection expires at a bounded ceiling, even when
    # the collector continues to report active or its evidence is unreadable.
    opportunity = orchestration.independent_review_opportunity_seconds()
    # Trusted harness ceiling: never derive the boundary from candidate code.
    multiplier = 2
    assert type(opportunity) is int and opportunity > 0
    assert orchestration.ACTIVE_COLLECTOR_GRACE_MULTIPLIER == multiplier
    active_inside = replace(base, started_at=(now - timedelta(seconds=opportunity * multiplier - 2)).isoformat())
    active_outside = replace(base, started_at=(now - timedelta(seconds=opportunity * multiplier + 2)).isoformat())
    with patch.object(orchestration, "collector_liveness", return_value=("active", 1)):
        assert orchestration._active_collector_still_running(
            "fixture/repo", "", active_inside, opportunity
        ), "live collector finalized early"
        assert not orchestration._active_collector_still_running(
            "fixture/repo", "", active_outside, opportunity
        ), "active collector bound ignored"


def _probe_v1_ensure_collector_recovery(orchestration: Any, fixture: dict[str, Any], grace: int) -> dict[str, Any]:
    """Drive the real ``ensure_collector`` through a stale-cycle sequence.

    Only the GitHub boundary is replaced, with one small stateful fake (the
    recorded cycle, the collector run list, and a dispatch counter); every
    decision under test -- idempotence inside the grace, bounded re-dispatch,
    the refreshed cycle, the terminal timeout and the active-collector ceiling
    -- is made by the candidate's own ``ensure_collector``,
    ``collector_needs_dispatch``, ``collector_liveness`` outcome handling and
    ``_older_than``. Timestamps are derived from the wall clock at fixed offsets
    far larger than scheduling jitter, so the sequence is deterministic.
    """
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    head = fixture["head_sha"]
    pr_number = fixture["pr_number"]
    budget = orchestration.MAX_COLLECTOR_DISPATCHES
    opportunity = orchestration.independent_review_opportunity_seconds()
    multiplier = orchestration.ACTIVE_COLLECTOR_GRACE_MULTIPLIER
    assert type(budget) is int and budget > 0, "invalid collector dispatch budget"
    assert type(opportunity) is int and opportunity > 0 and type(multiplier) is int and multiplier > 1

    fake: dict[str, Any] = {"cycle": None, "runs": 0, "dispatches": 0, "published": [], "liveness": None}

    def iso(age_seconds: int) -> str:
        return (datetime.now(UTC) - timedelta(seconds=age_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def fake_read_cycle(*_args: Any) -> tuple[str, Any, Any]:
        return ("present", fake["cycle"], None) if fake["cycle"] is not None else ("absent", None, None)

    def fake_publish_cycle(_repository: str, _token: str, _head: str, *, cycle: Any) -> None:
        fake["cycle"] = cycle
        fake["published"].append(cycle.state)

    def fake_dispatch(*_args: Any) -> None:
        fake["dispatches"] += 1
        fake["runs"] += 1

    def fake_liveness(*_args: Any) -> tuple[str, int]:
        if fake["liveness"] is not None:
            return fake["liveness"], fake["runs"]
        return ("dead" if fake["runs"] else "missing"), fake["runs"]

    def ensure() -> Any:
        return orchestration.ensure_collector("fixture/repo", "", pr_number, head)

    def age_cycle(age_seconds: int) -> None:
        fake["cycle"] = replace(fake["cycle"], started_at=iso(age_seconds))

    def reset() -> None:
        fake.update(cycle=None, runs=0, dispatches=0, published=[], liveness=None)

    with (
        patch.object(orchestration, "reviewer_pool_config_digest", return_value="fixture-digest"),
        patch.object(orchestration, "read_cycle", side_effect=fake_read_cycle),
        patch.object(orchestration, "publish_cycle", side_effect=fake_publish_cycle),
        patch.object(orchestration, "dispatch_collector", side_effect=fake_dispatch),
        patch.object(orchestration, "collector_liveness", side_effect=fake_liveness),
        patch.object(orchestration, "collector_runs", return_value=[]),
        patch.object(orchestration, "current_run_id", return_value=123),
    ):
        # 1. A fresh head records a pending cycle and dispatches exactly once.
        first = ensure()
        assert first.state == WAITING_FOR_REVIEWER_STATE and first.head_sha == head
        assert fake["dispatches"] == 1 and fake["published"] == [WAITING_FOR_REVIEWER_STATE], "fresh dispatch"

        # 2. Re-entry inside the grace is idempotent: no duplicate dispatch or publication.
        assert ensure() == first and fake["dispatches"] == 1 and len(fake["published"]) == 1, "grace not idempotent"

        # 3. A stale cycle whose collector died is refreshed and re-dispatched, but only
        #    within the finite dispatch budget and never twice for the same stale record.
        #    Recovery is reachable only while the grace still fits inside the opportunity
        #    budget; a grace at or beyond it means the cycle times out before it can ever
        #    be judged dead, so no recovery is expected (and none may happen) in that case.
        recoverable = grace + 60 < opportunity
        for _round in range(budget + 3 if recoverable else 0):
            age_cycle(grace + 30)
            before = (fake["dispatches"], len(fake["published"]))
            recovered = ensure()
            if fake["dispatches"] > before[0]:
                assert fake["dispatches"] == before[0] + 1, "recovery dispatched more than once"
                assert len(fake["published"]) == before[1] + 1, "recovery did not refresh the cycle"
                assert recovered.state == WAITING_FOR_REVIEWER_STATE
                assert not orchestration._older_than(recovered.started_at, grace), "stale cycle was not refreshed"
                refreshed = (fake["dispatches"], len(fake["published"]))
                assert (
                    ensure() == recovered and (fake["dispatches"], len(fake["published"])) == refreshed
                ), "refreshed cycle re-dispatched inside the grace"
        if recoverable:
            assert fake["dispatches"] == budget, f"dispatch budget not honoured: {fake['dispatches']} != {budget}"
            exhausted = (fake["dispatches"], len(fake["published"]))
            age_cycle(grace + 30)
            stale = fake["cycle"]
            assert (
                ensure() == stale and (fake["dispatches"], len(fake["published"])) == exhausted
            ), "unbounded redispatch"
        else:
            assert fake["dispatches"] == 1, "recovery dispatched although the grace outlives the opportunity"

        # 4. Past the opportunity budget with no live collector the cycle ends exactly once.
        reset()
        fake["cycle"] = orchestration.ReviewCycle(
            pr_number=pr_number,
            head_sha=head,
            state=WAITING_FOR_REVIEWER_STATE,
            provider_id="",
            trigger_id=123,
            started_at=iso(opportunity + 60),
            config_digest="fixture-digest",
        )
        fake["runs"] = budget
        timed_out = ensure()
        assert timed_out.state == "REVIEW_TIMED_OUT", f"stale cycle never reached the terminal timeout: {timed_out}"
        assert fake["published"] == ["REVIEW_TIMED_OUT"] and fake["dispatches"] == 0
        assert ensure() == timed_out and fake["published"] == ["REVIEW_TIMED_OUT"], "terminal state not idempotent"
        assert fake["dispatches"] == 0, "terminal head was re-dispatched"

        # 5. A live collector defers the timeout, but only up to the finite ceiling.
        reset()
        fake["liveness"] = "active"
        fake["runs"] = 1
        fake["cycle"] = orchestration.ReviewCycle(
            pr_number=pr_number,
            head_sha=head,
            state=WAITING_FOR_REVIEWER_STATE,
            provider_id="",
            trigger_id=123,
            started_at=iso(opportunity + 60),
            config_digest="fixture-digest",
        )
        deferred = ensure()
        assert deferred.state == WAITING_FOR_REVIEWER_STATE and not fake["published"], "live collector finalized early"
        assert fake["dispatches"] == 0, "duplicate dispatch beside a live collector"
        age_cycle(opportunity * multiplier + 60)
        ceiling = ensure()
        assert ceiling.state == "REVIEW_TIMED_OUT", "active collector protection is not bounded"

    return {"recovery_dispatches": budget if recoverable else 0, "terminal_timeout": True, "active_ceiling": True}


def scenario_a_v1(candidate_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    """Legacy v1 contract: ensure_collector manages collector dispatch with liveness grace.

    Properties of the legacy model, all read through the candidate's real functions:

    * ``COLLECTOR_LIVENESS_GRACE_SECONDS`` is a positive, finite grace period before liveness
      is judged, so an ordinary listing lag cannot be mistaken for a dead collector.
    * ``ensure_collector``, driven end to end through a stale-cycle sequence against a fake
      GitHub boundary, dispatches idempotently, recovers a dead collector within the finite
      dispatch budget, refreshes the cycle, and reaches a terminal timeout.
    """

    orchestration = _import_candidate(candidate_root, "hunter_review_orchestrator")
    grace = orchestration.COLLECTOR_LIVENESS_GRACE_SECONDS
    assert type(grace) is int and grace > 0, f"COLLECTOR_LIVENESS_GRACE_SECONDS is not positive and finite: {grace!r}"

    recovery = _probe_v1_ensure_collector_recovery(orchestration, fixture, grace)
    # Scenario B performs real elapsed-time boundary probes, shared by v1 A.
    _probe_v1_grace_boundary(orchestration, fixture, grace)

    return {
        "grace_positive": True,
        "ensure_collector_present": True,
        "grace_used_in_dispatch": True,
        "recovery_dispatches": recovery["recovery_dispatches"],
        "terminal_timeout": recovery["terminal_timeout"],
        "active_ceiling": recovery["active_ceiling"],
    }


def scenario_b_v1(candidate_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    """Legacy v1 contract: collector liveness grace bounds re-dispatch.

    The legacy model uses COLLECTOR_LIVENESS_GRACE_SECONDS to prevent duplicate
    collector dispatches. A collector run that has been accepted but not yet
    listed must not trigger a re-dispatch. This scenario verifies the grace
    period constant exists and is referenced in the dispatch logic.
    """

    orchestration = _import_candidate(candidate_root, "hunter_review_orchestrator")
    grace = orchestration.COLLECTOR_LIVENESS_GRACE_SECONDS
    assert type(grace) is int and grace > 0, f"COLLECTOR_LIVENESS_GRACE_SECONDS is not positive and finite: {grace!r}"

    _probe_v1_grace_boundary(orchestration, fixture, grace)

    return {"grace_positive": True, "grace_used_in_needs_dispatch": True, "older_than_present": True}


def _probe_v2_budget_is_pool_derived(orchestration: Any, candidate_root: Path) -> int:
    """Prove the exported budget is *derived from the candidate's reviewer pool*.

    A range check cannot tell a pool-derived budget from ``return 3600``. Instead
    the candidate's own pool loader is swapped for a mutated copy of the real
    pool and the exported budget must follow it exactly, equal to the
    candidate's canonical ``reviewer_chain_worst_case_seconds`` of that pool; a
    hard-coded or disconnected budget does not move. An unreadable or malformed
    pool must make the budget raise (fail closed) rather than return a number.
    """
    from copy import deepcopy
    from unittest.mock import patch

    pre_ready = _import_candidate(candidate_root, "hunter_pre_ready_review")
    pool, error = pre_ready.load_reviewer_pool()
    assert pool is not None and not error, f"candidate reviewer pool is unreadable: {error!r}"
    canonical = pre_ready.reviewer_chain_worst_case_seconds(pool)

    budget = orchestration.independent_review_opportunity_seconds()
    assert (
        type(budget) is int and 0 < budget < 7 * 24 * 3600
    ), f"opportunity budget is not positive and finite: {budget!r}"
    assert budget == canonical, f"opportunity budget {budget} is not derived from the reviewer pool ({canonical})"

    mutated = deepcopy(pool)
    enabled = pre_ready.enabled_pool_reviewers(mutated)
    assert enabled, "candidate reviewer pool has no enabled reviewer"
    enabled[0]["review_timeout_seconds"] = int(enabled[0]["review_timeout_seconds"]) + 600
    expected_mutated = pre_ready.reviewer_chain_worst_case_seconds(mutated)
    assert expected_mutated != canonical, "reviewer pool timeout does not influence the canonical chain budget"
    with patch.object(pre_ready, "load_reviewer_pool", return_value=(mutated, "")):
        followed = orchestration.independent_review_opportunity_seconds()
    assert followed == expected_mutated, "opportunity budget ignores the reviewer pool configuration (hard-coded?)"

    for malformed in (({}, ""), (None, "pool missing"), ({"agents": "x", "timeout_policy": []}, "")):
        with patch.object(pre_ready, "load_reviewer_pool", return_value=malformed):
            try:
                value = orchestration.independent_review_opportunity_seconds()
            except Exception:  # noqa: BLE001 - any raise is the fail-closed outcome being asserted
                continue
        raise AssertionError(
            f"malformed reviewer pool {malformed!r} produced budget {value!r} instead of failing closed"
        )
    return budget


def scenario_a_v2(candidate_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    """The review opportunity is a bounded budget, and nothing but a trusted publisher can end a cycle.

    Properties of the native model, all read through the candidate's real functions:

    * ``independent_review_opportunity_seconds`` is one strictly positive, finite budget that is
      *derived from the candidate's own reviewer pool* (verified by mutating the pool, not by range); and
    * ``read_cycle`` believes a published cycle only when it was created by the trusted status
      publisher AND the run it cites executes the trusted base workflow, bound to the exact PR
      for target events. A status from another creator or with no creator, a status citing the
      (status-less) collector run, or a run without trusted base provenance is never a cycle,
      so neither the collector, a forger, nor elapsed time
      can complete one. Malformed descriptions are never a cycle either.
    """

    orchestration = _import_candidate(candidate_root, "hunter_review_orchestrator")
    _probe_v2_budget_is_pool_derived(orchestration, candidate_root)

    head = fixture["head_sha"]
    pr_number = int(fixture["pr_number"])
    digest = fixture["config_digest"]
    trusted_sha = "e" * 40
    runs: dict[int, dict[str, Any]] = {
        1: {"head_branch": "main", "head_sha": trusted_sha, "path": ".github/workflows/hunter-governance-review.yml"},
        2: {"head_branch": "main", "head_sha": trusted_sha, "path": ".github/workflows/hunter-reviewer-collector.yml"},
        3: {
            "head_branch": "feature",
            "head_sha": trusted_sha,
            "path": ".github/workflows/hunter-governance-review.yml",
        },
        4: {"head_branch": "main", "head_sha": "f" * 40, "path": ".github/workflows/hunter-governance-review.yml"},
    }
    target_pr = {
        "number": pr_number,
        "head": {"sha": head, "ref": "feature", "repo": {"id": 1}},
        "base": {"sha": trusted_sha, "ref": "main", "repo": {"id": 2}},
    }
    runs[5] = {
        "event": "pull_request_target",
        "head_branch": "feature",
        "head_sha": head,
        "path": ".github/workflows/hunter-governance-review.yml",
        "pull_requests": [target_pr],
    }
    runs[6] = {**runs[5], "event": "pull_request"}
    runs[7] = {**runs[5], "head_sha": "f" * 40}
    trusted_creator = {"login": "github-actions[bot]"}
    trusted, omitted = object(), object()

    def believed(run_id: int, *, creator: Any = trusted, description: str | None = None) -> str:
        status: dict[str, Any] = {
            "context": f"{orchestration.CONTEXT_PREFIX}{pr_number}",
            # Canonical four-field description (state|provider|trigger|digest): the shape the
            # candidate's own _parse_cycle documents as the base-generation cycle.
            "description": description if description is not None else f"REVIEWER_UNAVAILABLE||{run_id}|{digest}",
            "target_url": f"https://github.com/owner/repo/actions/runs/{run_id}",
            "created_at": "2026-01-01T00:00:00Z",
        }
        if creator is trusted:
            status["creator"] = trusted_creator
        elif creator is not omitted:
            status["creator"] = creator

        def request_json(_repository: str, _token: str, _method: str, path: str, payload: Any = None) -> Any:
            if path == f"pulls/{pr_number}":
                return {"state": "open", "head": target_pr["head"], "base": target_pr["base"]}
            if path.startswith(f"commits/{head}/statuses"):
                return [status]
            if path == "":
                return {"default_branch": "main"}
            if path == "commits/main":
                return {"sha": trusted_sha}
            if path == f"compare/{'f' * 40}...{trusted_sha}":
                return {"status": "diverged"}
            if path == f"actions/runs/{run_id}":
                return runs[run_id]
            raise AssertionError(f"unexpected request {path!r}")

        orchestration.request_json = request_json
        found, cycle, _error = orchestration.read_cycle("owner/repo", "token", pr_number, head)
        assert (cycle is not None) == (found == "present")
        return found

    publisher, collector, off_branch = believed(1), believed(2), believed(3)
    assert believed(5) == "present", "a bound pull_request_target publisher must be believed"
    assert believed(6) == "absent", "a candidate pull_request workflow must never authenticate a cycle"
    assert believed(7) == "absent", "a target event from a different head must never authenticate a cycle"
    wrong_head = believed(4)
    assert wrong_head == "absent", "a trusted workflow run from an unrelated commit must never authenticate a cycle"
    assert publisher == "present", f"a trusted publisher cycle must be believed, got {publisher!r}"
    assert collector == "absent", "a status citing the collector run must never be a cycle"
    assert off_branch == "absent", "a status citing a run off the default branch must never be a cycle"

    forged = believed(1, creator={"login": "mallory"})
    assert forged == "absent", "a status from an untrusted creator must never be a cycle"
    no_creator = believed(1, creator=omitted)
    assert no_creator == "absent", "a status with no creator must never be a cycle"
    assert believed(1, creator=None) == "absent", "a status with a null creator must never be a cycle"
    empty_creator = believed(1, creator={})
    assert empty_creator == "absent", "a status with an empty creator must never be a cycle"

    for label, description in (
        ("trailing delimiter with a malformed generation", f"REVIEWER_UNAVAILABLE||1|{digest}|not a generation!"),
        ("extra field", f"REVIEWER_UNAVAILABLE||1|{digest}||extra"),
        ("short digest", "REVIEWER_UNAVAILABLE||1|abc"),
        ("empty", ""),
    ):
        assert believed(1, description=description) == "absent", f"malformed description must be refused: {label}"
    return {
        "budget_positive": True,
        "publisher": publisher,
        "collector": collector,
        "off_branch": off_branch,
        "forged_creator": forged,
        "no_creator": no_creator,
    }


def scenario_b_v2(candidate_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
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
    # A synthetic validator that is never called by the production collector
    # finalization path is not a v2 contract. Exercise the real call site and
    # prove the candidate cannot finalize a receipt rejected by that validator.
    from unittest.mock import patch

    with patch.object(
        orchestration, "collector_receipt_problem", return_value="rejected by native validator"
    ) as validator:
        decision = orchestration.pool_exhaustion(
            receipt,
            repository="owner/repo",
            pr_number=pr_number,
            head_sha=head,
            run_id=run_id,
            run_attempt=1,
            claims_id="c" * 64,
            generation_id="",
            pool={},
        )
    assert validator.called, "production finalization does not consult the native receipt validator"
    assert (
        decision.exhausted is False and "rejected by native validator" in decision.reason
    ), "production finalization can ignore a failed native receipt validation"
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

    This scenario is identical for both v1 and v2 contracts.
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


#: Scenario registry: (contract_version, scenario_id) -> scenario function
SCENARIOS: dict[tuple[str, str], Any] = {
    ("v1", "A"): scenario_a_v1,
    ("v1", "B"): scenario_b_v1,
    ("v1", "G"): scenario_g,
    ("v2", "A"): scenario_a_v2,
    ("v2", "B"): scenario_b_v2,
    ("v2", "G"): scenario_g,
}
#: Wall-clock bound on one scenario child. Generous for real work, but bounds
#: a hostile candidate module that hangs (an infinite loop, a blocking call)
#: instead of ever returning or raising.
SCENARIO_CHILD_TIMEOUT_SECONDS = 60


def _scenario_child(
    conn: Connection, contract_version: str, scenario_id: str, candidate_root_str: str, fixture: dict[str, Any]
) -> None:
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
        measurements = SCENARIOS[(contract_version, scenario_id)](Path(candidate_root_str), fixture)
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


def _run_scenario_isolated(
    contract_version: str, scenario_id: str, candidate_root: Path, fixture: dict[str, Any]
) -> dict[str, Any]:
    """Runs one scenario in a child process and trusts only the exact ordered
    pair of pipe messages ``_scenario_child`` can produce -- never the
    child's stdout, and never its exit code, either of which candidate
    top-level import code fully controls once it has run at all.
    """

    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_scenario_child,
        args=(child_conn, contract_version, scenario_id, str(candidate_root), fixture),
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
    parser.add_argument("--contract-version", required=True, choices=["v1", "v2"])
    parser.add_argument("--scenario", required=True)
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
    contract_version = args.contract_version
    scenario_id = args.scenario

    # Validate scenario_id is valid for this contract version
    if (contract_version, scenario_id) not in SCENARIOS:
        valid_scenarios = [sid for (cv, sid) in SCENARIOS if cv == contract_version]
        raise ValueError(
            f"scenario {scenario_id!r} not valid for contract {contract_version}; valid: {valid_scenarios}"
        )

    # Computed here, in this process, which never imports candidate code
    # itself -- the scenario runs in a separate spawned child (see
    # _run_scenario_isolated) -- and never accepted from anything that child
    # reports, so a forged claim in its (untrusted) pipe messages cannot
    # substitute a fake digest for the real one. It is also computed *first*:
    # resolving the complete candidate input closure rejects a symlinked or
    # escaping candidate file (see resolve_candidate_file) before any candidate
    # module is imported, so a substituted trusted-checkout file is never run.
    result: dict[str, Any] = {"scenario_id": scenario_id, "contract_version": contract_version}
    result["candidate_module_digest"] = candidate_module_digest(candidate_root, scenario_id, contract_version)
    result.update(_run_scenario_isolated(contract_version, scenario_id, candidate_root, fixture))
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
