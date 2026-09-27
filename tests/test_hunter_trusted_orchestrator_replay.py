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
from pathlib import Path

import hunter_trusted_orchestrator_replay as replay
import pytest

FIXED_PRE_READY = """
import json
from pathlib import Path


def load_reviewer_pool(source=None):
    path = Path(__file__).resolve().parents[1] / "docs" / "CODE_WRITE_POLICY.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["pool"], ""


def enabled_pool_reviewers(pool):
    return tuple(a for a in pool["agents"] if a.get("enabled"))


def reviewer_chain_worst_case_seconds(pool):
    total = 0
    for agent in enabled_pool_reviewers(pool):
        attempts = 1 + (pool["timeout_policy"]["retries_per_agent"] if agent["retryable"] else 0)
        total += attempts * int(agent["review_timeout_seconds"])
    return total + 120
"""

FIXED_ORCHESTRATOR_HEADER = """
from dataclasses import dataclass

import hunter_pre_ready_review as pre_ready


@dataclass(frozen=True)
class ReviewCycle:
    pr_number: int
    head_sha: str
    state: str
    provider_id: str
    trigger_id: int | None
    started_at: str
    config_digest: str
    generation_id: str = ""
"""

SCENARIO_A_FIXED = """

def independent_review_opportunity_seconds():
    pool, error = pre_ready.load_reviewer_pool()
    if pool is None or error:
        raise RuntimeError(error)
    return pre_ready.reviewer_chain_worst_case_seconds(pool)
"""

SCENARIO_A_BUGGY = """

def independent_review_opportunity_seconds():
    # Still the pre-fix hardcoded constant: ignores the reviewer pool entirely.
    return 900
"""

SCENARIO_B_FIXED = """

def ensure_collector(repository, token, pr_number, head_sha, generation_id=""):
    digest = reviewer_pool_config_digest()
    state, existing, _error = read_cycle(repository, token, pr_number, head_sha)
    if (
        state == "present"
        and existing is not None
        and existing.config_digest == digest
        and existing.generation_id == generation_id
    ):
        return existing
    # Fixed: check collector liveness before dispatching, even when the status
    # read still reports the cycle as absent/stale.
    liveness, _count = collector_liveness(repository, token, pr_number, head_sha, generation_id)
    if liveness in {"active", "completed"}:
        return ReviewCycle(pr_number, head_sha, "WAITING_FOR_REVIEWER", "", None, "", digest, generation_id)
    run_id = current_run_id()
    cycle = ReviewCycle(pr_number, head_sha, "WAITING_FOR_REVIEWER", "", run_id, "now", digest, generation_id)
    publish_cycle(repository, token, head_sha, cycle=cycle)
    dispatch_collector(repository, token, pr_number, head_sha, generation_id)
    return cycle
"""

SCENARIO_B_BUGGY = """

def ensure_collector(repository, token, pr_number, head_sha, generation_id=""):
    digest = reviewer_pool_config_digest()
    state, existing, _error = read_cycle(repository, token, pr_number, head_sha)
    if (
        state == "present"
        and existing is not None
        and existing.config_digest == digest
        and existing.generation_id == generation_id
    ):
        return existing
    # Buggy: dispatches unconditionally whenever the read is not "present",
    # with no liveness check -- duplicates under a stale read.
    run_id = current_run_id()
    cycle = ReviewCycle(pr_number, head_sha, "WAITING_FOR_REVIEWER", "", run_id, "now", digest, generation_id)
    publish_cycle(repository, token, head_sha, cycle=cycle)
    dispatch_collector(repository, token, pr_number, head_sha, generation_id)
    return cycle
"""

FIXED_GOVERNANCE = """
import hunter_review_orchestrator as orchestration


def review_orchestration_state(repository, token, pr_number, head_sha):
    state, cycle, error = orchestration.read_cycle(repository, token, pr_number, head_sha)
    if state == "present" and cycle is not None:
        return cycle.state, f"provider={cycle.provider_id or 'unassigned'}"
    if state == "absent":
        return "WAITING_FOR_REVIEWER", "no trusted exact-head orchestration cycle has been published"
    raise RuntimeError(error or f"invalid review orchestration state: {state}")


def pending_review_authority_state(repository, token, pr_number, head_sha):
    cycle_state, detail = review_orchestration_state(repository, token, pr_number, head_sha)
    if cycle_state in {"REVIEW_IN_PROGRESS", "FAILOVER_IN_PROGRESS", "WAITING_FOR_REVIEWER"}:
        return "pending", f"{cycle_state}: {detail}"
    return "failure", f"MISSING_REVIEW_AUTHORITY: {cycle_state}: {detail}"
"""

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

POLICY_JSON = {
    "pool": {
        "timeout_policy": {"retries_per_agent": 1},
        "agents": [
            {"id": "codex", "enabled": True, "retryable": True, "review_timeout_seconds": 1800, "priority": 1},
            {"id": "copilot", "enabled": True, "retryable": False, "review_timeout_seconds": 300, "priority": 2},
            {"id": "unused", "enabled": False, "retryable": True, "review_timeout_seconds": 300, "priority": 3},
        ],
    }
}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _candidate_root(
    tmp_path: Path,
    *,
    orchestrator_src: str,
    governance_src: str = FIXED_GOVERNANCE,
    pre_ready_src: str = FIXED_PRE_READY,
) -> Path:
    root = tmp_path / "candidate"
    _write(root / "scripts" / "hunter_pre_ready_review.py", pre_ready_src)
    _write(root / "scripts" / "hunter_review_orchestrator.py", FIXED_ORCHESTRATOR_HEADER + orchestrator_src)
    _write(root / "scripts" / "hunter_governance_review_v2.py", governance_src)
    _write(root / "docs" / "CODE_WRITE_POLICY.json", json.dumps(POLICY_JSON))
    return root


def _fixture_path(tmp_path: Path) -> Path:
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(replay.FIXTURE), encoding="utf-8")
    return path


# --- Scenario A: reviewer opportunity timing -------------------------------


def test_scenario_a_passes_against_a_pool_derived_opportunity(tmp_path):
    root = _candidate_root(tmp_path, orchestrator_src=SCENARIO_A_FIXED)
    result = replay._run_scenario("A", root, _fixture_path(tmp_path))
    assert result["outcome"] == "pass", result.get("error")
    assert result["measurements"]["baseline_seconds"] == result["measurements"]["worst_case_seconds"]
    assert result["measurements"]["mutated_seconds"] > result["measurements"]["baseline_seconds"]


def test_scenario_a_fails_against_a_hardcoded_opportunity(tmp_path):
    root = _candidate_root(tmp_path, orchestrator_src=SCENARIO_A_BUGGY)
    result = replay._run_scenario("A", root, _fixture_path(tmp_path))
    assert result["outcome"] == "fail"
    assert "must equal the candidate's own trusted worst-case-budget derivation" in result["error"]


# --- Scenario B: exact-head collector idempotency --------------------------


def test_scenario_b_passes_against_a_liveness_checked_dispatch(tmp_path):
    root = _candidate_root(tmp_path, orchestrator_src=SCENARIO_B_FIXED)
    result = replay._run_scenario("B", root, _fixture_path(tmp_path))
    assert result["outcome"] == "pass", result.get("error")
    assert result["measurements"]["dispatches"] == 1


def test_scenario_b_fails_against_an_unconditional_dispatch(tmp_path):
    root = _candidate_root(tmp_path, orchestrator_src=SCENARIO_B_BUGGY)
    result = replay._run_scenario("B", root, _fixture_path(tmp_path))
    assert result["outcome"] == "fail"
    assert "exactly one dispatch" in result["error"]


# --- Scenario G: WAITING_FOR_REVIEWER -> pending classification ------------


def test_scenario_g_passes_against_correct_end_to_end_classification(tmp_path):
    root = _candidate_root(tmp_path, orchestrator_src=SCENARIO_B_FIXED, governance_src=FIXED_GOVERNANCE)
    result = replay._run_scenario("G", root, _fixture_path(tmp_path))
    assert result["outcome"] == "pass", result.get("error")


def test_scenario_g_fails_against_a_swallowed_fail_closed_path(tmp_path):
    root = _candidate_root(tmp_path, orchestrator_src=SCENARIO_B_FIXED, governance_src=BUGGY_GOVERNANCE)
    result = replay._run_scenario("G", root, _fixture_path(tmp_path))
    assert result["outcome"] == "fail"
    assert "fail closed" in result["error"]


# --- Trusted digests are pure and deterministic ----------------------------


def test_trusted_digests_are_deterministic():
    assert replay.trusted_harness_definition_digest() == replay.trusted_harness_definition_digest()
    assert replay.scenario_set_digest() == replay.scenario_set_digest()
    assert replay.fixture_digest() == replay.fixture_digest()


def _good_receipt(tmp_path: Path) -> tuple[dict, Path]:
    root = _candidate_root(tmp_path, orchestrator_src=SCENARIO_A_FIXED + SCENARIO_B_FIXED)
    receipt = replay.build_receipt(candidate_root=root, pr_number=535, candidate_sha="a" * 40)
    return receipt, root


def test_build_receipt_round_trips_through_validate(tmp_path):
    receipt, root = _good_receipt(tmp_path)
    assert receipt["overall_result"] == "pass"
    errors = replay.validate_receipt(receipt, candidate_root=root, pr_number=535, candidate_sha="a" * 40)
    assert errors == []


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
    root = _candidate_root(tmp_path, orchestrator_src=SCENARIO_B_FIXED)
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
