"""Tests for the Trusted Candidate Orchestrator Replay Harness.

Scenario logic is exercised through the real, subprocess-isolated worker
against small, self-contained candidate fixtures -- a "fixed" variant that
implements each invariant correctly and a "buggy" variant that plausibly
violates it -- so these tests prove the scenarios actually discriminate
correct from incorrect controller behavior, not merely that they run.
Fixtures are independent of any specific PR's exact source so this harness
carries no coupling to PR #535's own file content.

The second half of this file drives 11 adversarial trust-boundary tests
against ``validate_receipt``, each showing that a specific way a hostile
candidate (or a forged/hostile receipt) might try to manufacture a passing
result is instead rejected fail-closed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import hunter_trusted_orchestrator_replay as replay
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Real production regressions each corrected scenario must still catch. Every
#: entry is a narrow, named mutation of this repository's *real*
#: ``hunter_review_orchestrator.py``, so the negative tests below prove the
#: scenarios discriminate against production code itself. Substituting a
#: purpose-built fake for the module under test is precisely what let the prior
#: harness assert an invariant production never implemented while its own suite
#: stayed green, so the fixed/buggy module pairs that used to stand in for
#: production are gone.
#
#: Each mutation is ``(anchor, replacement)``; the anchor must still be present
#: in the real source, so a production refactor that moves these lines fails
#: loudly here instead of silently making a negative test vacuous.
DROPS_OPPORTUNITY_TERMINALITY = (
    """            if existing.state in PENDING_STATES and _older_than(
                existing.started_at, INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS
            ):""",
    "            if False:",
)

MAKES_OPPORTUNITY_NON_POSITIVE = (
    "INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS = 15 * 60",
    "INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS = 0",
)

MAKES_OPPORTUNITY_UNCONDITIONAL = (
    """            if existing.state in PENDING_STATES and _older_than(
                existing.started_at, INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS
            ):""",
    "            if existing.state in PENDING_STATES:",
)

DROPS_COLLECTOR_IDEMPOTENCY_RECORD = (
    """            if existing.trigger_id is not None and not collector_needs_dispatch(repository, token, existing):
                return existing""",
    "            pass",
)

DROPS_LIVENESS_RECOVERY = (
    """    if not _older_than(cycle.started_at, COLLECTOR_LIVENESS_GRACE_SECONDS):
        return False""",
    "    return False",
)

DROPS_DISPATCH_BUDGET = ("    if count >= MAX_COLLECTOR_DISPATCHES:", "    if False:")

#: Codex P1 on PR #539: the declared budget is wider than the threshold the
#: candidate actually enforces. Sampling the two probes far apart only proves
#: *some* cutoff sits between them, so a 1.5x threshold still passed.
OPPORTUNITY_THRESHOLD_EXCEEDS_DECLARED = (
    "                existing.started_at, INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS",
    "                existing.started_at, INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS * 3 // 2",
)

#: Codex P1 on PR #539: the recovery dispatch is real, but the durable cycle it
#: was dispatched for is never refreshed, so every later reconcile recovers the
#: same exact head again off the original stale record. Written as a
#: three-part mutation (a recovery flag, its initialisation, and a publish that
#: skips the recovery) because suppressing the publish for the recovery alone
#: requires distinguishing the recovery path from the first dispatch.
_RECOVERY_FLAG_INIT = (
    """    digest = reviewer_pool_config_digest()
    state, existing, _error = read_cycle(repository, token, pr_number, head_sha)""",
    """    digest = reviewer_pool_config_digest()
    _recovering = False
    state, existing, _error = read_cycle(repository, token, pr_number, head_sha)""",
)
_RECOVERY_FLAG_SET = (
    """            if existing.trigger_id is not None and not collector_needs_dispatch(repository, token, existing):
                return existing""",
    """            if existing.trigger_id is not None and not collector_needs_dispatch(repository, token, existing):
                return existing
            _recovering = True""",
)
_RECOVERY_PUBLISH_SKIPPED = (
    """    publish_cycle(repository, token, head_sha, cycle=cycle)
    dispatch_collector(repository, token, pr_number, head_sha, generation_id)
    return cycle""",
    """    if not _recovering:
        publish_cycle(repository, token, head_sha, cycle=cycle)
    dispatch_collector(repository, token, pr_number, head_sha, generation_id)
    return cycle""",
)
RECOVERY_DISPATCHES_WITHOUT_REFRESHING_RECORD = (
    _RECOVERY_FLAG_INIT,
    _RECOVERY_FLAG_SET,
    _RECOVERY_PUBLISH_SKIPPED,
)

HOSTILE_EARLY_EXIT_ORCHESTRATOR = """

# Hostile: forges a passing payload and terminates the whole process at
# import time, before any real scenario logic (or even this module's own
# ensure_collector) ever runs. Reproduces the Codex P1 finding against the
# prior stdout/exit-code-trusting design.
import json as _json
import os as _os

print(
    _json.dumps(
        {
            "scenario_id": "B",
            "outcome": "pass",
            "measurements": {"dispatches": 1, "liveness_calls": 1},
            "candidate_module_digest": "0" * 64,
        }
    )
)
_os._exit(0)
"""

#: Swallows the fail-closed path: a malformed cycle record is treated as "no
#: cycle" instead of raising, so an unreadable exact-head state would classify
#: as pending rather than blocking.
BUGGY_GOVERNANCE = """
import hunter_review_orchestrator as orchestration


def review_orchestration_state(repository, token, pr_number, head_sha):
    state, cycle, error = orchestration.read_cycle(repository, token, pr_number, head_sha)
    if state == "present" and cycle is not None:
        return cycle.state, f"provider={cycle.provider_id or 'unassigned'}"
    if state == "absent":
        return "WAITING_FOR_REVIEWER", "no trusted exact-head orchestration cycle has been published"
    raise RuntimeError(error or f"invalid review orchestration state: {state}")


def pending_review_authority_state(repository, token, pr_number, head_sha):
    # Buggy: swallows malformed/absent-with-error evidence instead of failing
    # closed, silently reporting it as ordinary pending review.
    try:
        cycle_state, detail = review_orchestration_state(repository, token, pr_number, head_sha)
    except RuntimeError:
        return "pending", "swallowed error"
    if cycle_state in {"REVIEW_IN_PROGRESS", "FAILOVER_IN_PROGRESS", "WAITING_FOR_REVIEWER"}:
        return "pending", f"{cycle_state}: {detail}"
    return "failure", f"MISSING_REVIEW_AUTHORITY: {cycle_state}: {detail}"
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _candidate_root(
    tmp_path: Path,
    *,
    orchestrator_mutations: tuple[tuple[str, str], ...] = (),
    orchestrator_src: str | None = None,
    governance_src: str | None = None,
) -> Path:
    """Build a candidate root out of this repository's *real* production modules.

    Scenarios are validated against production code itself. A negative test
    proves a scenario still catches a real regression either by applying a
    narrow, named mutation to the real orchestrator (``orchestrator_mutations``)
    or by replacing exactly one module outright (``orchestrator_src`` /
    ``governance_src``). Standing in a synthetic module for the code under test
    is what previously let the harness assert an invariant production never
    implemented while its own suite stayed green.
    """
    root = tmp_path / "candidate"
    shutil.copytree(REPO_ROOT / "scripts", root / "scripts")
    shutil.copytree(REPO_ROOT / "src", root / "src")
    (root / "docs").mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO_ROOT / "docs" / "CODE_WRITE_POLICY.json", root / "docs" / "CODE_WRITE_POLICY.json")

    orchestrator_path = root / "scripts" / "hunter_review_orchestrator.py"
    source = orchestrator_path.read_text(encoding="utf-8") if orchestrator_src is None else orchestrator_src
    for anchor, replacement in orchestrator_mutations:
        assert anchor in source, f"mutation anchor missing from candidate orchestrator: {anchor!r}"
        source = source.replace(anchor, replacement, 1)
    _write(orchestrator_path, source)
    if governance_src is not None:
        _write(root / "scripts" / "hunter_governance_review_v2.py", governance_src)
    return root


def _fixture_path(tmp_path: Path) -> Path:
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(replay.FIXTURE), encoding="utf-8")
    return path


# --- Scenario A: bounded review opportunity that ends a cycle ---------------


def test_scenario_a_passes_against_real_production(tmp_path):
    """The scenario must pass against the candidate's real production
    orchestrator. The pre-fix harness could not do this at all: it called an
    API production never had, so every fixture in this file substituted a
    synthetic module and the suite stayed green while the real replay failed.
    """

    root = _candidate_root(tmp_path)
    result = replay._run_scenario("A", root, _fixture_path(tmp_path), workspace_root=tmp_path)
    assert result["outcome"] == "pass", result.get("error")
    measurements = result["measurements"]
    budget = measurements["opportunity_seconds"]
    assert budget > 0
    # The two probes must straddle the candidate's own declared budget
    # immediately, not merely sit on either side of some wider threshold.
    assert measurements["below_boundary_seconds"] < budget < measurements["above_boundary_seconds"], (
        f"the boundary probes must straddle the declared {budget}s budget, got "
        f"{measurements['below_boundary_seconds']}s and {measurements['above_boundary_seconds']}s"
    )
    assert (
        budget - measurements["below_boundary_seconds"] <= 2
    ), "the inside probe must sit immediately below the boundary"
    assert (
        measurements["above_boundary_seconds"] - budget <= 2
    ), "the outside probe must sit immediately above the boundary"
    assert measurements["timed_out_state"] == "REVIEW_TIMED_OUT"
    assert measurements["in_opportunity_state"] != "REVIEW_TIMED_OUT"


def test_scenario_a_fails_when_the_real_threshold_exceeds_the_declared_budget(tmp_path):
    """Codex P1 on PR #539: a candidate whose real timeout cutoff is wider than
    the budget it declares must be rejected. The pre-fix probes (1s inside,
    2x outside) both sat inside such a candidate's 1.5x threshold, so it passed
    while still leaving an exact head pending well past the bound it advertised.
    """

    root = _candidate_root(tmp_path, orchestrator_mutations=(OPPORTUNITY_THRESHOLD_EXCEEDS_DECLARED,))
    result = replay._run_scenario("A", root, _fixture_path(tmp_path), workspace_root=tmp_path)
    assert result["outcome"] == "fail"
    assert "real timeout threshold is wider than the budget it declares" in result["error"]


def test_scenario_a_fails_when_a_pending_cycle_can_stay_pending_forever(tmp_path):
    """Regression: production's own contract is that a completed or timed-out
    opportunity must never leave the exact-head status pending forever. A
    candidate that stops enforcing that must be rejected."""

    root = _candidate_root(tmp_path, orchestrator_mutations=(DROPS_OPPORTUNITY_TERMINALITY,))
    result = replay._run_scenario("A", root, _fixture_path(tmp_path), workspace_root=tmp_path)
    assert result["outcome"] == "fail"
    assert "must reach a terminal state" in result["error"]


def test_scenario_a_fails_when_the_opportunity_is_not_a_real_bound(tmp_path):
    """A non-positive budget is not a bound, so it cannot be the thing that ends
    a cycle."""

    root = _candidate_root(tmp_path, orchestrator_mutations=(MAKES_OPPORTUNITY_NON_POSITIVE,))
    result = replay._run_scenario("A", root, _fixture_path(tmp_path), workspace_root=tmp_path)
    assert result["outcome"] == "fail"
    assert "must be strictly positive" in result["error"]


def test_scenario_a_fails_when_the_opportunity_is_ignored(tmp_path):
    """Proves the in-budget half of the invariant is load-bearing: timing out
    every pending cycle regardless of age is an unbounded wait in the other
    direction and must not pass."""

    root = _candidate_root(tmp_path, orchestrator_mutations=(MAKES_OPPORTUNITY_UNCONDITIONAL,))
    result = replay._run_scenario("A", root, _fixture_path(tmp_path), workspace_root=tmp_path)
    assert result["outcome"] == "fail"
    assert "must not be timed out" in result["error"]


# --- Scenario B: bounded, idempotent exact-head collector dispatch ----------


def test_scenario_b_passes_against_real_production(tmp_path):
    root = _candidate_root(tmp_path)
    result = replay._run_scenario("B", root, _fixture_path(tmp_path), workspace_root=tmp_path)
    assert result["outcome"] == "pass", result.get("error")
    measurements = result["measurements"]
    # The budget is spent by the runs the real dispatches created, so the
    # dispatch count and the correlated-run count agree on the bound.
    assert measurements["dispatches"] == measurements["max_dispatches"], (
        f"dispatch must stop at the candidate's own budget of {measurements['max_dispatches']}, "
        f"got {measurements['dispatches']}"
    )
    assert measurements["correlated_runs"] == measurements["max_dispatches"], (
        "the dispatch budget must be consumed by the runs production actually created, got "
        f"{measurements['correlated_runs']} run(s)"
    )
    # The recovery refreshes the durable record, so more than one durable cycle
    # exists beyond the initial record and the stale probe.
    assert measurements["durable_records_published"] > 1
    assert measurements["exhausted_state"] == "WAITING_FOR_REVIEWER"


def test_scenario_b_fails_when_recovery_does_not_refresh_the_durable_record(tmp_path):
    """Codex P1 on PR #539: a candidate may dispatch the recovery and still
    leave the original stale durable record in place. Every later reconcile then
    recovers the same exact head again off a record that never changed. The
    pre-fix scenario substituted a synthetic liveness count at exactly this
    point, so it never read the durable record back and this passed.
    """

    root = _candidate_root(tmp_path, orchestrator_mutations=RECOVERY_DISPATCHES_WITHOUT_REFRESHING_RECORD)
    result = replay._run_scenario("B", root, _fixture_path(tmp_path), workspace_root=tmp_path)
    assert result["outcome"] == "fail"
    assert "must publish/refresh the durable cycle record" in result["error"]


def test_scenario_b_fails_when_a_recorded_cycle_is_dispatched_again(tmp_path):
    """Regression: the published cycle is the durable idempotency record, so a
    reconcile that can observe it must never issue a duplicate dispatch."""

    root = _candidate_root(tmp_path, orchestrator_mutations=(DROPS_COLLECTOR_IDEMPOTENCY_RECORD,))
    result = replay._run_scenario("B", root, _fixture_path(tmp_path), workspace_root=tmp_path)
    assert result["outcome"] == "fail"
    assert "must dispatch no further collector" in result["error"]


def test_scenario_b_fails_without_bounded_liveness_recovery(tmp_path):
    """A dispatch that never produces a run must recover, or the exact head
    stays pending forever."""

    root = _candidate_root(tmp_path, orchestrator_mutations=(DROPS_LIVENESS_RECOVERY,))
    result = replay._run_scenario("B", root, _fixture_path(tmp_path), workspace_root=tmp_path)
    assert result["outcome"] == "fail"
    assert "must recover exactly one further dispatch" in result["error"]


def test_scenario_b_fails_when_dispatch_is_unbounded(tmp_path):
    """Production states the bound explicitly: a cycle that keeps failing must
    settle into a blocked pending state rather than dispatch without end."""

    root = _candidate_root(tmp_path, orchestrator_mutations=(DROPS_DISPATCH_BUDGET,))
    result = replay._run_scenario("B", root, _fixture_path(tmp_path), workspace_root=tmp_path)
    assert result["outcome"] == "fail"
    assert "must stay pending rather than dispatching without end" in result["error"]


def test_scenario_fails_against_a_candidate_that_forges_a_passing_payload_and_exits_early(tmp_path):
    """A candidate module that prints a fabricated 'pass' JSON line and calls
    os._exit(0) at import time -- before any real scenario logic ever runs --
    must not be accepted as a genuine pass (Codex P1 finding on PR #536): the
    isolated scenario channel never delivers the required 'done' message, so
    the harness reports fail rather than being fooled by forged stdout."""

    root = _candidate_root(tmp_path, orchestrator_src=HOSTILE_EARLY_EXIT_ORCHESTRATOR)
    result = replay._run_scenario("B", root, _fixture_path(tmp_path), workspace_root=tmp_path)
    assert result["outcome"] == "fail"
    assert "trusted completion message" in result["error"]


# --- Scenario G: WAITING_FOR_REVIEWER -> pending classification ------------


def test_scenario_g_passes_against_correct_end_to_end_classification(tmp_path):
    root = _candidate_root(tmp_path)
    result = replay._run_scenario("G", root, _fixture_path(tmp_path), workspace_root=tmp_path)
    assert result["outcome"] == "pass", result.get("error")


def test_scenario_g_fails_against_a_swallowed_fail_closed_path(tmp_path):
    root = _candidate_root(tmp_path, governance_src=BUGGY_GOVERNANCE)
    result = replay._run_scenario("G", root, _fixture_path(tmp_path), workspace_root=tmp_path)
    assert result["outcome"] == "fail"
    assert "fail closed" in result["error"]


# --- Trusted digests are pure, deterministic, and content-sensitive -------
#
# Each digest is checked two ways: calling it twice with nothing changed
# must agree (determinism), and changing the underlying trusted content it
# covers must change the digest (content sensitivity) -- proving these
# assertions are non-vacuous, since a function that always returned a fixed
# constant would pass a same-value comparison but fail this second check.


def test_trusted_harness_definition_digest_is_deterministic_and_content_sensitive(monkeypatch):
    first = replay.trusted_harness_definition_digest()
    second = replay.trusted_harness_definition_digest()
    assert first == second

    monkeypatch.setattr(replay, "TRUSTED_DEFINITION_FILES", replay.TRUSTED_DEFINITION_FILES[:1])
    changed = replay.trusted_harness_definition_digest()
    assert changed != first, "digest must depend on which trusted files it covers, not be a constant"


def test_scenario_set_digest_is_deterministic_and_content_sensitive(monkeypatch):
    first = replay.scenario_set_digest()
    second = replay.scenario_set_digest()
    assert first == second

    mutated_invariants = dict(replay.SCENARIO_INVARIANTS)
    mutated_invariants["A"] = mutated_invariants["A"] + " (mutated for this test)"
    monkeypatch.setattr(replay, "SCENARIO_INVARIANTS", mutated_invariants)
    changed = replay.scenario_set_digest()
    assert changed != first, "digest must depend on the actual invariant text, not be a constant"


def test_fixture_digest_is_deterministic_and_content_sensitive(monkeypatch):
    first = replay.fixture_digest()
    second = replay.fixture_digest()
    assert first == second

    mutated_fixture = dict(replay.FIXTURE)
    mutated_fixture["run_id"] = int(mutated_fixture["run_id"]) + 1
    monkeypatch.setattr(replay, "FIXTURE", mutated_fixture)
    changed = replay.fixture_digest()
    assert changed != first, "digest must depend on the actual fixture content, not be a constant"


def _good_receipt(tmp_path: Path) -> tuple[dict, Path]:
    root = _candidate_root(tmp_path)
    receipt = replay.build_receipt(candidate_root=root, pr_number=535, candidate_sha="a" * 40, workspace_root=tmp_path)
    return receipt, root


def test_build_receipt_round_trips_through_validate(tmp_path):
    receipt, root = _good_receipt(tmp_path)
    assert receipt["overall_result"] == "pass"
    errors = replay.validate_receipt(receipt, candidate_root=root, pr_number=535, candidate_sha="a" * 40)
    assert errors == []


# --- Trusted-field validation and candidate-digest verification split ------
#
# The hosted workflow splits validate_receipt into two calls run in separate
# jobs with different privilege levels, precisely so the job that eventually
# publishes a status never also checks out untrusted candidate content (see
# verify_candidate_module_digests's docstring). These tests exercise that
# split directly, including through the real two-step CLI the workflow uses.


def test_trusted_fields_validation_needs_no_candidate_access(tmp_path):
    receipt, _root = _good_receipt(tmp_path)
    receipt["schema"] = "forged"
    errors = replay.validate_receipt_trusted_fields(receipt, pr_number=535, candidate_sha="a" * 40)
    assert any("schema" in e for e in errors)


def test_candidate_digest_verification_alone_catches_a_forged_digest(tmp_path):
    receipt, root = _good_receipt(tmp_path)
    for entry in receipt["scenario_results"]:
        if entry["scenario_id"] == "B":
            entry["candidate_module_digest"] = "0" * 64
    errors = replay.verify_candidate_module_digests(receipt, root)
    assert any("candidate_module_digest" in e for e in errors)


def test_verify_digests_and_validate_cli_round_trip_through_two_unprivileged_steps(tmp_path):
    receipt, root = _good_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    digest_check_path = tmp_path / "digest-check.json"
    script = Path(replay.__file__)

    verify_run = subprocess.run(
        [
            sys.executable,
            str(script),
            "verify-digests",
            "--workspace-root",
            str(tmp_path),
            "--receipt",
            receipt_path.name,
            "--candidate-root",
            root.name,
            "--out",
            digest_check_path.name,
        ],
        capture_output=True,
        text=True,
    )
    assert verify_run.returncode == 0, verify_run.stderr
    assert json.loads(digest_check_path.read_text())["errors"] == []

    validate_run = subprocess.run(
        [
            sys.executable,
            str(script),
            "validate",
            "--workspace-root",
            str(tmp_path),
            "--receipt",
            receipt_path.name,
            "--digest-check",
            digest_check_path.name,
            "--pr",
            "535",
            "--candidate-sha",
            "a" * 40,
        ],
        capture_output=True,
        text=True,
    )
    assert validate_run.returncode == 0, validate_run.stderr
    assert "REPLAY VALIDATION PASSED" in validate_run.stdout


def test_validate_cli_rejects_a_digest_check_reporting_errors(tmp_path):
    receipt, _root = _good_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    digest_check_path = tmp_path / "digest-check.json"
    digest_check_path.write_text(json.dumps({"errors": ["forged module digest detected"]}), encoding="utf-8")

    validate_run = subprocess.run(
        [
            sys.executable,
            str(Path(replay.__file__)),
            "validate",
            "--workspace-root",
            str(tmp_path),
            "--receipt",
            receipt_path.name,
            "--digest-check",
            digest_check_path.name,
            "--pr",
            "535",
            "--candidate-sha",
            "a" * 40,
        ],
        capture_output=True,
        text=True,
    )
    assert validate_run.returncode == 1
    assert "forged module digest detected" in validate_run.stderr


# --- Path-confinement adversarial tests (Sonar path-traversal findings) ----


def test_run_cli_rejects_a_traversal_candidate_root(tmp_path):
    root = _candidate_root(tmp_path)
    script = Path(replay.__file__)
    (tmp_path / "workspace").mkdir()

    run = subprocess.run(
        [
            sys.executable,
            str(script),
            "run",
            "--workspace-root",
            str(tmp_path / "workspace"),
            "--candidate-root",
            f"../{root.name}",
            "--pr",
            "535",
            "--candidate-sha",
            "a" * 40,
            "--out",
            "receipt.json",
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode != 0
    assert "traversal" in run.stderr


def test_run_cli_rejects_an_absolute_candidate_root(tmp_path):
    root = _candidate_root(tmp_path)
    script = Path(replay.__file__)

    run = subprocess.run(
        [
            sys.executable,
            str(script),
            "run",
            "--workspace-root",
            str(tmp_path),
            "--candidate-root",
            str(root),
            "--pr",
            "535",
            "--candidate-sha",
            "a" * 40,
            "--out",
            "receipt.json",
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode != 0
    assert "absolute" in run.stderr


def test_candidate_root_resolution_rejects_a_symlink_escaping_the_workspace_root(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    escape_link = workspace / "escape"
    escape_link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside the trusted workspace root"):
        replay.resolve_candidate_root(workspace.resolve(), "escape")


def test_verify_digests_cli_rejects_a_wrong_type_candidate_root(tmp_path):
    root = _candidate_root(tmp_path)
    receipt = replay.build_receipt(candidate_root=root, pr_number=535, candidate_sha="a" * 40, workspace_root=tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    # A file, not a directory, given as --candidate-root.
    not_a_dir = tmp_path / "not_a_dir"
    not_a_dir.write_text("nope", encoding="utf-8")
    script = Path(replay.__file__)

    run = subprocess.run(
        [
            sys.executable,
            str(script),
            "verify-digests",
            "--workspace-root",
            str(tmp_path),
            "--receipt",
            receipt_path.name,
            "--candidate-root",
            not_a_dir.name,
            "--out",
            "digest-check.json",
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode != 0
    result = json.loads((tmp_path / "digest-check.json").read_text())
    assert any("directory" in error for error in result["errors"])


def test_validate_cli_rejects_a_receipt_path_substituted_outside_the_workspace(tmp_path):
    receipt, _root = _good_receipt(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_receipt = tmp_path / "outside-receipt.json"
    outside_receipt.write_text(json.dumps(receipt), encoding="utf-8")

    validate_run = subprocess.run(
        [
            sys.executable,
            str(Path(replay.__file__)),
            "validate",
            "--workspace-root",
            str(workspace),
            "--receipt",
            "../outside-receipt.json",
            "--digest-check",
            "digest-check.json",
            "--pr",
            "535",
            "--candidate-sha",
            "a" * 40,
        ],
        capture_output=True,
        text=True,
    )
    assert validate_run.returncode == 1
    assert "traversal" in validate_run.stderr


def test_run_cli_rejects_an_out_path_substituted_outside_the_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    _candidate_root(workspace)

    run = subprocess.run(
        [
            sys.executable,
            str(Path(replay.__file__)),
            "run",
            "--workspace-root",
            str(workspace),
            "--candidate-root",
            "candidate",
            "--pr",
            "535",
            "--candidate-sha",
            "a" * 40,
            "--out",
            "../escape-receipt.json",
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode != 0
    assert "traversal" in run.stderr
    assert not (tmp_path / "escape-receipt.json").exists()


def test_run_cli_rejects_a_candidate_root_that_escapes_a_substituted_alternate_workspace_root(tmp_path):
    """Swapping which (legitimate, existing) directory is declared as
    --workspace-root must not let a relative --candidate-root reach outside
    *that* root: confinement is enforced against whichever workspace root is
    actually given, never against some other, real one the caller has in
    mind.
    """

    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    _candidate_root(real_workspace)
    alt_workspace = tmp_path / "alt-workspace"
    alt_workspace.mkdir()

    run = subprocess.run(
        [
            sys.executable,
            str(Path(replay.__file__)),
            "run",
            "--workspace-root",
            str(alt_workspace),
            "--candidate-root",
            "../real-workspace/candidate",
            "--pr",
            "535",
            "--candidate-sha",
            "a" * 40,
            "--out",
            "receipt.json",
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode != 0
    assert "traversal" in run.stderr


def test_verify_digests_cli_rejects_an_alternate_candidate_root_substitution(tmp_path):
    """A caller (or a compromised validate job) that swaps in a different
    candidate checkout than the one the receipt's digests were computed
    against must be caught by independent recomputation, never accepted as
    an equivalent candidate root.
    """

    receipt, _root = _good_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    # A second, differently-sourced candidate checkout under the same
    # workspace -- structurally valid, but not the one the receipt was built
    # against, so its recomputed digests must not match. Built in a separate
    # parent directory first, since _candidate_root always names its output
    # "candidate" and the real one already occupies that name in tmp_path.
    unrenamed_alternate_root = _candidate_root(
        tmp_path / "alt-source",
        orchestrator_mutations=(DROPS_DISPATCH_BUDGET,),
        governance_src=(REPO_ROOT / "scripts" / "hunter_governance_review_v2.py").read_text(encoding="utf-8")
        + "\n# alternate candidate checkout\n",
    )
    alternate_root = unrenamed_alternate_root.rename(tmp_path / "candidate-alternate")
    digest_check_path = tmp_path / "digest-check.json"

    run = subprocess.run(
        [
            sys.executable,
            str(Path(replay.__file__)),
            "verify-digests",
            "--workspace-root",
            str(tmp_path),
            "--receipt",
            receipt_path.name,
            "--candidate-root",
            alternate_root.name,
            "--out",
            digest_check_path.name,
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 1
    assert json.loads(digest_check_path.read_text())["errors"] != []


@pytest.mark.parametrize(
    "malformed_sha",
    [
        pytest.param("a" * 39, id="too_short"),
        pytest.param("a" * 41, id="too_long"),
        pytest.param("g" * 40, id="non_hex_characters"),
        pytest.param("../../../etc/passwd", id="traversal_text"),
        pytest.param("a" * 20 + "/etc/passwd", id="path_separator"),
        pytest.param("a" * 30 + ";rm -rf /", id="shell_metacharacters"),
        pytest.param("a" * 30 + "$(whoami)", id="command_substitution"),
        pytest.param("", id="empty"),
    ],
)
def test_run_cli_rejects_a_malformed_candidate_sha(tmp_path, malformed_sha):
    root = _candidate_root(tmp_path)
    (tmp_path / "workspace").mkdir()

    run = subprocess.run(
        [
            sys.executable,
            str(Path(replay.__file__)),
            "run",
            "--workspace-root",
            str(tmp_path / "workspace"),
            "--candidate-root",
            str(root),
            "--pr",
            "535",
            "--candidate-sha",
            malformed_sha,
            "--out",
            "receipt.json",
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode != 0
    assert "40 hexadecimal" in run.stderr
    assert not (tmp_path / "workspace" / "receipt.json").exists()


def test_validate_cli_rejects_a_malformed_candidate_sha(tmp_path):
    receipt, _root = _good_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    digest_check_path = tmp_path / "digest-check.json"
    digest_check_path.write_text(json.dumps({"errors": []}), encoding="utf-8")

    validate_run = subprocess.run(
        [
            sys.executable,
            str(Path(replay.__file__)),
            "validate",
            "--workspace-root",
            str(tmp_path),
            "--receipt",
            receipt_path.name,
            "--digest-check",
            digest_check_path.name,
            "--pr",
            "535",
            "--candidate-sha",
            "not-a-sha",
        ],
        capture_output=True,
        text=True,
    )
    assert validate_run.returncode == 1
    assert "40 hexadecimal" in validate_run.stderr


def test_validate_candidate_sha_accepts_exactly_40_hex_characters():
    assert replay.validate_candidate_sha("a" * 40) == "a" * 40
    assert replay.validate_candidate_sha("F" * 40) == "F" * 40


@pytest.mark.parametrize(
    "malformed_sha",
    ["a" * 39, "g" * 40, "../etc/passwd", "a" * 20 + "/x", "a" * 30 + ";touch pwned"],
)
def test_validate_candidate_sha_rejects_anything_not_exactly_40_hex_characters(malformed_sha):
    with pytest.raises(ValueError, match="40 hexadecimal"):
        replay.validate_candidate_sha(malformed_sha)


# --- 11 adversarial trust-boundary tests ------------------------------------


def test_adversarial_1_forged_candidate_sha_is_rejected(tmp_path):
    receipt, root = _good_receipt(tmp_path)
    errors = replay.validate_receipt(receipt, candidate_root=root, pr_number=535, candidate_sha="f" * 40)
    assert any("candidate_sha" in e for e in errors)


def test_adversarial_2_forged_candidate_pr_is_rejected(tmp_path):
    receipt, root = _good_receipt(tmp_path)
    errors = replay.validate_receipt(receipt, candidate_root=root, pr_number=999, candidate_sha="a" * 40)
    assert any("candidate_pr" in e for e in errors)


def test_adversarial_3_forged_trusted_harness_definition_digest_is_rejected(tmp_path):
    receipt, root = _good_receipt(tmp_path)
    receipt["trusted_harness_definition_digest"] = "0" * 64
    errors = replay.validate_receipt(receipt, candidate_root=root, pr_number=535, candidate_sha="a" * 40)
    assert any("trusted_harness_definition_digest" in e for e in errors)


def test_adversarial_4_weakened_scenario_invariant_text_is_rejected(tmp_path):
    receipt, root = _good_receipt(tmp_path)
    receipt["scenario_results"][0]["invariant"] = "a weaker, candidate-supplied invariant"
    errors = replay.validate_receipt(receipt, candidate_root=root, pr_number=535, candidate_sha="a" * 40)
    assert any("invariant text" in e for e in errors)
    assert any("scenario_set_digest" in e for e in errors) or any("invariant" in e for e in errors)


def test_adversarial_5_forged_input_fixture_digest_is_rejected(tmp_path):
    receipt, root = _good_receipt(tmp_path)
    receipt["input_fixture_digest"] = "0" * 64
    errors = replay.validate_receipt(receipt, candidate_root=root, pr_number=535, candidate_sha="a" * 40)
    assert any("input_fixture_digest" in e for e in errors)


def test_adversarial_6_missing_required_scenario_is_rejected(tmp_path):
    receipt, root = _good_receipt(tmp_path)
    receipt["scenario_results"] = [r for r in receipt["scenario_results"] if r["scenario_id"] != "G"]
    errors = replay.validate_receipt(receipt, candidate_root=root, pr_number=535, candidate_sha="a" * 40)
    assert any("exactly" in e and "scenario_results" in e for e in errors)


def test_adversarial_7_substituted_extra_scenario_id_is_rejected(tmp_path):
    receipt, root = _good_receipt(tmp_path)
    forged = dict(receipt["scenario_results"][0])
    forged["scenario_id"] = "Z"
    receipt["scenario_results"] = [r for r in receipt["scenario_results"] if r["scenario_id"] != "A"] + [forged]
    errors = replay.validate_receipt(receipt, candidate_root=root, pr_number=535, candidate_sha="a" * 40)
    assert any("exactly" in e and "scenario_results" in e for e in errors)


def test_adversarial_8_forged_candidate_module_digest_is_rejected(tmp_path):
    receipt, root = _good_receipt(tmp_path)
    for entry in receipt["scenario_results"]:
        if entry["scenario_id"] == "A":
            entry["candidate_module_digest"] = "0" * 64
    errors = replay.validate_receipt(receipt, candidate_root=root, pr_number=535, candidate_sha="a" * 40)
    assert any("candidate_module_digest" in e for e in errors)


def test_adversarial_9_inconsistent_overall_result_is_rejected(tmp_path):
    receipt, root = _good_receipt(tmp_path)
    for entry in receipt["scenario_results"]:
        if entry["scenario_id"] == "B":
            entry["outcome"] = "fail"
    receipt["overall_result"] = "pass"
    errors = replay.validate_receipt(receipt, candidate_root=root, pr_number=535, candidate_sha="a" * 40)
    assert any("overall_result" in e for e in errors)
    assert any("outcome is 'fail'" in e for e in errors)


def test_adversarial_10_reversed_or_missing_timestamps_are_rejected(tmp_path):
    receipt, root = _good_receipt(tmp_path)
    receipt["scenario_results"][0]["started_at"] = receipt["scenario_results"][0]["finished_at"]
    receipt["scenario_results"][0]["finished_at"] = "2020-01-01T00:00:00.000000Z"
    errors = replay.validate_receipt(receipt, candidate_root=root, pr_number=535, candidate_sha="a" * 40)
    assert any("precedes" in e for e in errors)


def test_adversarial_11_malformed_receipt_json_fails_closed(tmp_path):
    root = _candidate_root(tmp_path)
    errors = replay.validate_receipt("not a dict", candidate_root=root, pr_number=535, candidate_sha="a" * 40)
    assert errors == ["receipt is not a JSON object"]


# --- No production authority resolver may consult this diagnostic status --


@pytest.mark.parametrize(
    "relative_path",
    [
        "scripts/hunter_merge_readiness_v2.py",
        "scripts/hunter_governance_review_v2.py",
        "scripts/hunter_defect_prevention_preflight.py",
    ],
)
def test_no_production_authority_resolver_consults_the_replay_status(relative_path):
    root = Path(__file__).resolve().parents[1]
    source = (root / relative_path).read_text(encoding="utf-8")
    assert "Hunter Trusted Orchestrator Replay" not in source
    assert not re.search(r"orchestrator[-_]replay", source, re.IGNORECASE)


# --- S7631: the privileged publisher is checkout-free by construction ------
#
# githubactions:S7631 ("no untrusted code executed from a fork") was still
# open after pinning the trusted-controller checkout's ref to the literal
# `main`: Sonar's live trace kept flagging the mere presence of
# actions/checkout inside the workflow_run-triggered (privileged) job,
# regardless of its ref. The structural fix removes that checkout entirely --
# these tests prove it stays removed, that nothing replaces it with an
# equivalent repository-materializing or repository-executing step, and that
# the inline validation logic that took its place still behaves identically
# to validate_receipt_trusted_fields for both an accepted and a rejected
# receipt.


def _publish_workflow_path() -> Path:
    return (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-trusted-orchestrator-replay-publish.yml"
    )


def _publish_workflow_text() -> str:
    return _publish_workflow_path().read_text(encoding="utf-8")


def _publish_workflow_doc() -> dict:
    import yaml

    return yaml.safe_load(_publish_workflow_text())


def _publish_workflow_step_directives() -> list[str]:
    """Every step's `uses:`/`run:` value -- i.e. what the job actually does,
    as YAML parses it, excluding prose comments (which this file's own
    header comments legitimately mention "actions/checkout" and "git clone"
    *by name*, to explain what is no longer present).
    """

    doc = _publish_workflow_doc()
    steps = doc["jobs"]["publish-proof"]["steps"]
    directives = []
    for step in steps:
        if "uses" in step:
            directives.append(step["uses"])
        if "run" in step:
            directives.append(step["run"])
    return directives


def test_publish_workflow_contains_no_checkout_action():
    for directive in _publish_workflow_step_directives():
        assert "actions/checkout" not in directive


def test_publish_workflow_contains_no_git_clone_fetch_or_checkout_commands():
    for directive in _publish_workflow_step_directives():
        for forbidden in ("git clone", "git fetch", "git checkout", "git pull"):
            assert forbidden not in directive, f"found {forbidden!r} in the checkout-free privileged publisher"


def test_publish_workflow_executes_no_repository_script():
    text = _publish_workflow_text()
    # The only `python` invocations left are `python - <<'PY' ... PY`
    # heredocs running this file's own inline, non-repository script text --
    # never `python scripts/...` (a repository-hosted script path).
    assert "python scripts/" not in text
    assert re.search(r"^\s*python - <<'PY'\s*$", text, re.MULTILINE)


def test_publish_workflow_inline_validation_treats_downloaded_artifacts_only_as_data():
    doc = _publish_workflow_doc()
    steps = doc["jobs"]["publish-proof"]["steps"]
    validate_step = next(s for s in steps if s.get("id") == "validate_receipt")
    inline_source = validate_step["run"]
    # Only ever opened/parsed as text/JSON -- never executed, imported, or
    # handed to a subprocess/shell.
    for forbidden in ("exec(", "eval(", "import_module", "__import__(", "subprocess", "os.system", "os.popen"):
        assert forbidden not in inline_source, f"found {forbidden!r} in the checkout-free inline validator"
    assert "json.load" in inline_source or "json.loads" in inline_source


def test_publish_workflow_uses_no_candidate_controlled_field_as_an_executable_path():
    doc = _publish_workflow_doc()
    steps = doc["jobs"]["publish-proof"]["steps"]
    for step in steps:
        run_text = step.get("run")
        if not run_text:
            continue
        # No candidate/workflow_run-sourced expression is ever spliced
        # directly into a shell/run body -- every such value is threaded
        # through `env:` and read back as a shell/Python variable instead
        # (the same invariant the earlier expression-injection fix
        # established, re-checked here so a future edit can't reintroduce it
        # specifically in the now-checkout-free publisher).
        assert not re.search(r"\$\{\{\s*github\.event\.(pull_request|workflow_run)\.", run_text)


def test_publish_workflow_pr_head_binding_remains_fail_closed():
    doc = _publish_workflow_doc()
    steps = doc["jobs"]["publish-proof"]["steps"]
    context_step = next(s for s in steps if s.get("id") == "context")
    assert context_step["env"]["RUN_ID"] == "${{ github.event.workflow_run.id }}"
    assert "pull_requests[0].number" in context_step["run"]
    assert "exit 1" in context_step["run"]


def _extract_inline_validate_script() -> str:
    import textwrap

    doc = _publish_workflow_doc()
    steps = doc["jobs"]["publish-proof"]["steps"]
    run_text = next(s["run"] for s in steps if s.get("id") == "validate_receipt")
    lines = run_text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "python - <<'PY'")
    end = next(i for i, line in enumerate(lines) if i > start and line.strip() == "PY")
    return textwrap.dedent("\n".join(lines[start + 1 : end]))


def test_inline_validate_script_is_syntactically_valid_python():
    import ast

    ast.parse(_extract_inline_validate_script())


def _run_inline_validate_script(
    work: Path, *, conclusion: str, pr_number: int, candidate_sha: str
) -> tuple[int, str, str, str]:
    import os as _os

    script_path = work / "inline_validate.py"
    script_path.write_text(_extract_inline_validate_script(), encoding="utf-8")
    github_output = work / "github_output.txt"
    github_output.write_text("", encoding="utf-8")
    env = dict(_os.environ)
    env.update(
        {
            "WORKFLOW_CONCLUSION": conclusion,
            "PR_NUMBER": str(pr_number),
            "CANDIDATE_SHA": candidate_sha,
            "GITHUB_OUTPUT": str(github_output),
        }
    )
    result = subprocess.run([sys.executable, str(script_path)], cwd=str(work), env=env, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr, github_output.read_text(encoding="utf-8")


def _write_trusted_fetched_files(work: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    (work / "trusted-controller.fetched").write_text(
        (repo_root / "scripts" / "hunter_trusted_orchestrator_replay.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (work / "trusted-worker.fetched").write_text(
        (repo_root / "scripts" / "hunter_trusted_orchestrator_replay_worker.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (work / "trusted-definition.fetched").write_text(
        (repo_root / "scripts" / "hunter_trusted_orchestrator_replay_definition.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (work / "trusted-main-sha.fetched").write_text(replay._trusted_harness_sha(), encoding="utf-8")


def test_checkout_free_inline_validator_accepts_a_genuinely_good_receipt(tmp_path):
    receipt, _root = _good_receipt(tmp_path)
    (tmp_path / "replay-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    (tmp_path / "digest-check.json").write_text(json.dumps({"errors": []}), encoding="utf-8")
    _write_trusted_fetched_files(tmp_path)

    returncode, stdout, _stderr, github_output = _run_inline_validate_script(
        tmp_path, conclusion="success", pr_number=535, candidate_sha="a" * 40
    )
    assert returncode == 0
    assert "REPLAY VALIDATION PASSED" in stdout
    assert "result=success" in github_output

    # And it agrees with the trusted module's own validate_receipt_trusted_fields
    # for the identical receipt and trusted event fields.
    assert replay.validate_receipt_trusted_fields(receipt, pr_number=535, candidate_sha="a" * 40) == []


def test_checkout_free_inline_validator_rejects_a_forged_candidate_sha(tmp_path):
    receipt, _root = _good_receipt(tmp_path)
    receipt["candidate_sha"] = "f" * 40
    (tmp_path / "replay-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    (tmp_path / "digest-check.json").write_text(json.dumps({"errors": []}), encoding="utf-8")
    _write_trusted_fetched_files(tmp_path)

    returncode, _stdout, stderr, github_output = _run_inline_validate_script(
        tmp_path, conclusion="success", pr_number=535, candidate_sha="a" * 40
    )
    assert returncode == 0  # the inline script itself exits 0; failure is reported via GITHUB_OUTPUT
    assert "candidate_sha" in stderr
    assert "result=failure" in github_output

    # Same disposition as the trusted module's own validation for the same forgery.
    module_errors = replay.validate_receipt_trusted_fields(receipt, pr_number=535, candidate_sha="a" * 40)
    assert any("candidate_sha" in e for e in module_errors)


def test_checkout_free_inline_validator_rejects_incomplete_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # No replay-receipt.json / digest-check.json written -- mirrors a
        # failed or cancelled upstream validate-replay run.
        returncode, _stdout, stderr, github_output = _run_inline_validate_script(
            work, conclusion="success", pr_number=535, candidate_sha="a" * 40
        )
        assert returncode == 0
        assert "did not produce complete evidence" in stderr
        assert "result=failure" in github_output


def test_checkout_free_inline_validator_rejects_a_non_success_workflow_conclusion(tmp_path):
    receipt, _root = _good_receipt(tmp_path)
    (tmp_path / "replay-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    (tmp_path / "digest-check.json").write_text(json.dumps({"errors": []}), encoding="utf-8")

    returncode, _stdout, stderr, github_output = _run_inline_validate_script(
        tmp_path, conclusion="failure", pr_number=535, candidate_sha="a" * 40
    )
    assert returncode == 0
    assert "did not produce complete evidence" in stderr
    assert "result=failure" in github_output


# --- S8707: raw argparse values never reach build_receipt or the receipt --
#
# pythonsecurity:S8707 stayed open after validating candidate_sha in place:
# the live trace showed args.pr flowing, unvalidated, straight into
# build_receipt and the persisted receipt. The fix builds a new,
# validated TrustedReplayIdentity from validated primitives at CLI ingress
# and threads *that* everywhere in place of args.pr / args.candidate_sha.
# These tests prove the normalization actually rejects out-of-domain input,
# that a valid identity still succeeds end to end, that the persisted
# receipt carries only the normalized values, and that _cmd_run/_cmd_validate
# no longer reference the raw argparse fields at all.


def test_run_cli_rejects_a_non_integer_pr(tmp_path):
    root = _candidate_root(tmp_path)
    run = subprocess.run(
        [
            sys.executable,
            str(Path(replay.__file__)),
            "run",
            "--workspace-root",
            str(tmp_path),
            "--candidate-root",
            str(root),
            "--pr",
            "not-a-number",
            "--candidate-sha",
            "a" * 40,
            "--out",
            "receipt.json",
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 2  # argparse's own usage error for a malformed --pr
    assert "invalid int value" in run.stderr


@pytest.mark.parametrize("bad_pr", [0, -5, -1])
def test_run_cli_rejects_a_zero_or_negative_pr(tmp_path, bad_pr):
    root = _candidate_root(tmp_path)
    run = subprocess.run(
        [
            sys.executable,
            str(Path(replay.__file__)),
            "run",
            "--workspace-root",
            str(tmp_path),
            "--candidate-root",
            str(root),
            "--pr",
            f"{bad_pr}",
            "--candidate-sha",
            "a" * 40,
            "--out",
            "receipt.json",
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode != 0
    assert "must be between" in run.stderr


def test_run_cli_rejects_an_oversized_pr(tmp_path):
    root = _candidate_root(tmp_path)
    run = subprocess.run(
        [
            sys.executable,
            str(Path(replay.__file__)),
            "run",
            "--workspace-root",
            str(tmp_path),
            "--candidate-root",
            str(root),
            "--pr",
            "99999999999",
            "--candidate-sha",
            "a" * 40,
            "--out",
            "receipt.json",
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode != 0
    assert "must be between" in run.stderr


def test_parse_trusted_replay_identity_accepts_a_valid_pr_and_sha():
    identity = replay.parse_trusted_replay_identity(raw_pr=535, raw_candidate_sha="a" * 40)
    assert identity.pr_number == 535
    assert identity.candidate_sha == "a" * 40


@pytest.mark.parametrize("bad_pr", [0, -1, 10_000_001])
def test_parse_trusted_replay_identity_rejects_out_of_domain_pr(bad_pr):
    with pytest.raises(ValueError, match="must be between"):
        replay.parse_trusted_replay_identity(raw_pr=bad_pr, raw_candidate_sha="a" * 40)


def test_run_cli_persists_only_the_normalized_identity_values(tmp_path):
    root = _candidate_root(tmp_path)
    out_path = tmp_path / "receipt.json"

    run = subprocess.run(
        [
            sys.executable,
            str(Path(replay.__file__)),
            "run",
            "--workspace-root",
            str(tmp_path),
            "--candidate-root",
            root.name,
            "--pr",
            "535",
            "--candidate-sha",
            "A" * 40,  # uppercase hex, valid but not yet "normalized" casing
            "--out",
            out_path.name,
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    receipt = json.loads(out_path.read_text())
    assert receipt["candidate_pr"] == 535
    assert isinstance(receipt["candidate_pr"], int)
    assert receipt["candidate_sha"] == "A" * 40
    assert isinstance(receipt["candidate_sha"], str)


def test_cmd_run_and_cmd_validate_never_forward_raw_argparse_pr_or_sha():
    import inspect

    run_source = inspect.getsource(replay._cmd_run)
    validate_source = inspect.getsource(replay._cmd_validate)
    for source, name in ((run_source, "_cmd_run"), (validate_source, "_cmd_validate")):
        assert "parse_trusted_replay_identity" in source, f"{name} does not construct a normalized identity"
        # `args.pr`/`args.candidate_sha` may appear exactly once each -- as
        # the raw_pr=/raw_candidate_sha= inputs to parse_trusted_replay_identity
        # itself -- and never again downstream (build_receipt,
        # validate_receipt_trusted_fields, or the persisted receipt must
        # read only the normalized `identity` object).
        assert source.count("args.pr") == 1, f"{name} references args.pr somewhere other than identity construction"
        assert (
            source.count("args.candidate_sha") == 1
        ), f"{name} references args.candidate_sha somewhere other than identity construction"
