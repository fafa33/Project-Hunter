import json
from pathlib import Path

import hunter_pre_ready_review as review
import hunter_reviewer_benchmark as benchmark

ROOT = Path(__file__).resolve().parents[1]


def test_local_reviewer_cannot_gain_authority_without_benchmark_evidence():
    pool, error = review.load_reviewer_pool()
    assert not error
    local = next(a for a in pool["agents"] if a["id"] == "local-ollama")
    assert local["quality_gate"]["required"] is True
    assert local["quality_gate"]["benchmark_id"]


def test_score_measures_recall_and_false_positive_rate():
    cases = [{"id": "bug", "expected": "finding"}, {"id": "clean", "expected": "clear"}]
    recall, fp = benchmark.score(cases, {"bug": {"found": True}, "clean": {"found": False}})
    assert recall == 1.0
    assert fp == 0.0


def test_benchmark_fixture_covers_recorded_failure_classes():
    data = json.loads((ROOT / "tests/fixtures/reviewer_benchmark_cases.json").read_text())
    ids = {case["id"] for case in data}
    assert {"stale-head", "missing-authority", "retry-exhaustion", "workflow-permission", "clean-control"} <= ids


def test_failed_quality_threshold_keeps_local_reviewer_triage_only():
    pool, error = review.load_reviewer_pool()
    assert not error
    local = next(a for a in pool["agents"] if a["id"] == "local-ollama")
    gate = local["quality_gate"]
    result = gate["last_benchmark"]
    passed = (
        result["recall"] >= gate["minimum_recall"]
        and result["false_positive_rate"] <= gate["maximum_false_positive_rate"]
    )
    assert passed is False
    assert result["passed"] is False
    assert gate["authority_until_pass"] == "triage-only"
    assert local["authority_eligible"] is False
    assert local not in review.authority_pool_reviewers(pool)


#: Sentinel meaning "delete this key" in `_policy_with_local_agent`.
benchmark_removed = object()


def _policy_with_local_agent(**agent_overrides):
    """The canonical policy with the triage reviewer's declaration mutated."""

    policy = json.loads((ROOT / "docs/CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    pool = policy["review_progression"]["review_authority"]["reviewer_pool"]
    for agent in pool["agents"]:
        if agent["id"] == "local-ollama":
            for key, value in agent_overrides.items():
                if value is benchmark_removed:
                    agent.pop(key, None)
                else:
                    agent[key] = value
    return policy


def test_a_benchmark_failing_reviewer_cannot_declare_itself_authority_eligible():
    """The eligibility flag is bound to the recorded benchmark in trusted parsing.

    Without this, flipping the flag -- or deleting it, since an absent flag
    defaults to eligible -- would promote a triage-only reviewer to full review
    authority with nothing in the shared parser to contradict it.
    """

    for override in ({"authority_eligible": True}, {"authority_eligible": benchmark_removed}):
        pool, error = review.load_reviewer_pool(_policy_with_local_agent(**override))
        assert pool is None
        assert "quality gate" in error and "triage-only" in error


def test_a_passing_benchmark_permits_authority_eligibility():
    policy = _policy_with_local_agent(
        authority_eligible=True,
        quality_gate={
            "required": True,
            "benchmark_id": "hunter-local-reviewer-v1",
            "minimum_recall": 0.75,
            "maximum_false_positive_rate": 0.25,
            "authority_until_pass": "triage-only",
            "last_benchmark": {
                "model": "qwen2.5-coder:7b",
                "recall": 0.9,
                "false_positive_rate": 0.1,
                "latency_seconds": 12.0,
                "passed": True,
            },
        },
    )
    local = next(agent for agent in policy["review_progression"]["review_authority"]["reviewer_pool"]["agents"] if agent["id"] == "local-ollama")
    local["enabled"] = True
    local["priority"] = 1
    for agent in policy["review_progression"]["review_authority"]["reviewer_pool"]["agents"]:
        if agent["id"] != "local-ollama":
            agent["priority"] += 1
    pool, error = review.load_reviewer_pool(policy)
    assert pool is not None, error
    assert "local-ollama" in {agent["id"] for agent in review.authority_pool_reviewers(pool)}


def test_a_passed_flag_cannot_override_the_declared_thresholds():
    """A recorded ``passed: true`` that its own measurements contradict is not a pass."""

    policy = _policy_with_local_agent(
        authority_eligible=True,
        quality_gate={
            "required": True,
            "benchmark_id": "hunter-local-reviewer-v1",
            "minimum_recall": 0.75,
            "maximum_false_positive_rate": 0.25,
            "authority_until_pass": "triage-only",
            "last_benchmark": {"recall": 0.2, "false_positive_rate": 0.9, "passed": True},
        },
    )
    pool, error = review.load_reviewer_pool(policy)
    assert pool is None
    assert "quality gate" in error


def test_a_required_gate_without_a_recorded_benchmark_fails_closed():
    policy = _policy_with_local_agent(
        authority_eligible=True,
        quality_gate={
            "required": True,
            "benchmark_id": "hunter-local-reviewer-v1",
            "minimum_recall": 0.75,
            "maximum_false_positive_rate": 0.25,
        },
    )
    pool, error = review.load_reviewer_pool(policy)
    assert pool is None
    assert "last_benchmark" in error
